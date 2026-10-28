# Third-party notices

YesMachine integrates the unmodified RustDesk desktop engine, version 1.5.0.
Native windows retain the upstream RustDesk name and identity. No affiliation with
or endorsement from RustDesk or NoMachine is implied.

- Upstream: https://github.com/rustdesk/rustdesk
- Release: https://github.com/rustdesk/rustdesk/releases/tag/1.5.0
- Corresponding source commit: `fada664df7a294d1d1a9ca3e7cd3637069122f17`
- hbb_common submodule: `229b904508364c8997aad0fb5af57effac859f60`
- Engine license: GNU Affero General Public License v3.0 (upstream `LICENCE`).
- Matching engine source and notices: `vendor/rustdesk-source/` in this workspace.
- Platform assets and release SHA-256 digests: `vendor/manifest.json`.

When distributing the engine with this app, preserve its upstream notices and
make its corresponding source available along with the YesMachine source.
The small source distribution can retrieve the pinned engine/source via the
explicit setup script; it does not contain the user's `.state` directory.

The Python `cryptography` dependency retains its own Apache-2.0/BSD licenses.
System libraries and the engine's dependencies retain their respective licenses
and notices in the corresponding upstream source/distribution.

Apache Guacamole 1.6.0 JavaScript is bundled in `yesmachine/mobile/guacamole.js`.
Its matching source modules and manifest are in `vendor/guacamole-js-source`.
Upstream: https://github.com/apache/guacamole-client/tree/1.6.0
License: Apache-2.0; preserve `vendor/guacamole-js-source/LICENSE` and `NOTICE`.
The separate guacd Docker backend is pinned in `vendor/browser-backend.json`;
its upstream is https://github.com/apache/guacamole-server/tree/1.6.0.
Cloudflared is a separately installed executable, not redistributed here.
Pillow, NumPy, aiohttp, qrcode, and psutil retain their upstream licenses.
