# Scheduling Guide

Automate time-based actions like nightly shutdowns, morning startup sequences, and periodic status checks.

## Trigger-Based Schedules

Add a trigger with `"type": "schedule"` to any macro. The trigger fires the macro on a cron schedule.

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

Schedule triggers support all the same safety features as other triggers:

- Guard conditions: only fire when state matches
- Cooldown, debounce, and delay-with-recheck
- Overlap control (`skip`, `queue`, `allow`)
- Circular trigger chain detection
- Visible in the Triggers API (`GET /api/triggers`)

In the Programmer IDE, use the visual cron builder to configure schedules without memorizing cron syntax.

## Cron Syntax

Standard 5-field cron expressions (minute, hour, day-of-month, month, day-of-week).

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
