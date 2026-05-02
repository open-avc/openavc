The Driver Builder gets a major overhaul in this release, with a redesigned tab layout, real-time validation, inline help, and first-class editors for protocol features you previously had to write by hand. Cloud reliability also gets a tune-up, and a couple of platform bugs are fixed.

## Driver Builder

The 10-tab layout has been collapsed to 6, with related fields grouped together. Each tab carries a validation badge so you can see at a glance which sections need attention, and validation messages appear inline next to the field they refer to instead of in a wall at the top.

Every section now has a help link to the relevant documentation page. Built-in drivers are visually distinguished from drivers you've added.

New first-class editors for things that used to require hand-editing the YAML:

- Frame parsers for binary protocols
- Telnet authentication blocks
- on_connect lifecycle hooks
- OSC argument types and value bounds
- Discovery hints (UPnP types)
- Full command parameter schemas
- Catalog metadata and help fields

HTTP commands are now fully supported in the builder, including custom headers.

The Live Test panel rebuilds the driver against the same code path it'll run in production, so what you test is what gets shipped.

You can also duplicate an existing driver as a starting point for a new one.

## Drivers and Plugins management

Cleaner navigation between the driver list and the editor. The action toolbar moved into the header for easier reach, and now refuses to uninstall a driver or plugin that's in use by your current project, with a clear message about what's depending on it. Uninstall errors that do happen are clearer about why.

Browse Drivers now splits multi-brand drivers (a single driver supporting many manufacturer SKUs) into one card per brand, so a search for "Sony" actually finds the Sony devices instead of hiding them inside a generic entry.

## Cloud

Two reliability fixes:

- The cloud agent could end up in a reconnect loop after a handshake error because of a typo in the exception handler. Connections now recover cleanly.
- A failed session resume previously dropped the entire connection. The agent now drops the unsendable message buffer and continues.

## AI assistant

The AI's driver search and device match tools now query the community driver index for real instead of returning placeholder data. Asking the assistant to find a driver for a specific device now works.

## Bug fixes

- **Windows:** The Rollback section no longer appears on fresh installs. Previously it would show "Previous version: vunknown" with a "Rollback to v?" button before any update had ever been applied.
- **Pi image:** Fixed an issue where Pi OS Trixie's userconfig service was overriding our auto-login configuration on first boot, leaving the display sitting on a blank kiosk screen. Newly flashed Pi images now boot directly to the OpenAVC info page or panel.

## Documentation

The Driver Builder walkthrough has been rewritten for the new editor.
