# Scheduling Guide

Automate time-based actions like nightly shutdowns, morning startup sequences, and periodic status checks.

## Trigger-Based Schedules

The primary way to schedule actions in OpenAVC is with a **schedule trigger** on a macro. Create a macro with the steps you want to run, then add a trigger with type "Schedule" and a cron expression.

### Example: Nightly Shutdown

1. Click **Macros** in the sidebar
2. Create a macro called `nightly_shutdown` with steps to power off devices
3. Click the **Triggers** tab in the macro editor
4. Click **Add Trigger**, select type **Schedule**
5. Use the visual cron builder or type a cron expression: `0 22 * * 1-5` (10 PM weekdays)
6. Optionally add guard conditions (e.g., only fire if `var.room_active` equals `true`)

Schedule triggers support all the same safety features as other triggers:

- **Guard conditions**: only fire when state matches (e.g., skip if room is already off)
- **Cooldown, debounce, and delay-with-recheck**: prevent duplicate execution
- **Overlap control**: `skip`, `queue`, or `allow` concurrent executions
- **Circular trigger chain detection**: prevents infinite loops
- **Visible in the Triggers API**: `GET /api/triggers`

### Cron Builder

The Programmer IDE includes a visual cron builder with two modes:

- **Field-by-field editor**: separate inputs for minute, hour, day, month, and weekday with labels
- **Raw expression editor**: type a standard cron expression directly

A dropdown of common examples is available:

| Example | Cron Expression |
|---------|----------------|
| Every weekday at 8 AM | `0 8 * * 1-5` |
| Every day at 10 PM | `0 22 * * *` |
| Every 15 minutes during business hours | `*/15 8-17 * * 1-5` |
| First Monday of each month | `0 9 1-7 * 1` |
| Every hour | `0 * * * *` |

## Cron Syntax

Standard 5-field cron expressions (minute, hour, day-of-month, month, day-of-week).

| Field        | Values    | Example       |
|-------------|-----------|---------------|
| Minute      | 0-59      | `*/15` (every 15 min) |
| Hour        | 0-23      | `8` (8 AM)    |
| Day of month| 1-31      | `1` (first)   |
| Month       | 1-12      | `*` (every)   |
| Day of week | 0-6 (Sun=0) | `1-5` (Mon-Fri) |

**Operators:**

| Operator | Example | Meaning |
|----------|---------|---------|
| `*` | `* * * * *` | Every value |
| `,` | `0,30 * * * *` | Multiple values (0 and 30) |
| `-` | `1-5` | Range (Monday through Friday) |
| `/` | `*/15` | Step (every 15) |

> **Precision note:** Schedule triggers are checked every 30 seconds. For most AV scheduling (startup/shutdown at a specific hour), this is more than sufficient. If you need sub-minute precision, use script timers instead.

## Running a Script on a Schedule

Schedule triggers execute macros, not scripts directly. To run a script function on a schedule:

1. Create a macro with an **Emit Event** step that fires a custom event (e.g., `custom.hourly_check`)
2. In your script, use `@on_event("custom.hourly_check")` to handle it
3. Add a **Schedule** trigger to the macro with your cron expression

This keeps all scheduling visible in one place (the Triggers tab) rather than split between macros and scripts.

## Script Timers

For delays and intervals inside scripts, use the timer API. These are useful for sub-minute intervals, countdowns, and polling patterns that don't fit a cron schedule.

```python
from openavc import after, every, cancel_timer, cancel_all_timers, delay

# One-shot timer (runs once after 5 seconds)
timer_id = after(5.0, my_callback)

# Repeating timer (runs every 30 seconds)
timer_id = every(30.0, poll_sensor)

# Cancel a specific timer
cancel_timer(timer_id)

# Cancel all timers at once
cancel_all_timers()

# Async delay inside a handler (pauses current handler)
await delay(2.0)
```

**When to use what:**

| Need | Use |
|------|-----|
| Run a macro at a specific time of day | Schedule trigger (cron) |
| Check a sensor every 30 seconds | Script timer (`every()`) |
| Wait between commands in a sequence | `await delay()` in a script, or Delay step in a macro |
| One-time delayed action | Script timer (`after()`) |

## See Also

- [Macros and Triggers](macros-and-triggers.md). Full trigger documentation including debounce, cooldown, and guard conditions.
- [Scripting Guide](scripting-guide.md). Complete scripting API including timer functions.
- [Scripting API Reference](scripting-api-reference.md). Quick lookup for timer function signatures.
