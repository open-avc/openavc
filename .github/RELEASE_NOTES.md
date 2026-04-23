## OSC Transport

OpenAVC now supports Open Sound Control (OSC) as a built-in transport type alongside TCP, Serial, UDP, and HTTP. Build drivers for OSC devices using the Driver Builder UI or .avcdriver YAML files, no Python required. Commands use OSC address paths and typed arguments, responses match by address pattern instead of regex.

The first OSC driver ships with this release: **Behringer X32**, covering the full console (32 input channels, 16 mix buses, 6 matrices, 8 DCAs, 8 aux inputs, 8 FX returns, main stereo/mono, scene recall). Also compatible with the Midas M32. Install it from Browse Drivers.

## New Drivers

- **Audio-Technica ATDM-1012 Digital SmartMixer.** 12 input channels, output levels, presets, auto mix control, and phantom power. Full simulator included.
- **Audio-Technica ATDM-0604 Digital SmartMixer.** 6 input channels with the same control set. Full simulator included.
- **Behringer X32 Digital Mixer (OSC).** Full console control over OSC. Faders, mutes, pans, channel names, bus sends, DCAs, matrices, aux inputs, FX returns, scene/snippet/cue recall.

## Simulator: OSC Support

The device simulator now supports OSC devices. A new `OSCSimulator` base class handles UDP/OSC message decoding and address-based routing. YAML drivers with `transport: osc` get automatic simulation from their response definitions: messages with arguments update state and echo back, messages without arguments return current values.

## Connection Health for All Transports

Devices using connectionless transports (OSC, HTTP) now correctly report their connection status. Previously, these devices would show as "connected" immediately even if the hardware wasn't reachable. Now:

- **On connect:** OSC devices are verified with an `/info` probe. HTTP devices are verified with a HEAD request. If the device doesn't respond, it enters the auto-reconnect loop.
- **Ongoing:** The poll loop monitors for responses. If a device stops responding for several consecutive poll cycles, it's marked as disconnected and auto-reconnect begins.

## Variable and Macro Editor Fixes

A batch of fixes across the variable and macro editors: condition row rendering, trigger cooldown fields, variable delete confirmation, default variable values, and consistent spacing throughout.

## Binding Editor Improvements

- Status LED labels can now display text alongside the color indicator.
- Date/time elements support day-of-week format tokens.
- Matrix drag operations no longer leak into other UI areas.
- Multi-state feedback labels render correctly on buttons with icons.
- Fader elements respond to keyboard arrow keys with configurable step sizes.

## YAML Driver Enhancements

- **on_connect commands:** YAML drivers can now send initialization commands when the transport connects, using the `on_connect` list in the driver definition.
- **Simulator push notifications:** YAML driver simulators can broadcast unsolicited state change messages to connected clients using the `notifications` section.
