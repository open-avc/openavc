## OSC Transport

OpenAVC now supports Open Sound Control (OSC) as a built-in transport type alongside TCP, Serial, UDP, and HTTP. Build drivers for OSC devices using the Driver Builder UI or .avcdriver YAML files, no Python required.

The first OSC driver ships with this release: **Behringer X32**, covering the full console (32 input channels, 16 mix buses, 6 matrices, 8 DCAs, 8 aux inputs, 8 FX returns, main stereo/mono, scene recall). Also compatible with the Midas M32. Install it from Browse Drivers.

## Per-Panel Page Navigation

Each connected panel now tracks its own current page independently. Previously, navigating on one panel changed the page on every connected panel.

## Faster Startup

Devices now connect concurrently at startup instead of one at a time. Spaces with many devices will come online noticeably faster.

## Device Connection Visibility

When a device is offline, the Programmer IDE now shows why (connection refused, timeout, DNS failure) and the reconnect attempt count. The sidebar shows live online/offline counts. Connectionless transports (OSC, HTTP) now correctly verify reachability instead of always reporting "connected."

## ISC Security

Inter-System Communication now uses HMAC challenge-response authentication instead of plaintext shared keys.

## Project Save Conflict Detection

Multiple sessions editing the same project will no longer silently overwrite each other. Stale saves get a conflict warning instead of quietly winning.

## New Drivers

- **Audio-Technica ATDM-1012 Digital SmartMixer.** Full simulator included.
- **Audio-Technica ATDM-0604 Digital SmartMixer.** Full simulator included.
- **Behringer X32 Digital Mixer (OSC).** Full console control over OSC.

## Driver Fixes

Fixes across Samsung MDC, LG SICP, Extron SIS, Crestron NVX, Novastar H Series, vMix, Sony Bravia, PJLink, Audio-Technica, Sonos, and Dante DDM. Improved state polling, fixed response parsing, reduced log noise, and corrected simulator controls.

## YAML Driver Enhancements

- **On-connect commands.** Send initialization commands automatically when the transport connects.
- **Simulator push notifications.** Simulators can broadcast unsolicited state changes to connected clients.
- **Fader range scaling.** Map protocol values (e.g. 0-255) to display ranges (e.g. 0-100%) on panels.

## Security Hardening

- Authentication tokens no longer exposed in error responses.
- Project library and AI proxy endpoints now require authentication.
- Plugin installation validates filenames, URLs, and version requirements.
- Driver loading detects and rejects duplicate Python driver IDs.
- URL inputs validated against SSRF attacks.

## Programmer IDE

- Startup errors surfaced as a popup on Windows instead of failing silently.
- Clear error message when the server port is already in use.
- Improved Cloud view UX (copy buttons, uptime, password field).
- Discovery scan reports progress during finalize phase.
- Update system shows which step failed and tracks pending updates.
- ARIA dialog attributes on all modals.

## Editor Fixes

Fixes across the variable, macro, and binding editors: condition rendering, trigger cooldowns, variable defaults, multi-state feedback labels, matrix drag containment, fader keyboard step, and status LED text labels. Date/time elements support day-of-week format tokens.

## Engine Reliability

- Overlapping project reloads are serialized.
- Triggers stop before variable cleanup during reload.
- Device removal is serialized to prevent state corruption.
- Backup restore clears orphaned assets and includes persisted state.
- Project and driver saves use atomic writes.
- New/Open/Restore properly clears all previous project data from the UI.

## Linux Update System

The update and rollback system has been redesigned around a systemd ExecStartPre helper script for more reliable rollback and cleaner service integration.

## Plugin System

The loader now enforces `min_openavc_version` so incompatible plugins fail with a clear message instead of crashing.
