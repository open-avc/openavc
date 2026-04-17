## QR Code Pairing for Panel Apps

The Panel Access card in the Programmer now shows a QR code alongside the panel URL. Scan it from a tablet or phone to land on a page with **Open Panel** (browser) and **Install App** options. This sets up the groundwork for the native OpenAVC Panel apps for Android and iOS, which are in development.

## Network Discovery for Panel Apps

The server now advertises itself on the local network via mDNS as `_openavc._tcp.local.`. The upcoming Panel apps will use this to auto-find your OpenAVC system, so you don't have to type an IP address. You can disable the advertisement under discovery settings if your IT policy requires it.

## Per-View Error Recovery

If a view in the Programmer IDE hits an unexpected error, you'll now see a recovery screen with a clear message and a retry button instead of a blank page. The rest of the IDE keeps working while you recover from the crash.

## Minimum Platform Version for Drivers

Community drivers can now declare a minimum OpenAVC version they require. Installing a driver that needs a newer platform version gives a clear error at install time instead of a confusing runtime failure.

## Fixes

- Deleted devices no longer leave stale state in the cloud after the agent reconnects
- Hardened project file loading, ZIP extraction, and WebSocket broadcast against malformed input
- Tightened plugin recursion limits and regex validation to prevent runaway plugin code from hanging the server
- Fixed a throttle state issue that could cause polling to stall after long disconnects
- Logged previously swallowed exceptions to make troubleshooting easier

## Under the Hood

Significant internal refactors to the Programmer IDE, REST client, and cloud AI tooling. No user-visible changes, but makes future work faster and safer.
