# Shared UI components

## Toast vs inline error — when to use which

**Inline (`<InlineError>`)** for errors tied to a specific action. Render
the error next to the button the user clicked. The user sees the cause and
the action together so they can correct and retry without scrolling or
hunting for context.

Examples:
- Uninstall failed because a device still references the driver.
- Save failed because a required field was empty.
- Test connection returned a protocol error.

**Toast (`showError` from `store/toastStore`)** for global or asynchronous
errors that aren't bound to the user's last click. The user may have moved
on; a transient toast is the right footprint.

Examples:
- WebSocket dropped.
- Background save failed.
- Cloud agent went offline.
- Update check failed in the background.

If you're not sure: tied to a button → inline. Happens on its own → toast.
