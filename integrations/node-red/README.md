# OpenAVC nodes for Node-RED

Use Node-RED as the logic behind an [OpenAVC](https://openavc.com) space. OpenAVC keeps the devices, the state and the touch panels; the flow decides what happens.

A panel button sets a variable, the flow sees it within 50 ms, does whatever it does (checks a calendar, an occupancy sensor, a building system), sends device commands and writes status back, and the panel re-renders from state. The panel never knows where the logic lives.

![The logic-engine example: a panel button writes a variable, the flow routes the switcher and writes the status back](https://raw.githubusercontent.com/open-avc/openavc/main/docs/images/node-red-logic-engine.png)

## Nodes

| Node | What it does |
|------|--------------|
| **openavc-server** (config) | One OpenAVC system. All the nodes on it share a single WebSocket that reconnects on its own. |
| **state in** | A message for every change to a matching state key (`var.*`, `device.projector.power`, ...). Optionally replays current values on connect. |
| **event in** | A message for every matching event (`custom.*`, `ui.press.*`, `device.disconnected.*`, ...). |
| **command** | Sends a device command and answers with whether the device accepted it. |
| **macro** | Runs a macro and answers when it has finished. |
| **set variable** | Writes a `var.*` value. Every panel control bound to it updates. |
| **emit event** | Emits a `custom.*` event, which fires an event trigger or a script's handler. |

Device, command, macro and key fields fill their lists from the system, so you pick rather than type.

## Install

In Node-RED: **Manage palette › Install › `@openavc/node-red-openavc`**, or in your Node-RED user directory:

```
npm install @openavc/node-red-openavc
```

Needs Node-RED 4.0 or later and OpenAVC 0.33 or later.

## Connect

Add an **openavc-server**, enter the host and port the Programmer opens on, and deploy. Leave the API key blank to connect as a panel (read all state, send commands, run macros, write `var.*`, emit `custom.*` events), or paste a key from the Programmer's Settings › Security to connect as the Programmer.

Give the server node a name under **Announce as** and OpenAVC publishes `system.integration.<name>.connected`, so a panel light or an alert can say when the flow is not there.

## Examples

Three working flows ship with the nodes, under **Import › Examples › @openavc/node-red-openavc** in the editor:

| Example | What it shows |
|---------|---------------|
| **logic-engine** | The pattern above: a panel button writes `var.request_source`, the flow routes the switcher and writes `var.status` back. Also watches `custom.*` and device events. |
| **fire-a-trigger** | The reverse: the flow decides a meeting started and emits `custom.meeting_started`; a macro with an Event trigger runs the sequence and reads `$trigger.organizer`. |
| **macros-do-the-sequences** | The flow decides *when*, OpenAVC's macros do the warm-up and shut-down. Runs `system_on` / `system_off` from `var.room_mode`, waits for them to finish, and a catch node puts a failure on the panel. Matches the Conference Room starter project. |

Each tab's description (the `i` panel) says what to set up on the OpenAVC side. Point the **openavc-server** node at your system and deploy.

The full guide is in the OpenAVC docs: [docs.openavc.com/node-red](https://docs.openavc.com/node-red/).

## License

MIT
