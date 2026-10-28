# Security boundaries

The local control panel can launch native programs and read pairing keys. It is
bound only to 127.0.0.1; forwarding that port is not supported. Mutations require
an exact Host, same Origin, HttpOnly SameSite cookie, custom header and JSON body.
This protects against ordinary browser-origin attacks, not a compromised local
account. Local software running as the same user can access local files.

The public listener is a separate TLS tunnel. The host certificate is generated
locally; viewers must have its SHA-256 fingerprint before connecting. Certificate
validation uses that explicit pin, not public CA hostname validation. A random
32-byte access key follows only after the pin matches. Unauthenticated bytes are
never forwarded to the native engine. Failed keys are throttled per address.
The native engine still performs its own access/owner approval. Access keys are
stored in owner-protected local files/database; no OS credential vault is used.

Protect exported profiles as credentials. Removing a saved computer removes only
the viewer's local copy; it does not revoke a profile already copied elsewhere.
To revoke all exported profiles, replace the host access key in Network and send
fresh profiles to trusted devices. Host key rotation disconnects existing tunnels.

Native permissions remain OS-managed. The engine is not sandboxed: an approved
remote-control session has the desktop owner's permissions. This app does not
provide a system-level unattended service or bypass screen/input permission gates.
Advanced changes in the native RustDesk settings can weaken its whitelist or
approval policy; preserve the configured loopback whitelist and approval mode.

UPnP increases network reachability only after the user enables it. The router's
external address must be global; carrier NAT is reported. Mapping ownership
requires the exact install marker, local IP, internal port and TCP protocol.
Other mappings are never intentionally overwritten or removed. Lease fallback
and cleanup are explicit; a process crash or unavailable router can leave a
mapping behind. A public mapping is not evidence that a WAN client can connect.

The bundled engine is pinned to 1.5.0 and checked against its release digest.
Matching source is included here. No automatic update is performed; maintaining
the engine and dependencies over time is a separate explicit maintenance task.
This is a newly built integration, not a completed independent security audit.

## Phone browser and VNC

Phone HTTPS terminates at Cloudflare, then goes through cloudflared to an
isolated loopback gateway. It exposes only the viewer assets, key login, session
status and authenticated WebSocket. No management routes or arbitrary RDP
targets are exposed. Share keys are random, carried in the QR URL fragment,
removed from history, and exchanged for HttpOnly SameSite cookies. Failed keys
are throttled; sessions/links are bounded and revocable. No-expiry links have no
timed deadline while the app runs. Restart revokes them and changes Quick Tunnel
addresses. Anyone retaining a valid link can sign in after individual session
revocation; cycle/revoke the link to prevent that. Public QR codes are secrets.

Managed RDP uses TLS/NLA and an OS-keyring password, distinct from system/SSH
credentials. The browser backend connects only to this local managed server.
Its self-signed certificate is checked at startup, then accepted for that
loopback guacd path. Native RDP/NX UPnP controls expose their native protocols
and authentication independently from YesMachine pairing.

The VNC adapter uses an isolated local Chrome profile and an internal, private
gateway grant. Its separate 8-character VNC password is held in memory. It
offers VeNCrypt X509Vnc TLS with a self-signed certificate; clients must verify
that certificate. Legacy VNCAuth has a weak password space and no transport
encryption. Use a trusted LAN or VPN. No raw VNC Internet mapping is offered.
Keys, passwords, QR screenshots, browser profiles, logs and runtime state are
excluded from the public repository and source archive.
