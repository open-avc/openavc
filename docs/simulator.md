# Device Simulator

The Device Simulator lets you develop and test projects without real AV hardware. It runs virtual devices on your local machine that respond to commands just like real projectors, displays, switchers, DSPs, and cameras.

## Starting Simulation

### From the Programmer IDE

Click the **play button** at the bottom of the sidebar. A confirmation dialog explains what will happen, then:

1. The simulator starts with all devices from your project
2. Device connections redirect to the simulated devices
3. The Simulator UI opens in a new browser tab
4. Your drivers reconnect and start talking to virtual hardware

Click the **stop button** (same location) to stop simulation and restore real device connections.

### Standalone

You can also run the simulator directly from the command line:

```bash
python -m simulator --driver-paths path/to/openavc-drivers
```

Or with a configuration file:

```bash
python -m simulator --config sim_config.json
```

**Config file format:**

```json
{
  "driver_paths": ["path/to/openavc-drivers"],
  "devices": [
    { "device_id": "projector_1", "driver_id": "pjlink_class1" },
    { "device_id": "switcher_1", "driver_id": "extron_sis" },
    { "device_id": "display_1", "driver_id": "lg_sicp" }
  ]
}
```

The Simulator UI is available at `http://localhost:19500`.

## Simulator UI

The Simulator UI shows your virtual devices and lets you interact with them from the "hardware side" — as if you were pressing buttons on the actual equipment.

**Device cards** show state and provide interactive controls based on the device category:

- **Projectors** — Power LED (animates during warmup), input selector, lamp hours
- **Displays** — Screen visual, power, input, volume slider, mute
- **Switchers** — Input port grid with active route, signal indicator, volume, mute
- **Audio/DSPs** — Level meters, level slider, mute
- **Cameras** — Tally light, zoom slider, power

**Children** — a device whose driver declares child entities (a matrix's outputs, an amp's zones) gets a collapsible Children section listing each child with its own state values. Edit a value to change that child's state directly; drivers polling per-child status pick the change up on the next cycle.

**Protocol log** at the bottom shows raw protocol traffic between your drivers and the simulated devices, with timestamps and direction indicators.

**Network conditions** dropdown in the header lets you simulate degraded network connections (latency, packet drops, jitter) using presets from "Perfect" to "Barely Working."

**Error injection** on each device card lets you toggle error conditions (communication timeout, corrupted data, device-specific errors) to test how your macros and triggers handle failures.

## How It Works

The simulator runs real protocol servers -- a simulated PJLink projector runs a TCP server that speaks the actual PJLink protocol. Your driver connects to `localhost` instead of the real device IP and talks the same protocol. From the driver's perspective, it's real hardware.

When you change state from the Simulator UI (adjusting a slider, toggling a button, etc.), simulators with `push_state: true` in their driver definition push the update to connected drivers immediately. This matches real devices that send unsolicited updates (verbose mode, subscriptions, notifications). Your driver picks up the change through its existing response matchers, the same way it would from real hardware. Drivers without `push_state` are poll-only, matching devices where the driver must query for state. HTTP simulators are poll-based unless the driver declares an SSE or HTTP-listener push block.

Drivers that declare a `push:` block (devices that deliver notifications on a channel of their own rather than the control connection) are simulated faithfully too. With a multicast push block, the simulator emits the driver's notification templates to the same group and port. With an SSE push block, the simulated HTTP device serves the declared event-stream paths and delivers the templates to subscribers. With a TCP-listener push block, the simulated device plays the dial-back loop the real one does: it accepts the driver's registration, then opens a connection back to the driver's listener port for each notification, framed exactly as the real device frames it. With an HTTP-listener push block, the simulated device posts the templates to the callback URL the driver registered on it, webhook style. In every case, push updates flow end to end exactly as they would from the real device.

When you add or remove devices while simulation is active, the simulator automatically syncs -- new devices get simulated, removed devices are cleaned up. No restart needed.

## Which Drivers Support Simulation

All YAML drivers (`.avcdriver`) get basic simulation automatically — the simulator reverses their command and response definitions to generate protocol handlers. This handles simple request/response patterns out of the box. For more realistic behavior (correct state tracking, conditional responses, realistic delays), add a `simulator:` section to your `.avcdriver` file. All community drivers include this section.

Python drivers need a companion simulator file (`_sim.py`) placed alongside the driver.

Check the Browse Drivers view for the simulator badge (play icon) to see which community drivers include enhanced simulation support.

For information on adding simulation support to your own drivers, see the Writing Simulators guide in the driver repository documentation.

## API Reference

The simulator exposes a REST API at `http://localhost:19500/api/`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Overall simulator status |
| `/api/available` | GET | Discovered drivers |
| `/api/devices` | GET | Running simulator instances |
| `/api/devices/{id}` | GET | Single device state |
| `/api/devices/{id}/start` | POST | Start simulating a device |
| `/api/devices/{id}/stop` | POST | Stop simulating a device |
| `/api/devices/{id}/state` | POST | Change device state |
| `/api/devices/{id}/errors/{mode}` | POST | Inject or clear an error |
| `/api/devices/{id}/log` | GET | Protocol log for a device |
| `/api/network` | GET/POST | Network condition settings |
| `/api/network/preset` | POST | Apply a named preset |
| `/api/shutdown` | POST | Shut down the simulator process |
| `/ws` | WebSocket | Real-time state and protocol updates |

## See Also

- [Getting Started](getting-started.md). Installation and first steps with the simulator
- [Devices and Drivers](devices-and-drivers.md). Adding equipment and managing drivers
- [Creating Drivers](creating-drivers.md). Building device drivers with simulation support
