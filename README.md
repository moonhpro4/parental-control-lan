# Parental Control (LAN)

- `windows-agent/` - headless Python agent (PyInstaller exe, UAC manifest, Task Scheduler as SYSTEM, WebSocket on port 354).
- `android/` - Kotlin control app (pair with IP + 6-digit PIN, Lock PC, app lock/unlock grid).
- Binaries are built by GitHub Actions and published on the **latest** release.

## Install
1. Run `ParentalAgent.exe` on the child's PC once, accept the UAC prompt. A dialog shows the PC's IP and the PIN.
2. Install `ParentalControl.apk` on the parent's phone, same Wi-Fi as the PC, enter just the PIN.
   The app finds the PC automatically via a UDP broadcast on port 355 - no need to type the IP, and it
   keeps working even if the router later gives the PC a new IP. Manual IP entry is still available
   (tap "Enter IP manually instead") as a fallback if broadcast is blocked on your network.
3. Tap the globe icon top-right to set the app's language regardless of the phone's system language.

Protocol: the PIN never crosses the network; the agent sends a random nonce and the app answers with HMAC-SHA256(PIN, nonce).
Traffic is otherwise unencrypted ws:// on the local network. Only use on a trusted LAN.

## Commands (type in cmd or Win+R; each asks for admin approval via UAC)
- `wiz.app.uninstall` - removes the agent, its startup task, firewall rule and all files.
- `wiz.app.pin` - shows the PC's IP and the current pairing PIN again (doesn't change it).
- `wiz.devices` - shows which phones are connected right now. Not a history of every device ever
  paired - the agent doesn't keep one; any phone with the current PIN can connect.
