"""
RainbowAgent (screen helper) - runs in the logged-in user's desktop session, launched by the main
agent only while a phone is actively viewing the screen. Captures the screen, injects mouse/keyboard
input from the phone, and shows a red tray icon that cannot be hidden while it's running.

Talks to the main agent over a loopback-only TCP socket (127.0.0.1) - never reachable from the network.
"""
import ctypes
import json
import socket
import struct
import sys
import threading
import time

from PIL import Image
from PIL.ImageGrab import grab
import io
import pystray

RELAY_HOST = "127.0.0.1"
RELAY_PORT = 353
MSG_FRAME = 1
MSG_CONTROL = 2

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

TEXT = {
    "en": "Rainbow is streaming your screen",
    "fr": "Rainbow diffuse votre écran",
    "ar": "Rainbow يبث شاشتك الآن",
    "es": "Rainbow está transmitiendo tu pantalla",
    "de": "Rainbow überträgt deinen Bildschirm",
    "pt": "Rainbow está transmitindo sua tela",
    "tr": "Rainbow ekranınızı yayınlıyor",
    "ru": "Rainbow транслирует ваш экран",
}


def get_lang():
    a = sys.argv
    if "--lang" in a and a.index("--lang") + 1 < len(a):
        return a[a.index("--lang") + 1].lower().split("-")[0].split("_")[0]
    return "en"


def single_instance():
    h = kernel32.CreateMutexW(None, False, "ParentalScreenHelperMutex")
    if kernel32.GetLastError() == 183:
        sys.exit(0)


# --------------------------------------------------------------------------- relay framing
def send_msg(sock, lock, msg_type, payload):
    with lock:
        sock.sendall(struct.pack(">IB", len(payload), msg_type) + payload)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(sock):
    header = recv_exact(sock, 5)
    if not header:
        return None, None
    length, msg_type = struct.unpack(">IB", header)
    payload = recv_exact(sock, length)
    if payload is None:
        return None, None
    return msg_type, payload


# --------------------------------------------------------------------------- input injection
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0004, 0x0002, 0x0008
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL = 0x0800
VK = {"enter": 0x0D, "backspace": 0x08, "tab": 0x09, "escape": 0x1B,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "delete": 0x2E, "space": 0x20}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def mouse_move_abs(nx, ny):
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x = int(max(0, min(1, nx)) * 65535)
    y = int(max(0, min(1, ny)) * 65535)
    mi = MOUSEINPUT(x, y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
    _send(INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi)))


def mouse_move_rel(dx, dy):
    mi = MOUSEINPUT(int(dx), int(dy), 0, MOUSEEVENTF_MOVE, 0, None)
    _send(INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi)))


def mouse_button(down, right=False):
    flag = (MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP) if right else \
           (MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)
    mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
    _send(INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi)))


def mouse_scroll(delta):
    mi = MOUSEINPUT(0, 0, int(delta), MOUSEEVENTF_WHEEL, 0, None)
    _send(INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi)))


def key_text(ch):
    code = ord(ch)
    down = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
    up = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
    _send(INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=down)), INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=up)))


def key_special(name):
    vk = VK.get(name)
    if vk is None:
        return
    down = KEYBDINPUT(vk, 0, 0, 0, None)
    up = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
    _send(INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=down)), INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=up)))


def apply_control(msg):
    try:
        cmd = msg.get("cmd")
        if cmd == "mouse_move":
            mouse_move_abs(float(msg["x"]), float(msg["y"]))
        elif cmd == "mouse_delta":
            mouse_move_rel(float(msg["dx"]), float(msg["dy"]))
        elif cmd == "mouse_down":
            mouse_button(True, msg.get("button") == "right")
        elif cmd == "mouse_up":
            mouse_button(False, msg.get("button") == "right")
        elif cmd == "scroll":
            mouse_scroll(float(msg["dy"]))
        elif cmd == "key_text":
            for ch in msg.get("text", ""):
                key_text(ch)
        elif cmd == "key_special":
            key_special(msg.get("key", ""))
    except Exception:
        pass


# --------------------------------------------------------------------------- tray icon
def make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=(230, 40, 60, 255), outline=(255, 255, 255, 255), width=3)
    return img


def run_tray(tooltip):
    icon = pystray.Icon("rainbow", make_icon_image(), tooltip, menu=pystray.Menu())
    icon.run()


# --------------------------------------------------------------------------- main loops
def capture_loop(sock, lock, stop_evt):
    last_size = None
    while not stop_evt.is_set():
        try:
            img = grab()
            w, h = img.size
            if w > 1280:
                scale = 1280 / w
                img = img.resize((1280, int(h * scale)))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=55)
            send_msg(sock, lock, MSG_FRAME, buf.getvalue())
        except Exception:
            pass
        time.sleep(0.12)  # ~8 fps


def control_loop(sock, stop_evt):
    while not stop_evt.is_set():
        msg_type, payload = recv_msg(sock)
        if msg_type is None:
            break
        if msg_type == MSG_CONTROL:
            try:
                apply_control(json.loads(payload.decode("utf-8")))
            except Exception:
                pass
    stop_evt.set()


def main():
    single_instance()
    lang = get_lang()
    threading.Thread(target=run_tray, args=(TEXT.get(lang, TEXT["en"]),), daemon=True).start()

    while True:
        try:
            sock = socket.create_connection((RELAY_HOST, RELAY_PORT), timeout=5)
            break
        except OSError:
            time.sleep(0.5)

    lock = threading.Lock()
    stop_evt = threading.Event()
    threading.Thread(target=capture_loop, args=(sock, lock, stop_evt), daemon=True).start()
    control_loop(sock, stop_evt)  # blocks until the service disconnects us
    sys.exit(0)


if __name__ == "__main__":
    main()
