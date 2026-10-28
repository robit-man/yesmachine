---
version: alpha
name: YesMachine
description: A calm, practical desktop connection console with a visible network path.
colors:
  background: "#101419"
  surface: "#191f27"
  text: "#ffffff"
  muted: "#b4c0ce"
  primary: "#226b9a"
  secondary: "#202f3e"
  border: "#3a4655"
  success: "#8de0b4"
  warning: "#ffd08b"
  danger: "#ffacb8"
  link: "#9bd5ff"
  on-primary: "#ffffff"
typography:
  sans:
    fontFamily: '"Aptos", "Segoe UI", system-ui, sans-serif'
  display:
    fontFamily: '"Trebuchet MS", "Aptos", system-ui, sans-serif'
  mono:
    fontFamily: '"Cascadia Code", "SFMono-Regular", monospace'
rounded:
  DEFAULT: "0.75rem"
  sm: "0.375rem"
  md: "0.75rem"
  lg: "1.25rem"
spacing:
  section-gap: "2rem"
  page-max: "76rem"
components:
  button: {}
  card: {}
  dialog: {}
  input: {}
---

# YesMachine Design System

## Overview

The reference is a physical network switch's connection diagram: show exactly
which machine, listener and router are involved. The signature is one quiet
three-node network path with plain status text. This is a product for someone
switching among their own computers, not a marketing page. English UI; dates use
the viewer's locale and timezone. No Japan-specific market scope is assumed.

## Colors

Dark graphite backgrounds, white text, blue actions, and pale blue links.
Status always has text. `yesmachine/web/tokens.css` owns runtime tokens (Model B);
the frontmatter mirrors the values. `scripts/check_design.py` verifies drift.

## Typography

Trebuchet display headings carry a modest mechanical character. Aptos/Segoe UI
body text stays familiar. Cascadia/SFMono is reserved for addresses and ports.
Body 15px/1.55, headings 32px/1.15, utility 12px/1.4. System fallback only: no
font requests, delayed swaps or third-party assets.

## Layout

Desktop: 220px navigation and a flexible main column up to 76rem. Main content
uses a 1.5:1 grid with a compact host panel. At 850px, navigation becomes a compact fixed icon
rail; at 640px, all sections stack. The sidebar is locked at 100vh/100dvh and scrolls independently; the right
workspace owns its own vertical scroll. Cards/forms retain natural height, and
modal bodies can scroll. Rhythm is 8px.

## Elevation & Depth

Use tonal panels and thin borders. No ornamental gradients or floating cards.
Only modal overlays use a shadow; focus uses a visible 3px outline.

## Shapes

12px panel corners, 6px controls, 20px feature panel. Icons are simple inline
SVG strokes with text labels. No unrelated decorative imagery.

## Components

Buttons share solid brand, outline neutral and danger variants. Hover darkens,
focus outlines, pressed shifts 1px, busy preserves width and disables duplicate
submission. Disabled controls have a textual explanation. Native select popups
are intentionally OS-owned. Every form owns inline errors and first-error focus.

Navigation uses hash routes and aria-current. Address-book rows share one action
pattern. Search has an explicit clear button. The global status strip persists
across routes. Toasts acknowledge success; persistent errors stay inline.
Notices use a thin outline on all sides; warning and error text retain semantic
colors without an accent stripe.

Dialogs use native HTML dialog with app-owned content, explicit labels, cancel,
Escape and focus restoration. Secrets are entered only in the native engine.
Loading uses text plus a small spinner, never fabricated progress.

Motion is limited to 120ms color and opacity feedback. Reduced-motion removes
transitions. Scrollbars inherit global semantic tokens and respect forced colors.

## Do's and Don'ts

- Show actual listener and router state; distinguish requested from connected.
- Keep the same action names in dashboard, devices and activity.
- Do not mark a launched native viewer as an established remote connection.
- Do not sell unsupported passthrough features with enabled-looking controls.
