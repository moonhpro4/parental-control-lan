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

## Screen view & remote control
Tap "View PC screen" once paired. While active, a small red tray icon appears on the PC's taskbar
reading "Rainbow is streaming your screen" - it cannot be hidden or dismissed while viewing is on,
and disappears automatically the moment you exit the screen view. Touchscreen mode taps/drags
directly on what you touch; Trackpad mode drags the cursor relatively and taps where it currently
sits, with a dedicated right-click button. A keyboard button brings up your phone's own keyboard
and types directly into the PC. This works over the same local/already-paired connection as
everything else - it is not yet available from outside your home network (see note below).

## App locking details
- Up to 500 apps can be locked at once.
- A locked app that's launched is closed immediately and a plain "Access denied" message appears
  on the PC - nothing more is explained.
- Locks are tied to the app's identity (its executable or, for Java-based launchers, its jar
  family name), so uninstalling and reinstalling the same app keeps it locked.
- Locking now also catches Java-based launchers (the process often runs as java.exe/javaw.exe
  with no name of its own) and apps that only ship a .jar with no native .exe.
- The device-lock state (and which apps/sites are blocked) is written to disk, and the agent
  re-applies it immediately on every restart and at the moment of every Windows sign-in - not just
  when the service gets around to noticing - closing the "restart to bypass the lock screen" gap.

## Website blocking
In the app, "Block websites" lets you type a domain and press Enter to block it on the PC; tap the
X next to a blocked site to unblock it. This works via the Windows hosts file (a DNS-level block
local to that PC, with the entries clearly marked so nothing else in the file is touched) and takes
effect immediately.

## Commands (type in cmd or Win+R; each asks for admin approval via UAC)
- `wiz.app.uninstall` - removes the agent, its startup task, firewall rule and all files.
- `wiz.app.pin` - shows the PC's IP and the current pairing PIN again (doesn't change it).
- `wiz.devices` - shows which phones are connected right now. Not a history of every device ever
  paired - the agent doesn't keep one; any phone with the current PIN can connect.
