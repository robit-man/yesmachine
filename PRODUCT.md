# YesMachine

User brief: a new NoMachine alternative in `~/Desktop/yesmachine`, cross-platform,
using UPnP, targeting full feature equivalence. Do not download or install a
NoMachine update. Current connection to Basilisk must not be reconfigured.

Implementation decision: build a new local control center around the unmodified
RustDesk 1.5.0 desktop engine. Preserve upstream identity, license and matching
source. This is an integration, not a newly written remote-display protocol.
Engine session windows keep the RustDesk name. No automatic downloads or updates.

Host and viewer targets: Linux, Windows and macOS. Only Linux can be exercised
in this workspace. Phone browser viewing uses an isolated Guacamole gateway and Cloudflare Quick
Tunnel. It currently auto-hosts on Linux GNOME only. Standard RDP, existing NX,
and a VNC/RFB adapter provide additional native-client paths.

## Access and exposure

- The control panel binds only to IPv4 loopback, checks Host and Origin, requires
  a same-site HttpOnly session cookie and a custom request header for mutations.
- Starting hosting and publishing a router port are separate explicit actions.
  Default access requires the desktop owner's approval in the native engine.
- Never reuse, log, store or pass SSH passwords to the new application.
- YesMachine connections use TLS on TCP 21180. The native engine's TCP 21118
  listener allows only loopback addresses. The control-panel port is never forwarded.
- UPnP can manage only mappings carrying this installation's exact ownership
  marker. Refuse conflicts; do not overwrite NoMachine or other applications.
- Leases are renewed while the application runs; clean shutdown removes the
  owned mapping. A router allowing only permanent leases is reported explicitly.
- Closing the control-panel browser does not stop hosting. The app's Quit action
  removes the mapping and stops its owned host process.
- The native engine handles remote authentication, encryption, permissions and
  session concurrency. No pre-login script rejects a second viewer.
- Address-book records contain names, hostnames/IPs, port, optional MAC and paired
  certificate fingerprints/access keys. Secrets are never returned in status or
  stored in browser storage. Local state uses owner-only filesystem permissions
  on Unix; on Windows it inherits the account's filesystem ACL. It is not an
  encrypted credential vault: protect the local account and export profiles privately.
  Removal affects the address book, never the remote computer. Local activity
  retains the most recent 500 events. Runtime/configuration stays local. Linux
  engine configuration is isolated below `.state`; Windows and macOS use the
  native engine profile with a backup of existing settings. Close other RustDesk
  instances before using YesMachine; no system service is installed automatically.

## Honest scope

Desktop, input, audio, clipboard, files, display selection, recording and native
session tools come from the engine, subject to OS permissions and limitations.
NoMachine USB forwarding, transparent printer/disk/smart-card redirection and
Linux virtual-desktop provisioning are not provided. Do not claim complete
NoMachine parity. See `docs/FEATURES.md` for the delivery matrix.

## Phone and native client access

Explicitly generated QR links authorize remote control with independent keys and
expiry, including no timed expiry. Restart/shutdown revokes all links. Browser
sessions appear under Phone access; revoke one session or revoke/cycle its link.
Cloudflare terminates public HTTPS. The public gateway cannot reach management
routes or arbitrary upstreams. The managed RDP password stays in the OS keyring.
VNC has a separate 8-character in-memory password and no raw Internet publishing
control; classic RFB is for trusted LAN/VPN use. Theme colors are customizable.
The sidebar is 100vh with separate content scrolling, as explicitly requested.
