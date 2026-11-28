// GENERATED FILE - DO NOT EDIT.
// Rendered from the platform's device keys (core/device_info.PLATFORM_DEVICE_KEYS
// and drivers/spec.CHILD_RESERVED_PROPS).
// Regenerate with:  python -m openavc.core.device_keys_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `DEVICE_PLATFORM_PROPS` is every `device.<id>.<key>` the platform writes for
// a device, and `CHILD_PLATFORM_PROPS` every `device.<id>.<type>.<local>.<key>`
// it writes for a child entity. They describe the device rather than come from
// it, so a control bound to one keeps drawing while the device is away.

export const DEVICE_PLATFORM_PROPS = new Set([
    "asleep",
    "connected",
    "enabled",
    "name",
    "offline_detail",
    "offline_reason",
    "orphan_reason",
    "orphaned",
    "paused",
    "paused_for",
    "presence",
    "reconnect_attempt",
    "reconnect_failed",
    "restarting",
    "wake_command",
    "web_ui_url",
]);
export const CHILD_PLATFORM_PROPS = new Set([
    "label",
    "offline_detail",
    "offline_reason",
    "online",
]);
