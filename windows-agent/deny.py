"""ParentalDeny - a single plain message box shown in the user's session when a locked app is blocked."""
import ctypes
import sys

TEXT = {
    "en": "Access denied",
    "fr": "Accès refusé",
    "ar": "تم رفض الوصول",
    "es": "Acceso denegado",
    "de": "Zugriff verweigert",
    "pt": "Acesso negado",
    "tr": "Erişim engellendi",
    "ru": "Доступ запрещён",
}


def get_lang():
    a = sys.argv
    if "--lang" in a and a.index("--lang") + 1 < len(a):
        return a[a.index("--lang") + 1].lower().split("-")[0].split("_")[0]
    return "en"


if __name__ == "__main__":
    msg = TEXT.get(get_lang(), TEXT["en"])
    ctypes.windll.user32.MessageBoxW(0, msg, msg, 0x30)  # MB_ICONWARNING, OK only
