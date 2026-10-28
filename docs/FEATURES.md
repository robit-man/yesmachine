# Feature delivery matrix

YesMachine integrates existing desktop engines. It does not implement the full
NoMachine feature set, and must not be described as cross-platform tested.

| Feature | Delivery | Verification / limits |
|---|---|---|
| Native Linux host/viewer | Unmodified RustDesk 1.5.0 | Local host/listener lifecycle verified; two-machine native session not exercised |
| Windows/macOS | Setup + launchers | Not run on those operating systems |
| Desktop, input, files, audio, monitors, recording, chat | Native engine | Engine/OS dependent; these native session tools not independently tested here |
| Phone browser desktop | Guacamole 1.6.0 + managed GNOME RDP | Actual 3840×2160 desktop rendered through public Cloudflare endpoint |
| Mobile interaction | Pointer, hold-drag, direct touch, pinch/pan, portrait viewport, landscape fit | Chromium phone emulation tested; physical Safari/Android device testing remains |
| Mobile keyboard and clipboard | Browser menu | Implemented; OS keyboard/clipboard policies apply |
| Public QR share | Existing cloudflared Quick Tunnel | HTTPS browser endpoint tested; key in fragment, cookie auth |
| Share lifecycle | No expiry / 15m / 1h / 4h; cycle, revoke links/sessions | Automated auth/lifecycle tests; restart revokes all shares |
| Native RDP clients | GNOME TLS/NLA and `.rdp` export | Real local RDP used by browser and VNC; OS keyring required |
| NoMachine Player | Existing server + `.nxs` export | No NX emulation; no server update/reconfiguration |
| VNC clients | RFB 3.3/3.7/3.8; VNCAuth; VeNCrypt X509Vnc; Raw/Zlib | Real desktop framebuffer delivered; wire/auth/input tests pass. RVNC product/version policies apply |
| VNC clipboard | Client → desktop text | Server → client clipboard synchronization not implemented |
| Encrypted native tunnel | TLS + certificate pin + random access key | Large binary transfer and rejection before upstream access tested |
| Address book and pairing | Private local storage + profile import/export | Browser CRUD/recovery tested |
| UPnP | Owned TCP mappings, renewal, cleanup | Real RAX43v2 mapping lifecycle previously verified; WAN reachability not independently tested |
| Dark/custom theme | Twelve configurable colors | Shared settings; independent sidebar/content scrolling |
| Wake-on-LAN | Local broadcast | Hardware wake not tested |
| USB/printer/smart-card forwarding | Not implemented | Requires additional native integration |
| Transparent disk mounting | Not implemented | Use native file transfer |
| Virtual desktops / headless login | Not implemented | Shares an existing logged-in desktop |
| Signed installers / auto-update | Not provided | Source launchers; startup performs no downloads |
