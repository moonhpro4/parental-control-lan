"""
ParentalAgent - headless Windows agent.

Manual launch (admin, via UAC manifest)  -> installs itself + scheduled task, shows PIN, exits.
Launch with --service (Task Scheduler)   -> runs WebSocket server on port 354 + process monitor.
"""
import asyncio
import ctypes
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psutil
import websockets
import winreg

APP = "ParentalAgent"
PORT = 354
DISCOVERY_PORT = 355
DISCOVERY_PING = b"PARENTALAGENT_DISCOVER"
DISCOVERY_PONG = b"PARENTALAGENT_HERE"
RELAY_HOST = "127.0.0.1"
RELAY_PORT = 353
RELAY_MSG_FRAME = 1
RELAY_MSG_CONTROL = 2
SCREEN_HELPER_EXE = "RainbowAgent.exe"
DENY_EXE = "ParentalDeny.exe"
RELOCK_TASK = "ParentalAgentRelock"
HOSTS_FILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
HOSTS_MARK_BEGIN = "# BEGIN PARENTALAGENT\n"
HOSTS_MARK_END = "# END PARENTALAGENT\n"
CREATE_NO_WINDOW = 0x08000000

DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP
INSTALL_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP
INSTALL_EXE = INSTALL_DIR / f"{APP}.exe"
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "agent.log"
WIN_DIR = os.environ.get("SystemRoot", r"C:\Windows").lower()
UNINSTALL_CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "wiz.app.uninstall.cmd"
PIN_CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "wiz.app.pin.cmd"
DEVICES_CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "wiz.devices.cmd"
DEVICES_FILE = DATA_DIR / "devices.json"

# Never terminated, even if someone tries to block them (would brick the session).
PROTECTED = {
    "explorer.exe", "winlogon.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
    "svchost.exe", "smss.exe", "dwm.exe", "system", "registry", "taskhostw.exe", "fontdrvhost.exe",
    "sihost.exe", "ctfmon.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "logonui.exe", "rundll32.exe", "conhost.exe", "parentalagent.exe",
    "cmd.exe", "python.exe", "pythonw.exe", "parentaloverlay.exe", "rainbowagent.exe",
    "parentaldeny.exe",
}
SKIP_NAMES = ("unins", "uninst", "setup", "update", "crash", "helper", "installer", "redist",
              "vc_redist", "elevat", "notificationhelper", "repair")

log = logging.getLogger(APP)


# ----------------------------------------------------------------------------- helpers
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, **kw)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def msgbox(text, title=APP, icon=0x40):
    ctypes.windll.user32.MessageBoxW(0, text, title, icon)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "unknown"
    finally:
        s.close()


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), "utf-8")
    os.replace(tmp, CONFIG_FILE)


# ----------------------------------------------------------------------------- install
TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{exe}</Command><Arguments>--service</Arguments></Exec>
  </Actions>
</Task>
"""


RELOCK_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{exe}</Command><Arguments>--relock</Arguments></Exec>
  </Actions>
</Task>
"""

def kill_other_instances():
    me = {os.getpid(), os.getppid()}  # onefile bootloader parent shares our image name
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() in (f"{APP}.exe".lower(), "parentaloverlay.exe") and p.pid not in me:
                p.kill()
        except Exception:
            pass


def install():
    if not getattr(sys, "frozen", False):
        print("Run with --service for development, or build the exe with PyInstaller.")
        return
    if not is_admin():
        msgbox("Administrator rights are required to install. Please run again and accept the UAC prompt.",
               icon=0x10)
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    if not cfg.get("pin"):
        cfg["pin"] = f"{secrets.randbelow(10**6):06d}"
    cfg.setdefault("blocked", [])
    save_config(cfg)
    # Only SYSTEM and Administrators may read the PIN / blocklist.
    run(["icacls", str(DATA_DIR), "/inheritance:r", "/grant:r",
         "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])

    run(["schtasks", "/end", "/tn", APP])
    kill_other_instances()
    time.sleep(1)
    if Path(sys.executable).resolve() != INSTALL_EXE.resolve():
        shutil.copy2(sys.executable, INSTALL_EXE)
    overlay_src = Path(getattr(sys, "_MEIPASS", ".")) / "ParentalOverlay.exe"
    if overlay_src.exists():
        shutil.copy2(overlay_src, INSTALL_DIR / "ParentalOverlay.exe")
    helper_src = Path(getattr(sys, "_MEIPASS", ".")) / SCREEN_HELPER_EXE
    if helper_src.exists():
        shutil.copy2(helper_src, INSTALL_DIR / SCREEN_HELPER_EXE)
    deny_src = Path(getattr(sys, "_MEIPASS", ".")) / DENY_EXE
    if deny_src.exists():
        shutil.copy2(deny_src, INSTALL_DIR / DENY_EXE)

    # `wiz.app.uninstall` / `wiz.app.pin` / `wiz.devices` typed in cmd or Win+R: each elevates (UAC)
    # and runs the matching agent action.
    def write_launcher(cmd_file, arg):
        cmd_file.write_text(
            "@echo off\r\n"
            f'powershell -NoProfile -WindowStyle Hidden -Command "Start-Process \'{INSTALL_EXE}\' '
            f'-ArgumentList \'{arg}\' -Verb RunAs"\r\n', "ascii")

    write_launcher(UNINSTALL_CMD, "wiz.app.uninstall")
    write_launcher(PIN_CMD, "wiz.app.pin")
    write_launcher(DEVICES_CMD, "wiz.devices")

    # Scheduled task: boot trigger, SYSTEM, highest privileges (== /ru SYSTEM /rl highest),
    # with the default 72h execution limit disabled and auto-restart on failure.
    xml_path = Path(tempfile.gettempdir()) / "parental_task.xml"
    xml_path.write_text(TASK_XML.format(exe=str(INSTALL_EXE)), encoding="utf-16")
    r = run(["schtasks", "/create", "/tn", APP, "/xml", str(xml_path), "/f"])
    xml_path.unlink(missing_ok=True)
    if r.returncode != 0:
        msgbox(f"Could not create scheduled task:\n{r.stderr or r.stdout}", icon=0x10)
        return

    # Second task: fires the instant a user logs on (even right after a restart) and re-shows
    # the lock screen if the device was left locked, instead of waiting for the service's own
    # polling loop to notice. Closes the "restart to bypass the lock" window.
    relock_xml_path = Path(tempfile.gettempdir()) / "parental_relock_task.xml"
    relock_xml_path.write_text(RELOCK_TASK_XML.format(exe=str(INSTALL_EXE)), encoding="utf-16")
    run(["schtasks", "/create", "/tn", RELOCK_TASK, "/xml", str(relock_xml_path), "/f"])
    relock_xml_path.unlink(missing_ok=True)

    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={APP}"])
    run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={APP}", "dir=in", "action=allow",
         "protocol=TCP", f"localport={PORT}", "profile=any"])
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={APP}Discovery"])
    run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={APP}Discovery", "dir=in", "action=allow",
         "protocol=UDP", f"localport={DISCOVERY_PORT}", "profile=any"])

    run(["schtasks", "/run", "/tn", APP])
    msgbox(f"{APP} is installed and running.\n\n"
           f"PC IP address:  {local_ip()}\n"
           f"Pairing PIN:    {cfg['pin']}\n\n"
           f"Enter these in the Android app. It starts automatically at every boot.\n\n"
           f"Note: the phone and this PC must be on the same Wi-Fi network to pair. "
           f"After pairing once, you'll be able to use the app from anywhere.")


CLEANUP_TASK = "ParentalAgentCleanup"


def uninstall():
    """Remove task, firewall rule, files. Files are deleted by a one-shot SYSTEM task after we exit."""
    kill_overlays()
    kill_screen_helper()
    run(["schtasks", "/end", "/tn", APP])
    run(["schtasks", "/delete", "/tn", APP, "/f"])
    run(["schtasks", "/delete", "/tn", RELOCK_TASK, "/f"])
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={APP}"])
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={APP}Discovery"])
    write_hosts_block(set())  # strip any blocked-site entries we added
    bat = Path(WIN_DIR) / "Temp" / "pa_cleanup.bat"
    bat.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f"taskkill /f /im {APP}.exe >nul 2>&1\r\n"
        "taskkill /f /im ParentalOverlay.exe >nul 2>&1\r\n"
        f"taskkill /f /im {SCREEN_HELPER_EXE} >nul 2>&1\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'rmdir /s /q "{INSTALL_DIR}"\r\n'
        f'rmdir /s /q "{DATA_DIR}"\r\n'
        f'del "{UNINSTALL_CMD}"\r\n'
        f'del "{PIN_CMD}"\r\n'
        f'del "{DEVICES_CMD}"\r\n'
        f"schtasks /delete /tn {CLEANUP_TASK} /f\r\n"
        'del "%~f0"\r\n', "ascii")
    run(["schtasks", "/create", "/tn", CLEANUP_TASK, "/tr", str(bat), "/sc", "once", "/st", "00:00",
         "/ru", "SYSTEM", "/rl", "highest", "/f"])
    run(["schtasks", "/run", "/tn", CLEANUP_TASK])


def uninstall_cli():
    """`ParentalAgent.exe wiz.app.uninstall` - silent forced uninstall (needs admin; manifest prompts for it)."""
    if not is_admin():
        return 1
    uninstall()
    return 0


def do_relock():
    """`ParentalAgent.exe --relock` - run by the logon-trigger task right at sign-in; re-shows the
    lock screen immediately if the device was left locked, instead of waiting for the service's
    polling loop to notice after the desktop is already usable."""
    cfg = load_config()
    if cfg.get("locked"):
        launch_overlay(cfg.get("lang", "en"))
    return 0


def show_pin():
    """`wiz.app.pin` - redisplay the pairing PIN shown at install (doesn't change it)."""
    if not is_admin():
        return 1
    cfg = load_config()
    pin = cfg.get("pin")
    if not pin:
        msgbox(f"{APP} is not installed yet. Run ParentalAgent.exe as administrator first.", icon=0x10)
        return 1
    msgbox(f"PC IP address:  {local_ip()}\nPairing PIN:    {pin}\n\n"
           f"Enter these in the Android app. Both devices must be on the same Wi-Fi to pair.")
    return 0


def show_devices():
    """`wiz.devices` - how many phones are connected to this agent right now."""
    if not is_admin():
        return 1
    try:
        devices = json.loads(DEVICES_FILE.read_text("utf-8"))
    except Exception:
        devices = {}
    if not devices:
        msgbox("No devices are currently connected.\n\n"
               "(This shows devices connected right now, not a history of every device ever paired.)")
        return 0
    lines = "\n".join(f"  {ip}  -  connected {_ago(ts)}" for ip, ts in sorted(devices.items()))
    msgbox(f"{len(devices)} device(s) currently connected:\n\n{lines}\n\n"
           f"(Shows devices connected right now, not a history of every device ever paired.)")
    return 0


def _ago(ts):
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


# ----------------------------------------------------------------------------- service
class State:
    def __init__(self):
        cfg = load_config()
        if not cfg.get("pin"):
            cfg["pin"] = f"{secrets.randbelow(10**6):06d}"
            save_config(cfg)
        self.pin = cfg["pin"]
        self.blocked = {b.lower() for b in cfg.get("blocked", [])}
        self.blocked_sites = {s.lower() for s in cfg.get("blocked_sites", [])}
        self.locked = bool(cfg.get("locked", False))
        self.lang = cfg.get("lang", "en")
        self.lock = threading.Lock()
        self.devices = {}  # ip -> connected_at timestamp (authenticated clients only)
        # Screen-viewing state. Only ever touched from the asyncio event loop (not the monitor
        # thread), so it deliberately does not use state.lock.
        self.streaming_clients = set()   # websockets currently viewing the screen
        self.helper_writer = None        # asyncio StreamWriter to the connected RainbowAgent helper

    def persist(self):
        cfg = load_config()
        cfg["pin"] = self.pin
        cfg["blocked"] = sorted(self.blocked)
        cfg["blocked_sites"] = sorted(self.blocked_sites)
        cfg["locked"] = self.locked
        cfg["lang"] = self.lang
        save_config(cfg)


DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def clean_domain(raw):
    """Strip scheme/path/port, lowercase, and reject anything that isn't a plain domain - this
    goes straight into the hosts file, so it must never be able to contain a newline or anything
    else that could break out of our block or inject an unrelated hosts entry."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/")[0].split(":")[0].split("?")[0]
    d = d[:253]
    if not DOMAIN_RE.match(d):
        return None
    return d


def write_hosts_block(sites):
    """Rewrites the ParentalAgent-managed section of the hosts file to exactly match `sites`,
    leaving every other line in the file untouched, then flushes the DNS cache."""
    try:
        try:
            lines = HOSTS_FILE.read_text("utf-8", errors="ignore").splitlines(keepends=True)
        except FileNotFoundError:
            lines = []
        out = []
        skipping = False
        for line in lines:
            if line == HOSTS_MARK_BEGIN:
                skipping = True
                continue
            if line == HOSTS_MARK_END:
                skipping = False
                continue
            if not skipping:
                out.append(line)
        if sites:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(HOSTS_MARK_BEGIN)
            for site in sorted(sites):
                out.append(f"0.0.0.0 {site}\n")
                if not site.startswith("www."):
                    out.append(f"0.0.0.0 www.{site}\n")
            out.append(HOSTS_MARK_END)
        tmp = HOSTS_FILE.with_suffix(".parentalagent_tmp")
        tmp.write_text("".join(out), "utf-8")
        os.replace(tmp, HOSTS_FILE)
        run(["ipconfig", "/flushdns"])
    except Exception:
        log.exception("write_hosts_block failed")



def enable_privileges():
    """SYSTEM's token carries SeTcbPrivilege/SeAssignPrimaryTokenPrivilege/SeIncreaseQuotaPrivilege
    but they're disabled by default; CreateProcessAsUser/WTSQueryUserToken need them enabled. Doing
    this once at startup makes the session-crossing calls (lock overlay, deny message, screen
    helper) reliable immediately after boot instead of only after the first successful call."""
    try:
        import win32security
        import win32con
        import win32api
        names = ["SeTcbPrivilege", "SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege",
                 "SeDebugPrivilege"]
        htoken = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
        privs = []
        for name in names:
            try:
                luid = win32security.LookupPrivilegeValue(None, name)
                privs.append((luid, win32con.SE_PRIVILEGE_ENABLED))
            except Exception:
                pass
        win32security.AdjustTokenPrivileges(htoken, False, privs)
    except Exception:
        log.exception("enable_privileges failed (non-fatal)")


def launch_deny_message(lang):
    exe = INSTALL_DIR / DENY_EXE
    if not exe.exists():
        return
    lang = re.sub(r"[^a-z]", "", (lang or "en").lower())[:5] or "en"
    try:
        launch_in_user_session(f'"{exe}" --lang {lang}', str(exe))
    except Exception:
        log.exception("deny message launch failed")


def monitor_loop(state):
    me = os.getpid()
    last_launch = 0.0
    last_deny = {}  # exe/jar family -> last time we showed "Access denied" for it, to avoid spam
    while True:
        try:
            with state.lock:
                blocked = set(state.blocked)
                locked = state.locked
                lang = state.lang
            if blocked or locked:
                overlay_up = False
                for p in psutil.process_iter(["name", "cmdline"]):
                    try:
                        n = (p.info["name"] or "").lower()
                        if locked and n == "taskmgr.exe":
                            p.kill()
                        elif n == "parentaloverlay.exe":
                            overlay_up = True
                        elif n not in PROTECTED and p.pid != me and should_kill(n, p.info.get("cmdline") or [], blocked):
                            p.kill()
                            log.info("killed %s (%s)", n, p.pid)
                            key = n if n not in ("java.exe", "javaw.exe") else "java:" + n
                            if time.time() - last_deny.get(key, 0) > 3:
                                last_deny[key] = time.time()
                                launch_deny_message(lang)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                if locked and not overlay_up and time.time() - last_launch > 0.5:
                    last_launch = time.time()
                    launch_overlay(lang)  # watchdog: bring the lock screen back if it was closed
        except Exception:
            log.exception("monitor error")
        time.sleep(0.2)  # ~5 scans per second


def launch_in_user_session(cmdline, exe):
    """SYSTEM lives in session 0; start a process on the logged-in user's desktop."""
    import win32con, win32process, win32profile, win32ts
    sid = win32ts.WTSGetActiveConsoleSessionId()
    if sid == 0xFFFFFFFF:
        return False
    token = win32ts.WTSQueryUserToken(sid)
    env = win32profile.CreateEnvironmentBlock(token, False)
    si = win32process.STARTUPINFO()
    si.lpDesktop = "winsta0\\default"
    win32process.CreateProcessAsUser(token, exe, cmdline, None, None, False,
                                     win32con.CREATE_NO_WINDOW | 0x400, env, str(INSTALL_DIR), si)
    return True


def launch_overlay(lang):
    exe = INSTALL_DIR / "ParentalOverlay.exe"
    if not exe.exists():
        return False
    lang = re.sub(r"[^a-z]", "", (lang or "en").lower())[:5] or "en"
    try:
        return launch_in_user_session(f'"{exe}" --lang {lang}', str(exe))
    except Exception:
        log.exception("overlay launch failed")
        return False


def kill_overlays():
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == "parentaloverlay.exe":
                p.kill()
        except Exception:
            pass


def kill_screen_helper():
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == SCREEN_HELPER_EXE.lower():
                p.kill()
        except Exception:
            pass


def launch_screen_helper(lang):
    exe = INSTALL_DIR / SCREEN_HELPER_EXE
    if not exe.exists():
        return False
    lang = re.sub(r"[^a-z]", "", (lang or "en").lower())[:5] or "en"
    try:
        return launch_in_user_session(f'"{exe}" --lang {lang}', str(exe))
    except Exception:
        log.exception("screen helper launch failed")
        return False


def set_lock(state, locked, lang=None):
    with state.lock:
        state.locked = locked
        if lang:
            state.lang = re.sub(r"[^a-zA-Z]", "", lang.split("-")[0])[:5].lower() or "en"
        state.persist()
        cur_lang = state.lang
    if locked:
        if not launch_overlay(cur_lang):
            return lock_workstation()  # fallback: normal Windows lock screen
    else:
        kill_overlays()
    return True


def lock_workstation():
    """SYSTEM runs in session 0, so LockWorkStation must be launched inside the user's session."""
    try:
        import win32con, win32process, win32profile, win32ts
        sid = win32ts.WTSGetActiveConsoleSessionId()
        if sid != 0xFFFFFFFF:
            token = win32ts.WTSQueryUserToken(sid)
            env = win32profile.CreateEnvironmentBlock(token, False)
            si = win32process.STARTUPINFO()
            si.lpDesktop = "winsta0\\default"
            rundll = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "rundll32.exe")
            win32process.CreateProcessAsUser(
                token, rundll, f'"{rundll}" user32.dll,LockWorkStation', None, None, False,
                win32con.CREATE_NO_WINDOW | 0x400, env, None, si)
            return True
    except Exception:
        log.exception("CreateProcessAsUser lock failed, trying fallbacks")
    try:
        if ctypes.windll.user32.LockWorkStation():
            return True
    except Exception:
        pass
    try:  # last resort: disconnect console session -> shows the sign-in screen
        sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
        return bool(ctypes.windll.wtsapi32.WTSDisconnectSession(0, sid, False))
    except Exception:
        return False


def _val(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _exes_in(folder, limit=6):
    """Top-level AND one subfolder deep (bootstrap-style launchers often nest in a bin/ subfolder),
    for both .exe and .jar (Java-based launchers that ship no native executable at all)."""
    out = []
    try:
        for f in os.scandir(folder):
            n = f.name.lower()
            if f.is_file() and (n.endswith(".exe") or n.endswith(".jar")) and not any(s in n for s in SKIP_NAMES):
                out.append(f.name)
            elif f.is_dir() and len(out) < limit:
                try:
                    for g in os.scandir(f.path):
                        gn = g.name.lower()
                        if g.is_file() and (gn.endswith(".exe") or gn.endswith(".jar")) and not any(s in gn for s in SKIP_NAMES):
                            out.append(g.name)
                except OSError:
                    pass
    except OSError:
        pass
    return out[:limit]


def jar_stem(filename):
    """'SKLauncher-3.2.18.jar' -> 'sklauncher' - a stable family identifier even after an
    auto-updating bootstrap launcher downloads and re-runs a differently-versioned jar."""
    base = filename.rsplit(".", 1)[0].lower()
    base = re.sub(r"[-_ ]?v?\d[\d.]*$", "", base)
    return base or filename.rsplit(".", 1)[0].lower()


def should_kill(name, cmdline, blocked):
    """True if this process matches something on the block list - by exact executable name, or
    (for Java-based apps, where the real process is java.exe/javaw.exe, not the app's own name) by
    the blocked jar's family name appearing on the command line."""
    if name in blocked:
        return True
    if name in ("java.exe", "javaw.exe") and cmdline:
        cmd = " ".join(cmdline).lower()
        for b in blocked:
            if b.endswith(".jar"):
                stem = jar_stem(b)
                if stem and stem in cmd:
                    return True
    return False


def installed_apps():
    found = {}

    def add(name, exe):
        exe = (exe or "").strip().lower()
        if not (exe.endswith(".exe") or exe.endswith(".jar")) or exe in PROTECTED or any(s in exe for s in SKIP_NAMES):
            return
        found.setdefault(exe, name or Path(exe).stem.title())

    unin = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            root = winreg.OpenKey(hive, unin, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        name = _val(k, "DisplayName")
                        if not name:
                            continue
                        icon = (_val(k, "DisplayIcon") or "").split(",")[0].strip().strip('"')
                        loc = _val(k, "InstallLocation") or ""
                        icon_lower = icon.lower()
                        if (icon_lower.endswith(".exe") or icon_lower.endswith(".jar")) and not icon_lower.startswith(WIN_DIR):
                            add(name, os.path.basename(icon))
                        elif loc and os.path.isdir(loc) and not loc.lower().startswith(WIN_DIR):
                            # Registry pointed at a folder (or its icon didn't resolve to a usable
                            # exe/jar directly): look inside - this is the case that was missing
                            # jar-only launchers like a bootstrap-style Minecraft launcher before.
                            for e in _exes_in(loc):
                                add(name, e)
                except OSError:
                    continue

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                n = winreg.EnumKey(root, i)
                add(Path(n).stem.title(), n)
    except OSError:
        pass

    for p in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            exe = p.info["exe"] or ""
            name = (p.info["name"] or "").lower()
            if exe and not exe.lower().startswith(WIN_DIR):
                add(None, p.info["name"])
            if name in ("java.exe", "javaw.exe"):
                # A running Java app has no useful exe name of its own - pull the .jar it's
                # actually running from the command line so it can be listed and blocked by name.
                for arg in p.info.get("cmdline") or []:
                    if arg.lower().endswith(".jar"):
                        add(None, os.path.basename(arg))
        except Exception:
            pass

    # Folder scan so that apps without registry entries (portable apps, launchers, per-user installs,
    # games like Roblox/Minecraft launchers which nest several folders deep) show up too.
    skip_dirs = {"windowsapps", "common files", "installer", "windows defender", "windows nt",
                 "microsoft.net", "reference assemblies", "windowspowershell", "temp", "cache", "crashpad",
                 "nvidia", "amd", "intel", "packages", "connecteddevicesplatform", "diagnostics"}
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    users = Path(os.environ.get("SystemDrive", "C:") + "\\Users")
    try:
        for u in users.iterdir():
            if u.name.lower() in ("public", "default", "default user", "all users"):
                continue
            roots.append(str(u / "AppData" / "Local" / "Programs"))
            roots.append(str(u / "AppData" / "Local"))
            roots.append(str(u / "AppData" / "Roaming"))
            roots.append(str(u / "Desktop"))
            roots.append(str(u / "Downloads"))
    except OSError:
        pass
    count = 0
    for root in filter(None, roots):
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\").count("\\")
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.count("\\") - base_depth
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs] if depth < 6 else []
            for fn in filenames:
                fnl = fn.lower()
                if fnl.endswith(".exe") or fnl.endswith(".jar"):
                    add(None, fn)
                    count += 1
            if count > 8000:
                break

    return sorted(({"name": v, "exe": k} for k, v in found.items()), key=lambda a: a["name"].lower())



def write_devices(state):
    try:
        with state.lock:
            data = dict(state.devices)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DEVICES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), "utf-8")
        os.replace(tmp, DEVICES_FILE)
    except Exception:
        log.exception("write_devices failed")

fails = {}  # ip -> (count, first_ts)


def is_locked_out(ip):
    c, t = fails.get(ip, (0, 0))
    if c >= 5 and time.time() - t < 60:
        return True
    if time.time() - t >= 300:
        fails.pop(ip, None)
    return False


def note_fail(ip):
    c, t = fails.get(ip, (0, time.time()))
    fails[ip] = (c + 1, t if c else time.time())


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data == DISCOVERY_PING:
            self.transport.sendto(DISCOVERY_PONG, addr)


async def run_discovery_responder():
    loop = asyncio.get_running_loop()
    while True:
        try:
            transport, _ = await loop.create_datagram_endpoint(
                DiscoveryProtocol, local_addr=("0.0.0.0", DISCOVERY_PORT),
                allow_broadcast=True)
            try:
                await asyncio.Future()
            finally:
                transport.close()
        except OSError:
            log.exception("discovery bind failed, retrying")
            await asyncio.sleep(3)



async def handle_relay_conn(reader, writer, state):
    state.helper_writer = writer
    try:
        while True:
            header = await reader.readexactly(5)
            length, msg_type = struct.unpack(">IB", header)
            payload = await reader.readexactly(length)
            if msg_type == RELAY_MSG_FRAME:
                dead = []
                for ws in state.streaming_clients:
                    try:
                        await ws.send(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    state.streaming_clients.discard(ws)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception:
        log.exception("relay connection error")
    finally:
        if state.helper_writer is writer:
            state.helper_writer = None
        writer.close()


async def send_control(state, obj):
    w = state.helper_writer
    if w is None:
        return False
    try:
        payload = json.dumps(obj).encode("utf-8")
        w.write(struct.pack(">IB", len(payload), RELAY_MSG_CONTROL) + payload)
        await w.drain()
        return True
    except Exception:
        return False


async def stop_streaming(state):
    state.streaming_clients.clear()
    state.helper_writer = None
    await asyncio.get_running_loop().run_in_executor(None, kill_screen_helper)


async def serve(state):
    clients = set()
    loop = asyncio.get_running_loop()

    async def reply(ws, obj):
        await ws.send(json.dumps(obj))

    def blocked_msg():
        with state.lock:
            return json.dumps({"type": "blocked", "blocked": sorted(state.blocked)})

    def lock_msg():
        return json.dumps({"type": "lock_state", "locked": state.locked})

    async def handler(ws):
        ip = ws.remote_address[0] if ws.remote_address else "?"
        if is_locked_out(ip):
            await ws.close(1008, "too many attempts")
            return
        nonce = secrets.token_hex(16)
        await reply(ws, {"type": "hello", "nonce": nonce})
        authed = False
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    cmd = msg.get("cmd")
                except Exception:
                    continue

                if not authed:
                    if cmd != "auth":
                        continue
                    expect = hmac.new(state.pin.encode(), nonce.encode(), hashlib.sha256).hexdigest()
                    if hmac.compare_digest(expect, str(msg.get("hmac", ""))):
                        authed = True
                        clients.add(ws)
                        fails.pop(ip, None)
                        with state.lock:
                            state.devices[ip] = time.time()
                        write_devices(state)
                        await reply(ws, {"type": "auth", "ok": True})
                        await ws.send(blocked_msg())
                        await ws.send(lock_msg())
                        with state.lock:
                            sites_now = sorted(state.blocked_sites)
                        await ws.send(json.dumps({"type": "sites", "sites": sites_now}))
                    else:
                        note_fail(ip)
                        await reply(ws, {"type": "auth", "ok": False})
                        await ws.close(1008, "bad pin")
                        return
                    continue

                if cmd == "ping":
                    await reply(ws, {"type": "pong"})
                elif cmd == "lock":
                    ok = await loop.run_in_executor(None, set_lock, state, True, msg.get("lang"))
                    await reply(ws, {"type": "lock", "ok": ok})
                    websockets.broadcast(clients, lock_msg())
                elif cmd == "unlock":
                    ok = await loop.run_in_executor(None, set_lock, state, False, None)
                    await reply(ws, {"type": "unlock", "ok": ok})
                    websockets.broadcast(clients, lock_msg())
                elif cmd == "list_apps":
                    apps = await loop.run_in_executor(None, installed_apps)
                    with state.lock:
                        known = {a["exe"] for a in apps}
                        for b in state.blocked:  # keep blocked-but-not-detected entries visible
                            if b not in known:
                                apps.append({"name": Path(b).stem.title(), "exe": b})
                        blocked = sorted(state.blocked)
                    apps.sort(key=lambda a: a["name"].lower())
                    await reply(ws, {"type": "apps", "apps": apps, "blocked": blocked})
                elif cmd == "screen_start":
                    state.streaming_clients.add(ws)
                    if state.helper_writer is None:
                        await loop.run_in_executor(None, launch_screen_helper, msg.get("lang"))
                    await reply(ws, {"type": "screen_start", "ok": True})
                elif cmd == "screen_stop":
                    state.streaming_clients.discard(ws)
                    if not state.streaming_clients:
                        await stop_streaming(state)
                    await reply(ws, {"type": "screen_stop", "ok": True})
                elif cmd in ("mouse_move", "mouse_down", "mouse_up", "mouse_delta", "scroll",
                             "key_text", "key_special"):
                    if ws in state.streaming_clients:
                        await send_control(state, msg)
                elif cmd == "uninstall":
                    await loop.run_in_executor(None, uninstall)
                    await reply(ws, {"type": "uninstall", "ok": True})
                    loop.call_later(0.5, os._exit, 0)
                elif cmd == "set_block":
                    exe = os.path.basename(str(msg.get("exe", ""))).strip().lower()
                    if not (exe.endswith(".exe") or exe.endswith(".jar")) or exe in PROTECTED:
                        await reply(ws, {"type": "error", "message": f"cannot change {exe}"})
                        continue
                    with state.lock:
                        if msg.get("blocked"):
                            if len(state.blocked) >= 500 and exe not in state.blocked:
                                await reply(ws, {"type": "error", "message": "500 app limit reached"})
                                continue
                            state.blocked.add(exe)
                        else:
                            state.blocked.discard(exe)
                        state.persist()
                    websockets.broadcast(clients, blocked_msg())
                elif cmd == "set_site_block":
                    site = clean_domain(msg.get("site", ""))
                    if not site:
                        await reply(ws, {"type": "error", "message": "invalid site"})
                        continue
                    with state.lock:
                        if msg.get("blocked"):
                            state.blocked_sites.add(site)
                        else:
                            state.blocked_sites.discard(site)
                        state.persist()
                        sites = sorted(state.blocked_sites)
                    await loop.run_in_executor(None, write_hosts_block, set(sites))
                    websockets.broadcast(clients, json.dumps({"type": "sites", "sites": sites}))
                elif cmd == "list_sites":
                    with state.lock:
                        sites = sorted(state.blocked_sites)
                    await reply(ws, {"type": "sites", "sites": sites})
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("client error")
        finally:
            clients.discard(ws)
            if ws in state.streaming_clients:
                state.streaming_clients.discard(ws)
                if not state.streaming_clients:
                    await stop_streaming(state)
            if authed:
                with state.lock:
                    state.devices.pop(ip, None)
                write_devices(state)

    asyncio.create_task(run_discovery_responder())
    await asyncio.start_server(lambda r, w: handle_relay_conn(r, w, state), RELAY_HOST, RELAY_PORT)
    while True:
        try:
            async with websockets.serve(handler, "0.0.0.0", PORT, ping_interval=10, ping_timeout=10,
                                        max_size=65536):
                log.info("listening on :%d", PORT)
                await asyncio.Future()
        except OSError:
            log.exception("bind failed, retrying")
            await asyncio.sleep(3)


def run_service():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    enable_privileges()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2)])
    state = State()
    state.devices = {}
    write_devices(state)
    write_hosts_block(set(state.blocked_sites))
    threading.Thread(target=monitor_loop, args=(state,), daemon=True).start()
    asyncio.run(serve(state))


if __name__ == "__main__":
    if "wiz.app.uninstall" in sys.argv or "--uninstall" in sys.argv:
        sys.exit(uninstall_cli())
    if "wiz.app.pin" in sys.argv:
        sys.exit(show_pin())
    if "wiz.devices" in sys.argv:
        sys.exit(show_devices())
    if "--relock" in sys.argv:
        sys.exit(do_relock())
    if "--service" in sys.argv:
        run_service()
    else:
        install()
