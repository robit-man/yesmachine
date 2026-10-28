# UX Contract

Audience: owners connecting their own desktop computers. English product UI;
browser locale/timezone for numbers and timestamps. WCAG 2.2 AA target.

## Business-context sources

Permission, exposure, data lifecycle and retention: `PRODUCT.md`, Access and
exposure. Feature scope: `docs/FEATURES.md`. No billing or legal workflows.

## Visual contract

`DESIGN.md` mirrors canonical `yesmachine/web/tokens.css`. Drift is checked by
`scripts/check_design.py`. Dark defaults, arbitrary validated RGB settings, and forced-colors support.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Form | app.js submitForm and fieldError | PRODUCT.md | create/edit | browser CRUD |
| Select/Listbox | native select | DESIGN.md | native | keyboard/browser |
| Scrollbar | styles.css app shell | user brief | independent sidebar/content/modal | computed style |
| Toast | app.js notify | this contract | success/info/error | live region |
| CRUD | devices API + device dialog | PRODUCT.md | return to devices | browser CRUD |

## Flow ledger

| Operation | Pending | Success | Failure recovery | Source ref |
|---|---|---|---|---|
| Save device | lock submit | close dialog, refresh list, announce | retain fields, inline error | PRODUCT.md |
| Remove device | named confirmation | refresh list, restore focus | keep dialog, retry | PRODUCT.md |
| Connect/files | lock action | native window launch acknowledged | inline engine error, retry | PRODUCT.md |
| Start hosting | tracked job | listener state verified | visible error, native permissions help | PRODUCT.md |
| Enable UPnP | exposure confirmation + tracked job | display external address/lease | preserve host, show router error | PRODUCT.md |
| Stop hosting | tracked job | remove owned mapping first | show cleanup error, retry | PRODUCT.md |

## Behavior

Hash navigation preserves the address-book query in URLSearchParams. Lists use
20-row pagination and reset to page one on search. Search is local, IME-safe,
clearable and never delayed. Empty, no-results, failed and loading states keep
the surrounding section stable. All native engine actions describe launches,
not verified remote success.

One notification owner uses a polite live region; persistent errors are also
shown inside their panel. Dialog owns focus containment via showModal, closes
on Escape, restores its invoking control and initially focuses Cancel for
exposure/removal/shutdown. Unsaved device edits require an app-owned discard
confirmation, including route changes. Native engine secrets never reach web UI.

Mutations are pessimistic and single-flight. The server has one job worker and
serializes engine/configuration changes. Poll every three seconds, preserve
forms and stale readable content on failure, show one persistent offline banner,
retry on the next poll. No writes queued offline. Late status responses cannot
overwrite a newer request. Errors preserve input; explicit retry revalidates.

Keyboard focus is visible. Navigation becomes a fixed icon rail on narrow viewports;
all actions wrap and remain reachable. Touch targets are at least 40px.
No date inputs or application-owned select popups. Unavailable capabilities
are described as unavailable, not fake controls.

## Verification

`python3 -m unittest discover -s tests -v`, `node --check yesmachine/web/app.js`,
`python3 scripts/check_design.py`, Python compileall, strict premium audit, and
browser CRUD, keyboard, failure, narrow viewport and screenshot review. Engine
display/control and router WAN reachability require separate real-machine tests;
report those independently of local panel tests.

## Phone and viewer contract

Phone access owns the generator, No expiry default, link list, and signed-in
session list. Keys/cookies never appear in status responses. QR presentation is
a deliberate secret-bearing action. Per-link cycle/revoke and session revoke
follow shared confirmation and job states. Public errors keep reconnect reachable.
The phone viewer hides controls in a right-edge menu, uses a draggable pointer,
provides fit/viewport modes and two-finger pan/pinch, and handles orientation.
Connection success requires rendered desktop pixels, not merely a socket/canvas.
VNC uses the same menu/form/job owners as RDP, with separate credentials and
explicit LAN encryption guidance. Linux-only hosting limits remain visible.
