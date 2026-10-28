# Verification

Verified on Ubuntu 24.04 / GNOME Wayland on 2026-10-28. Windows/macOS and physical
phone browser testing remain outstanding. Full NoMachine feature parity is not
claimed.

## Passing checks

- 37 Python tests: TLS data transfer, bad pin/key rejection, management isolation,
  storage/CRUD, UPnP ownership/conflict/lease/cleanup, passive listener checks,
  image instruction framing across all split positions, Unicode/chunk parsing,
  no-expiry authorization, cookie/origin checks, link cycle/revocation, VNC
  3.3/3.7/3.8 authentication, bad password rejection, Zlib pixels and input.
- Python compileall, both web/phone JavaScript syntax checks, and design-token
  drift checks pass. Editable install with declared dependencies succeeds.
- `tests/browser_smoke.cjs` against isolated port 21189: create/edit/search/remove,
  validation/focus, unsaved edits, failure recovery, confirmation cancellation,
  reduced motion, narrow layout, and no JavaScript page errors.
- Real public Cloudflare HTTPS phone endpoint rendered the actual 3840×2160
  desktop. `tests/mobile_browser.cjs` verifies many distinct, opaque desktop
  pixels, fragment removal, portrait viewport, touch pointer dragging, pinch zoom,
  landscape fit, menu bounds and reconnect. No JavaScript page errors.
- Live VNC authentication delivered real 1920×1080 desktop pixels. A public phone
  viewer and VNC renderer also joined the same desktop successfully. VNC internal
  keys stay out of public link/session lists. The renderer is downsampled to at
  most 1920×1080, with input mapped back to full desktop coordinates.
- Main control panel exposes No expiry (value 0), QR/link/session controls,
  RDP credential management, and VNC setup. Sidebar measured 900px at 900px
  viewport height; sidebar/content both scroll independently.
- Existing cloudflared and digest-pinned guacd backend checks pass. No NoMachine
  update was downloaded or installed.
- Earlier real RAX43v2 mapping creation/readback/ownership/removal succeeded.
  A mapping is not proof of external WAN reachability.

## Root cause and prevention

The browser relay previously sent arbitrary TCP chunks. Keepalive replies could
be inserted in the middle of an image instruction, corrupting the graphics stream
while the client still appeared connected. Complete-instruction framing prevents
that interleaving. Regression tests cover every split location, including image
payloads containing delimiters. Readiness now requires real pixels. A first-frame
timeout offers reconnect instead of an indefinite waiting screen. Reconnect
reuses one keyboard handler. Passive Linux listener inspection avoids making
spurious RDP connections during status polling.

## Design review

The previous thick side accent was removed. Mobile text size and danger-button
hover contrast were fixed. The design detector has no remaining primary findings.
A narrow `clipped-overflow-container` exception applies only to the management
HTML: the user explicitly requested the viewport-locked independent scrollers;
menus are viewport-fixed and dialogs use the top layer. Existing radius/color
advisories remain; no broad rule/file suppression was added.

The premium static audit reports three `affordance.actionless-button` findings
in dynamic `data-action` templates (device connect and native-profile actions).
These are false positives: the document click delegate calls `handleAction`,
and browser tests exercise those controls. The raw audit output is retained;
no fictitious inline handlers were introduced to satisfy its parser.

## Limits and unverified behavior

Three overlapping viewer starts during parallel QA caused one VNC renderer to
time out; a sequential phone-plus-VNC check then passed. Higher concurrency and
sustained load are not validated. Native RDP apps may take over GNOME's one RDP
session. Physical iOS/Android keyboard behavior, audio playback, VeNCrypt across
third-party products, reverse VNC clipboard sync, and RVNC-specific policies have
not been verified on devices. VNC clipboard currently goes client → desktop.

No two-machine native RustDesk desktop/file/audio session or external client
through the UPnP port was tested. Windows/macOS setup was not run. Headless
login, virtual desktop provisioning and transparent USB/printer/smart-card/disk
redirection are not implemented. See FEATURES.md and SECURITY.md.

Screenshots and detailed local browser results remain in ignored `artifacts/`;
no QR screenshots, secrets or runtime state are published.

## Initial snapshot and native cursor follow-up

Every visible browser now joins a persistent guacd desktop owner. The backend
sends its complete cached layers before incremental updates. The owner keeps
frames acknowledged and sends keepalives even when the screen is static. It is
created when sharing starts and canceled on gateway shutdown. Browser readiness
requires opaque coverage of the complete desktop, including after size changes.

`tests/initial_frame_browser.cjs` supplies a partial center tile, verifies that
it stays in loading state, then supplies an entire static desktop and checks
both corners before any mouse input. It also verifies that hover sends movement
with no button pressed, host-sent cursor positions update, the cursor uses the
host bitmap, and the mock circular pointer is absent. The live mobile-browser
test additionally requires 100% opaque initial coverage before any input.
A real 3840×2160 desktop passed, followed by touch, pinch, rotation and reconnect.

Tunnel maintenance also skips cleanup while share startup holds its lifecycle
lock, preventing premature tunnel shutdown before a new key is created. This
startup race has a dedicated regression test.
