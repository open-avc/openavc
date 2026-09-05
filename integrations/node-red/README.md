# OpenAVC nodes for Node-RED

Use Node-RED as the logic behind an [OpenAVC](https://openavc.com) space. OpenAVC keeps the devices, the state and the touch panels; the flow decides what happens.

A panel button sets a variable, the flow sees it within 50 ms, does whatever it does (checks a calendar, an occupancy sensor, a building system), sends device commands and writes status back, and the panel re-renders from state. The panel never knows where the logic lives.

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

Import **Import › Examples › @openavc/node-red-openavc › logic-engine** for a working flow to start from.

The full guide is in the OpenAVC docs: [docs.openavc.com/node-red](https://docs.openavc.com/node-red/).

## License

MIT
