# OpenAVC v0.20.0

- **The device simulator now launches on Windows installs.** On Windows the
  simulator failed to start with an internal error, so you could not test a
  project against simulated devices. It starts normally again.

- **Windows: the system tray always controls the server.** The Windows service
  is now installed by default, so the tray's Start, Stop, and Restart actions
  always work. Skipping the service during a custom install previously left the
  tray unable to start anything. The tray now also tells you when a service
  action fails instead of doing nothing.

- **Pick, don't type.** Command and action parameters, macro steps, and touch
  panel bindings now offer dropdowns wherever the choices are known: presets,
  inputs, child controls, and variables. Where free text still makes sense, it
  trims and validates your entry before sending it to the device.

- **Touch panel bindings are organized into Shows and Does.** Each element now
  splits clearly between what it reflects from state (Shows: value, items,
  appearance, visibility) and what it triggers (Does: press, change, select, and
  so on). A control can read a variable or device state directly without a
  helper macro, and a list's two-way selection updates correctly. Existing
  projects upgrade automatically when opened.

- **Multi-unit devices model their parts as child entities.** Drivers for matrix
  switchers, multi-zone amplifiers, and DSPs can address each input, output, or
  zone as its own child with its own controls, including devices whose layout is
  discovered at connect time.

- **Edit a YAML driver on disk and reload it.** A new reload action re-reads an
  `.avcdriver` file you changed outside the Driver Builder and reconnects its
  devices, matching what Python drivers already do.

- **Variables "Used By" lists wildcard subscriptions.** Scripts that subscribe
  to a pattern like `device.*` now show up correctly as users of the matching
  variables.

- **Fixed the touch panel preview when viewed through the cloud.**
