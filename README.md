# Parental Control (LAN)

- `windows-agent/` - headless Python agent (PyInstaller exe, UAC manifest, Task Scheduler as SYSTEM, WebSocket on port 354).
- `android/` - Kotlin control app (pair with IP + 6-digit PIN, Lock PC, app lock/unlock grid).
- Binaries are built by GitHub Actions and published on the **latest** release.

## Install
1. Run `ParentalAgent.exe` on the child's PC once, accept the UAC prompt. A dialog shows the PC's IP and the PIN.
2. Install `ParentalControl.apk` on the parent's phone (same Wi-Fi), enter the IP + PIN.

Protocol: the PIN never crosses the network; the agent sends a random nonce and the app answers with HMAC-SHA256(PIN, nonce).
Traffic is otherwise unencrypted ws:// on the local network. Only use on a trusted LAN.
