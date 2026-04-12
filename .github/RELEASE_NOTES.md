## Stream Deck on Linux

The Stream Deck plugin now works on Linux without any manual setup. The HIDAPI native library is downloaded automatically when you install the plugin, matching the seamless experience on Windows. Pi images ship with the library and USB access rules pre-installed.

If something does go wrong, the Programmer UI now shows clear instructions instead of a cryptic Python error.

## Panel Fixes

- Fixed the clock element ignoring Date and DateTime display modes (always showing time-only)
- Fixed images not loading when using uploaded assets (assets:// URLs)

## Builder Fixes

- Fixed icons not rendering in Simple (On/Off) visual feedback mode
