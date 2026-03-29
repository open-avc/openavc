# Scheduling Guide

Automate time-based actions like nightly shutdowns, morning startup sequences, and periodic status checks. OpenAVC has two scheduling mechanisms. This guide explains when to use each.

## Trigger-Based Schedules (Recommended)

Add a trigger with `"type": "schedule"` to any macro. This is the preferred approach for new projects.

```json
{
  "id": "lights_on",
  "name": "Morning Lights",
  "steps": [
    { "action": "device.command", "device": "lights1", "command": "on" }
  ],
  "triggers": [
    {
      "id": "trig_morning",
      "type": "schedule",
      "cron": "0 8 * * 1-5",
      "conditions": [
        { "key": "var.vacation_mode", "operator": "eq", "value": false }
      ]
    }
  ]
}
```

**Advantages over legacy schedules:**

- Guard conditions: only fire when state matches
- Cooldown, debounce, and delay-with-recheck
- Overlap control (`skip`, `queue`, `allow`)
- Circular trigger chain detection
- Visible in the Triggers API (`GET /api/triggers`)

## Legacy Schedules (project.avc `schedules` section)

The top-level `schedules` array in project.avc emits events on a cron schedule. These events can be caught by scripts or other triggers.

```json
{
  "schedules": [
    {
      "id": "morning_event",
      "type": "cron",
      "expression": "0 8 * * 1-5",
      "event": "schedule.morning",
      "enabled": true,
      "description": "Fires every weekday at 8 AM"
    }
  ]
}
```

Legacy schedules only emit events. They don't execute macros directly and have no condition or overlap controls.

## Migration

To migrate a legacy schedule to a trigger-based schedule:

1. Note the cron expression and event name from the legacy schedule entry
2. Create (or pick) the macro you want to run
3. Add a trigger to that macro:
   ```json
   {
     "id": "trig_<schedule_id>",
     "type": "schedule",
     "cron": "<expression>"
   }
   ```
4. Remove the legacy schedule entry from `schedules`
5. If scripts listen for the old event, add an `event.emit` step to the macro

## Cron Syntax

Both mechanisms use standard 5-field cron expressions (minute, hour, day-of-month, month, day-of-week). Requires the `croniter` package.

| Field        | Values    | Example       |
|-------------|-----------|---------------|
| Minute      | 0-59      | `*/15` (every 15 min) |
| Hour        | 0-23      | `8` (8 AM)    |
| Day of month| 1-31      | `1` (first)   |
| Month       | 1-12      | `*` (every)   |
| Day of week | 0-6 (Sun=0) | `1-5` (Mon-Fri) |

## Script Timers

For delays and intervals inside scripts, use the timer API:

```python
from openavc import after, every, cancel_timer, delay

# One-shot timer
timer_id = after(5.0, my_callback)

# Repeating timer
timer_id = every(30.0, poll_sensor)

# Cancel
cancel_timer(timer_id)

# Async delay inside a handler
await delay(2.0)
```
