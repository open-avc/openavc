## Update System

Fixed several issues that would have prevented in-app updates from working correctly on Linux and Pi deployments. The update helper script, release archive format, and artifact naming are now aligned end to end. Windows silent updates and rollback were also hardened.

## Macro and Trigger Reliability

- Macros called from two different triggers at the same time no longer incorrectly block each other as "circular." Each execution chain now tracks its own call stack independently.
- Cancel groups (mutual preemption) now work correctly when two macros in the same group start simultaneously. Previously both could run.
- Startup triggers no longer silently fail to fire due to the task reference being garbage collected.
- Disabling a trigger while a macro is queued now correctly prevents it from firing when the queue drains.

## Serial and UDP Device Communication

Serial and UDP transports now hold the send lock through the full send-and-wait cycle, matching TCP. Previously, a polling query could interleave between a command send and its response, returning the wrong data to the caller. This fixes intermittent wrong state values on serial devices (RS-232 projectors, displays, DSPs) under load.

## Device Reconnection

Manual reconnect no longer causes a spurious double-connect. Previously, disconnecting a device for reconnection triggered the auto-reconnect handler, which would tear down the newly established connection two seconds later.

## Cloud State Sync

When many devices reconnect at once, the cloud dashboard no longer shows stale values. The state relay now keeps the latest value per key instead of truncating the oldest entries. Batches that exceed the size limit are sent in chunks instead of being dropped.

## Project Migration

Upgrading project files from v0.3.0 to v0.4.0 now correctly converts per-device group assignments into device group entries. Previously, group assignments were silently dropped during migration.
