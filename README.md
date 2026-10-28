# YesMachine

A desktop connection console with a phone browser viewer, revocable QR shares,
standard RDP and VNC access, native NoMachine profiles, and owned UPnP mappings.
Native YesMachine sessions use the unmodified RustDesk 1.5.0 engine.

This is an early integration, **not full NoMachine feature parity**. Linux is
verified here; Windows/macOS launchers are provided but have not been tested on
those platforms. NoMachine is never updated or reconfigured by YesMachine.
See [the feature matrix](docs/FEATURES.md) for specific limits.

## Start

Python 3.11+ is required. On this configured Linux computer, double-click
**YesMachine** on the Desktop or run `./run.sh`. The local control panel opens at
`http://127.0.0.1:21179`. Closing its window keeps the service running; use
**Quit YesMachine** to stop it and remove its owned router mappings.

For a fresh checkout, explicitly install the separate, checksum-pinned runtime
and Python dependencies first:

```bash
python3 scripts/setup.py
./run.sh
```

Windows: `Setup.cmd`, then `YesMachine.cmd`. macOS: `Setup.command`, then
`YesMachine.command`. Setup never downloads a NoMachine update. Normal startup
never downloads software. Screen/input permissions remain OS managed.

## Phone access

On Ubuntu GNOME, install the browser backend once using the existing Docker and
cloudflared installations:

```bash
python3 scripts/setup-mobile.py
```

Open **Phone access**, choose **Share expiry**, and **Generate tunnel**. Scan the
QR with your phone. The Cloudflare HTTPS endpoint presents the remote desktop
web app; the private key in the QR fragment is removed from browser history when
read. The management API is never exposed through this endpoint.

The compact right tab opens input/display controls. Drag to move the mouse,
tap to click, hold then drag to move windows, and use two fingers to pan/pinch.
Portrait defaults to a 1:1 viewport; landscape fits the screen. Keyboard,
clipboard, right click, zoom, and reconnect are in the menu.

**No expiry** lasts until revocation or app/tunnel shutdown, with no timed share
deadline. Quick Tunnel addresses change on restart; this is not a permanent
hostname. Links and signed-in browser sessions appear below the generator.
Revoke an individual session, revoke a link, or cycle that link's QR key.
Cycling disconnects that link's viewers. A device retaining a valid link can
sign in again after its session is revoked; revoke/cycle the link to prevent it.

Anyone holding the QR/key can control the desktop. Cloudflare terminates HTTPS;
this path is not encrypted end to end against Cloudflare. Keep QR codes private.
Phone hosting currently requires a logged-in, unlocked GNOME desktop, its
keyring, and GNOME Remote Desktop. The pinned guacd Docker path uses Linux host
networking. Other host platforms require additional backend integration.

## Other clients

**Other clients** shows addresses and controls for:

- **RDP:** managed GNOME Remote Desktop with separate credentials stored in the
  OS keyring, TLS/NLA, and password-free `.rdp` exports. Use **Set RDP credentials**
  to choose a password your native client can use. Ordinary RDP clients can
  connect. Stop RDP restores the settings YesMachine replaced.
- **NoMachine Player:** `.nxs` exports use your existing NoMachine server and its
  system credentials. No proprietary NX protocol is reimplemented.
- **VNC / RFB:** enable a separate, exactly 8-character VNC password. Compatible
  TigerVNC, TightVNC, Remmina, and RVNC versions can connect to the displayed LAN
  address. Some RealVNC versions restrict third-party servers. The adapter offers
  VNC authentication, VeNCrypt X509Vnc TLS, Raw/Zlib, mouse, keyboard, and clipboard
  input. Classic VNC screen traffic is unencrypted: use a trusted LAN or VPN.
  A local Chrome/Chromium browser renders the same desktop for this adapter.

RDP and phone viewers use one shared upstream desktop while connected through
the browser gateway. A separate native RDP client may take over the GNOME host's
single native session. Phone/VNC credentials do not use RustDesk approval.

## Native sessions and UPnP

Run YesMachine on both computers. On the host, **Start hosting**, export its
private LAN connection profile, import it on the viewer, then **Connect** and
approve the request in the native RustDesk window. Use the native menus for
files, audio, clipboard, display selection, quality, recording, and chat where
supported by the engine and OS.

**Network → Enable UPnP access** publishes the certificate-pinned YesMachine TLS
listener on TCP 21180. The native engine listener and local management panel
remain private. Native RDP/NX publishing has separate explicit controls and uses
those servers' own authentication. Mappings are checked for ownership, renewed,
and removed on clean shutdown. UPnP cannot solve carrier NAT or an upstream
router. External reachability needs a test from another network.

## Appearance

The sidebar stays at viewport height and scrolls independently from the content.
Dark mode and white text are defaults. **Settings** lets you change all theme
colors, preview, save, reset, or cancel; phone controls use saved colors too.

## Verify and package

```bash
.venv/bin/python -m unittest discover -s tests -v
python3 -m compileall -q yesmachine scripts
node --check yesmachine/web/app.js
node --check yesmachine/mobile/phone.js
python3 scripts/check_design.py
# With Playwright installed and an isolated test instance running:
YESMACHINE_TEST_URL=http://127.0.0.1:21189 node tests/browser_smoke.cjs
python3 scripts/package.py
```

`tests/mobile_browser.cjs` receives `{url,key,fragment:true}` as JSON on stdin
and tests real desktop pixels, touch dragging, pinch, rotation, and reconnect.
Never put the key on the command line. See [verification](docs/VERIFICATION.md)
and [security boundaries](docs/SECURITY.md).

## License and source

YesMachine: AGPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
The unmodified RustDesk engine retains its upstream name, notices, and license.
Use `python3 scripts/setup.py --runtime-only --source` to fetch its pinned matching
source. Apache Guacamole JavaScript is bundled with its Apache license/notices;
the Docker backend is pinned by digest. Runtime state, credentials, binaries,
screenshots, and local profiles are excluded from source releases.
