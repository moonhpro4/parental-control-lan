"""ParentalOverlay - full-screen "device locked" screen. Launched by the agent in the user's session.
Not a security boundary against an administrator; the agent relaunches it if it is closed."""
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import tkinter as tk

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

TEXT = {
    "en": ("This device has been locked", "Ask a parent to unlock it."),
    "fr": ("Cet appareil a été verrouillé", "Demandez à un parent de le déverrouiller."),
    "ar": ("تم قفل هذا الجهاز", "اطلب من أحد الوالدين فتح القفل."),
    "es": ("Este dispositivo ha sido bloqueado", "Pide a un adulto que lo desbloquee."),
    "de": ("Dieses Gerät wurde gesperrt", "Bitte einen Elternteil, es zu entsperren."),
    "pt": ("Este dispositivo foi bloqueado", "Peça a um responsável para desbloquear."),
    "tr": ("Bu cihaz kilitlendi", "Bir ebeveynden kilidi açmasını isteyin."),
    "ru": ("Это устройство заблокировано", "Попросите родителей разблокировать его."),
}

_keep = []  # keep ctypes callbacks / handles alive


def get_lang():
    a = sys.argv
    if "--lang" in a and a.index("--lang") + 1 < len(a):
        return a[a.index("--lang") + 1].lower().split("-")[0].split("_")[0]
    return "en"


def single_instance():
    h = kernel32.CreateMutexW(None, False, "ParentalOverlayMutex")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    _keep.append(h)


def block_keyboard():
    """Low-level hook swallowing every key (Alt+Tab, Alt+F4, Win key, ...). Ctrl+Alt+Del cannot be hooked."""
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = wt.HHOOK
    user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    kernel32.GetModuleHandleW.restype = wt.HMODULE

    def proc(n_code, w_param, l_param):
        if n_code >= 0:
            return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    cb = HOOKPROC(proc)
    _keep.append(cb)

    def pump():
        user32.SetWindowsHookExW(13, cb, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=pump, daemon=True).start()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    single_instance()
    block_keyboard()
    title, sub = TEXT.get(get_lang(), TEXT["en"])

    root = tk.Tk()
    root.configure(bg="black", cursor="none")
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)    # virtual screen origin
    w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)    # virtual screen size (all monitors)
    pw, ph = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)    # primary monitor
    root.overrideredirect(True)
    root.geometry(f"{w}x{h}{x:+d}{y:+d}")
    root.attributes("-topmost", True)

    box = tk.Frame(root, bg="black")
    tk.Label(box, text=title, fg="white", bg="black", font=("Segoe UI", 44, "bold")).pack()
    tk.Label(box, text=sub, fg="#9a9a9a", bg="black", font=("Segoe UI", 22)).pack(pady=(14, 0))
    box.place(x=pw // 2 - x, y=ph // 2 - y, anchor="center")

    root.protocol("WM_DELETE_WINDOW", lambda: None)  # Alt+F4 / close does nothing

    def keep_on_top():
        root.lift()
        root.attributes("-topmost", True)
        root.focus_force()
        root.after(100, keep_on_top)

    keep_on_top()
    root.mainloop()


if __name__ == "__main__":
    main()
