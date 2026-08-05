# OpenAVC v0.24.0

- **Free panel layout.** The UI Builder is now a design canvas. Controls go
  anywhere on the page, sized as percentages of the screen, with drag snapping
  on an adjustable ruler, alignment and distribution tools, match size, marquee
  selection, and element locks. Groups are real containers that carry their
  contents when they move, the Outline panel is a drag-and-drop tree, and any
  control can lock its aspect ratio. Existing projects convert automatically
  on first load and keep their look.

- **Portrait layouts.** A page can carry a separate arrangement per
  orientation, so the same project serves a wall-mounted portrait tablet and a
  landscape touch screen. Panel text and control chrome now scale with the
  screen, so one design fits any display of the same shape at any size. For
  looks the theme system cannot express, elements accept a CSS class and a
  project can ship its own stylesheet.

- **The Driver Builder authors the whole driver format.** JSON response rules,
  what a command sets and queries, commands that run while the device is
  offline, device actions, and an Open Web UI button. The Test tab shows the
  exact bytes a command puts on the wire before anything is sent, and the
  platform's own validation runs inline while you edit. Driver authors can
  check a driver file from the terminal with `python -m server.drivers.check`,
  and a driver's minimum platform version is computed from the features it
  uses.

- **Open Web UI.** Devices with a built-in web page get an action that opens
  it straight from OpenAVC, auto-detected on discovered devices where
  possible. Discovery can also identify a device by its TLS certificate
  subject, and network scans on macOS now capture MAC addresses.

- **Update safety.** The updater can verify release signatures before
  applying an update, on every platform including the Raspberry Pi image.
  Automatic rollback now restores user data from the backup taken before the
  update, so a failed update cannot take recent programming with it.

- **Session sign-in.** The Programmer exchanges the password for a session
  token at sign-in instead of sending credentials with every request, and the
  live log stream requires an authenticated session.

- **Cloud agent hardening.** The agent negotiates its protocol version with
  the platform so either side can update first, refuses oversize messages
  instead of stalling fleet operations, and reconnects after sustained silence
  on the line.

- **Verified installs.** Community drivers and plugins are checked against the
  catalog's checksums before anything is written to disk, and a plugin that
  wants extra browser permissions in its panel pages asks for them at install
  time.

- **Simulator improvements.** Device simulators serve every transport from one
  implementation, can serve TLS for HTTPS-only devices, model child entities
  in auto-generated simulators, and answer coalesced or unterminated command
  streams correctly. A WebSocket server base covers devices that speak
  WebSocket natively.

- **Reliability fixes.** A slow panel connection no longer stalls updates for
  other clients. A macro started by a trigger is not cancelled by an unrelated
  project edit, and macros gain per-macro overlap and cooldown rules. A device
  that rejects its credentials stops auto-reconnecting instead of retrying
  into an IP lockout, and a device behind an offline bridge reports that as
  the reason. Fractional-step sliders and faders no longer emit floating point
  noise, and the projector power button can turn a cooling projector back on.

- **Odds and ends.** OpenAVC builds as a standard Python wheel, so pip
  installs work for development and embedded setups. The server answers its
  root address with a landing page, the System Log gains a Download button,
  the setup screen lists every network address on the machine, and child
  processes on Windows no longer pop console windows.
