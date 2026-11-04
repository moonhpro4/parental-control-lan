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
CREATE_NO_WINDOW = 0x08000000

DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP
INSTALL_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP
INSTALL_EXE = INSTALL_DIR / f"{APP}.exe"
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "agent.log"
WIN_DIR = os.environ.get("SystemRoot", r"C:\Windows").lower()
UNINSTALL_CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "wiz.app.uninstall.cmd"

# Never terminated, even if someone tries to block them (would brick the session).
PROTECTED = {
    "explorer.exe", "winlogon.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
    "svchost.exe", "smss.exe", "dwm.exe", "system", "registry", "taskhostw.exe", "fontdrvhost.exe",
    "sihost.exe", "ctfmon.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "logonui.exe", "rundll32.exe", "conhost.exe", "parentalagent.exe",
    "cmd.exe", "python.exe", "pythonw.exe",
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


def kill_other_instances():
    me = {os.getpid(), os.getppid()}  # onefile bootloader parent shares our image name
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == f"{APP}.exe".lower() and p.pid not in me:
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

    # `wiz.app.uninstall` typed in cmd / Win+R: elevates (UAC) and runs the forced uninstall.
    UNINSTALL_CMD.write_text(
        "@echo off\r\n"
        f'powershell -NoProfile -WindowStyle Hidden -Command "Start-Process \'{INSTALL_EXE}\' '
        '-ArgumentList \'wiz.app.uninstall\' -Verb RunAs"\r\n', "ascii")

    # Scheduled task: boot trigger, SYSTEM, highest privileges (== /ru SYSTEM /rl highest),
    # with the default 72h execution limit disabled and auto-restart on failure.
    xml_path = Path(tempfile.gettempdir()) / "parental_task.xml"
    xml_path.write_text(TASK_XML.format(exe=str(INSTALL_EXE)), encoding="utf-16")
    r = run(["schtasks", "/create", "/tn", APP, "/xml", str(xml_path), "/f"])
    xml_path.unlink(missing_ok=True)
    if r.returncode != 0:
        msgbox(f"Could not create scheduled task:\n{r.stderr or r.stdout}", icon=0x10)
        return

    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={APP}"])
    run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={APP}", "dir=in", "action=allow",
         "protocol=TCP", f"localport={PORT}", "profile=any"])

    run(["schtasks", "/run", "/tn", APP])
    msgbox(f"{APP} is installed and running.\n\n"
           f"PC IP address:  {local_ip()}\n"
           f"Pairing PIN:    {cfg['pin']}\n\n"
           f"Enter these in the Android app. It starts automatically at every boot.")


CLEANUP_TASK = "ParentalAgentCleanup"


def uninstall():
    """Remove task, firewall rule, files. Files are deleted by a one-shot SYSTEM task after we exit."""
    run(["schtasks", "/end", "/tn", APP])
    run(["schtasks", "/delete", "/tn", APP, "/f"])
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={APP}"])
    bat = Path(WIN_DIR) / "Temp" / "pa_cleanup.bat"
    bat.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f"taskkill /f /im {APP}.exe >nul 2>&1\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'rmdir /s /q "{INSTALL_DIR}"\r\n'
        f'rmdir /s /q "{DATA_DIR}"\r\n'
        f'del "{UNINSTALL_CMD}"\r\n'
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


# ----------------------------------------------------------------------------- service
class State:
    def __init__(self):
        cfg = load_config()
        if not cfg.get("pin"):
            cfg["pin"] = f"{secrets.randbelow(10**6):06d}"
            save_config(cfg)
        self.pin = cfg["pin"]
        self.blocked = {b.lower() for b in cfg.get("blocked", [])}
        self.lock = threading.Lock()

    def persist(self):
        cfg = load_config()
        cfg["pin"] = self.pin
        cfg["blocked"] = sorted(self.blocked)
        save_config(cfg)


def monitor_loop(state):
    me = os.getpid()
    while True:
        try:
            with state.lock:
                blocked = set(state.blocked)
            if blocked:
                for p in psutil.process_iter(["name"]):
                    try:
                        n = (p.info["name"] or "").lower()
                        if n in blocked and n not in PROTECTED and p.pid != me:
                            p.kill()
                            log.info("killed %s (%s)", n, p.pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except Exception:
            log.exception("monitor error")
        time.sleep(0.2)  # ~5 scans per second


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


def _exes_in(folder, limit=4):
    out = []
    try:
        for f in os.scandir(folder):
            n = f.name.lower()
            if f.is_file() and n.endswith(".exe") and not any(s in n for s in SKIP_NAMES):
                out.append(f.name)
    except OSError:
        pass
    return out[:limit]


def installed_apps():
    found = {}

    def add(name, exe):
        exe = (exe or "").strip().lower()
        if not exe.endswith(".exe") or exe in PROTECTED or any(s in exe for s in SKIP_NAMES):
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
                        if icon.lower().endswith(".exe") and not icon.lower().startswith(WIN_DIR):
                            add(name, os.path.basename(icon))
                        elif loc and os.path.isdir(loc) and not loc.lower().startswith(WIN_DIR):
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

    for p in psutil.process_iter(["name", "exe"]):
        try:
            exe = p.info["exe"] or ""
            if exe and not exe.lower().startswith(WIN_DIR):
                add(None, p.info["name"])
        except Exception:
            pass

    # Folder scan so that apps without registry entries (portable apps, games, per-user installs) show up too.
    skip_dirs = {"windowsapps", "common files", "installer", "windows defender", "windows nt",
                 "microsoft.net", "reference assemblies", "windowspowershell", "temp", "cache", "crashpad"}
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    users = Path(os.environ.get("SystemDrive", "C:") + "\\Users")
    try:
        for u in users.iterdir():
            roots.append(str(u / "AppData" / "Local" / "Programs"))
            roots.append(str(u / "AppData" / "Local"))
    except OSError:
        pass
    count = 0
    for root in filter(None, roots):
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\").count("\\")
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.count("\\") - base_depth
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs] if depth < 3 else []
            for fn in filenames:
                if fn.lower().endswith(".exe"):
                    add(None, fn)
                    count += 1
            if count > 4000:
                break

    return sorted(({"name": v, "exe": k} for k, v in found.items()), key=lambda a: a["name"].lower())


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


async def serve(state):
    clients = set()
    loop = asyncio.get_running_loop()

    async def reply(ws, obj):
        await ws.send(json.dumps(obj))

    def blocked_msg():
        with state.lock:
            return json.dumps({"type": "blocked", "blocked": sorted(state.blocked)})

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
                        await reply(ws, {"type": "auth", "ok": True})
                        await ws.send(blocked_msg())
                    else:
                        note_fail(ip)
                        await reply(ws, {"type": "auth", "ok": False})
                        await ws.close(1008, "bad pin")
                        return
                    continue

                if cmd == "ping":
                    await reply(ws, {"type": "pong"})
                elif cmd == "lock":
                    ok = await loop.run_in_executor(None, lock_workstation)
                    await reply(ws, {"type": "lock", "ok": ok})
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
                elif cmd == "uninstall":
                    await loop.run_in_executor(None, uninstall)
                    await reply(ws, {"type": "uninstall", "ok": True})
                    loop.call_later(0.5, os._exit, 0)
                elif cmd == "set_block":
                    exe = os.path.basename(str(msg.get("exe", ""))).strip().lower()
                    if not exe.endswith(".exe") or exe in PROTECTED:
                        await reply(ws, {"type": "error", "message": f"cannot change {exe}"})
                        continue
                    with state.lock:
                        if msg.get("blocked"):
                            state.blocked.add(exe)
                        else:
                            state.blocked.discard(exe)
                        state.persist()
                    websockets.broadcast(clients, blocked_msg())
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("client error")
        finally:
            clients.discard(ws)

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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2)])
    state = State()
    threading.Thread(target=monitor_loop, args=(state,), daemon=True).start()
    asyncio.run(serve(state))


if __name__ == "__main__":
    if "wiz.app.uninstall" in sys.argv or "--uninstall" in sys.argv:
        sys.exit(uninstall_cli())
    if "--service" in sys.argv:
        run_service()
    else:
        install()
