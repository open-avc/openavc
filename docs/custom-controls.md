# Writing a Custom Control

A custom control is a small web page you write yourself, running inside one element's box on a panel page, next to ordinary buttons and faders. HTML, CSS and JavaScript, nothing else to install.

Reach for one when the built-in controls cannot express what a space needs: a seating map you tap to select a zone, a diagram of the rack with live status on it, a vendor widget nobody has a driver for, a display of your own design.

For placing one and pointing it at a device, see [UI Builder](ui-builder.md#custom-controls). This page is about writing the page itself.

## Where the files live

Everything a control needs lives in the project, in a folder called `ui/`. It travels with the project: export it, back it up, restore it on another machine, and the control comes with it.

Three ways in, all writing to the same folder:

- Select the custom control on the page, then **Add files** in the properties panel. Pick files, a folder, or a `.zip`.
- Drop files onto the same control.
- Edit them in the IDE the way you edit a script.

A control can be one file or a folder of them:

```
ui/
  room_map/
    index.html
    map.css
    map.js
    floor.png
```

Point the element at `room_map/index.html`. Everything beside it loads with relative paths (`map.css`, not `/ui/room_map/map.css`).

Allowed file types are the ones a browser needs: HTML, CSS, JavaScript, images, fonts, JSON, and a few others. Anything else is refused when you upload it, and the message says which files were skipped.

**Anything in `ui/` is readable by anyone who can reach the server.** A wall panel presents no password, so these files are served without one. Keep credentials, customer data and anything else you would not hand out of this folder.

## The shape of a control

A control is a normal web page. It talks to the panel with `postMessage`.

```html
<!DOCTYPE html>
<html>
<body>
  <button id="lights">House Lights</button>
  <span id="level">--</span>

  <script>
    // The panel speaks first, once, when the page loads.
    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (msg.type === 'openavc:init') {
        // msg.config   your settings for this control
        // msg.theme    the panel's colors
        // msg.state    the state you were granted, right now
        // msg.grant    what this control is allowed to reach
        document.getElementById('level').textContent = msg.state['device.lights.level'] ?? '--';
      }
      if (msg.type === 'openavc:state' && msg.key === 'device.lights.level') {
        document.getElementById('level').textContent = msg.value;
      }
    });

    // And the control asks the panel to act.
    document.getElementById('lights').onclick = () => {
      parent.postMessage({
        type: 'openavc:action',
        action: 'device.command',
        device: 'lights',
        command: 'preset_1',
      }, '*');
    };
  </script>
</body>
</html>
```

## What the panel sends you

### `openavc:init`

Once, when your page loads.

| Field | What it holds |
|-------|---------------|
| `config` | The settings you typed into **Settings passed to the control** on the element, verbatim |
| `theme` | The panel's twelve theme variables, as a map of CSS variable name to value |
| `state` | Every state key this control was granted, with its current value |
| `grant` | What this control may reach: `{devices, variables, macros, navigate}` |
| `elementId` | This element's id, useful when the same page runs in more than one box |
| `edit` | `true` when the control is drawing in the UI Builder's design canvas rather than on a panel |

### `openavc:state`

Every time a granted value changes.

```javascript
if (msg.type === 'openavc:state') {
  // msg.key, msg.value
}
```

You only ever receive keys you were granted. A control granted the DSP hears about the DSP and nothing else.

## What you can send back

All four are `parent.postMessage(..., '*')`, and all four are refused unless the element's grant covers them.

**Command a device**

```javascript
parent.postMessage({
  type: 'openavc:action', action: 'device.command',
  device: 'projector_1', command: 'power_on', params: { source: 'hdmi1' },
}, '*');
```

**Set a variable**

```javascript
parent.postMessage({
  type: 'openavc:action', action: 'state.set',
  key: 'var.room_mode', value: 'presentation',
}, '*');
```

**Run a macro**

```javascript
parent.postMessage({ type: 'openavc:action', action: 'macro.run', macro: 'system_on' }, '*');
```

**Change the page**

```javascript
parent.postMessage({ type: 'openavc:navigate', page: 'lighting' }, '*');
```

`$back` and `$dismiss` work here the same as they do on a Page Nav button.

## What it is allowed to reach

Nothing, until you say otherwise. A control you place and do not configure draws, gets its config and its theme, and sees an empty `state`. Everything it sends is dropped.

You grant it access in the **Can reach** section of the properties panel: tick the devices and variables this control should have, and the two switches for running macros and changing pages. Ticking a device gives the control both directions at once, its state and its commands.

Two things worth knowing when you write against a grant:

- A grant on a device covers everything under it, including child entities like `device.dsp1.input.03.gain`. You do not have to list them.
- Read `msg.grant` at startup and adapt. A control that hides the button for a device it was not given is much easier to commission than one that looks fine and does nothing.

Refused messages are logged to the browser console (`[panel] custom control 'room_map' attempted ...`), which is where to look when a button does nothing.

Peer instance state (`isc.*`), system state (`system.*`) and panel state (`ui.*`) cannot be granted to a control.

## Seeing it while you build it

Your control draws for real on the Builder's design canvas, at the size you gave it and in the project's theme, so you can lay a page out around it. What it does not do there is touch the room: the canvas has no connection to it, so commands, variable writes, macros and page changes all stop at the panel. The state it receives on the canvas is a snapshot of what the room was last reporting, which is enough to see your readouts filled in.

`edit: true` in the opening message is how your page can tell. Use it to draw representative content when there is nothing live to show:

```javascript
if (msg.type === 'openavc:init') {
  const level = msg.edit ? 42 : (msg.state['device.lights.level'] ?? 0);
  draw(level);
}
```

**Preview** runs the control exactly as it will on the glass: live state, working commands, the real room. That is the one to trust before you hand a space over.

Saving a file into `ui/` redraws the control on the canvas. You do not need to reload the IDE.

## When something goes wrong, say so

A control runs in its own window, so nothing outside it can see a script error inside it. Report your own in one line and the panel shows it in the element's box, and in the IDE while you are building:

```javascript
window.onerror = (message) => {
  parent.postMessage({ type: 'openavc:error', message: String(message) }, '*');
};
```

Do that in every control you write. A control that throws without it is a blank rectangle, and on a wall panel there is no console to check.

The panel raises one failure on its own: if the file the element points at is not there, the box says so and names the file.

## Matching the panel's look

The theme arrives in `openavc:init` as the same twelve variables the project stylesheet uses. Set them on your own page and the control follows a theme switch instead of fighting it:

```javascript
if (msg.type === 'openavc:init') {
  for (const [name, value] of Object.entries(msg.theme)) {
    document.documentElement.style.setProperty(name, value);
  }
}
```

| Variable | What it holds |
|----------|---------------|
| `--panel-bg` | Page background |
| `--panel-text` | Default text color |
| `--panel-accent` | Accent color for active states and highlights |
| `--panel-button-bg` | Button background |
| `--panel-button-text` | Button text |
| `--panel-button-border` | Button border |
| `--panel-surface` | Surface color for tracks, inputs, and panels |
| `--panel-surface-border` | Surface border |
| `--panel-danger` | Danger or alarm color |
| `--panel-success` | Success or on color |
| `--panel-warning` | Warning color |
| `--panel-border-radius` | Default corner radius |

Your page fills the element's box exactly, so give it `margin: 0` and let it size from 100% width and height rather than fixed pixels. The box is whatever you drew in the Builder, and it changes with the panel's screen.

## Rules that keep working in a real space

- **Everything ships with the project.** No web fonts from Google, no library from a CDN, no remote images. A panel on a wall may have no internet at all, and anything remote renders as nothing. Put what you need in `ui/` beside your page.
- **Relative paths only.** `map.js`, never `http://192.168.1.50:8080/...`. An absolute address works on the local network and fails through the cloud tunnel and over HTTPS.
- **Keep it small and few-file.** Every file is a separate request, and through the cloud tunnel every request is a separate relay. A control is a widget, not a web app.
- **Draw for a finger.** There is no minimum size on a custom control, because we do not know what you drew. Anything a person taps wants about 9mm on the glass, the same as the built-in controls.
- **Your code runs in its own window.** It cannot reach the panel's page, its session or the other controls. If it throws, that one box stops working and the rest of the panel carries on.
- **Test on the real glass.** Tablet browsers are not desktop Chrome. Check anything unusual on the panel the space will actually use.

## When the control does not draw

- Read the message in the box. A missing file names itself, and so does anything your control reports through `openavc:error`.
- Check the **Control** dropdown on the element. A file that was renamed or removed shows as `(missing)`.
- Open the page on its own to see it in isolation: `http://<your-server>:8080/api/projects/default/ui/room_map/index.html`.
- Open the browser console on the panel. A refused action names itself there, and so does a script error in your page.

## Pointing your own web app at OpenAVC instead

A custom control is not the only way to write your own interface. Everything the panel does is available over the REST and WebSocket API with an API key, so you can build a separate web app and host it yourself.

What that buys you: any framework you like, any hosting, no constraints from the element box.

What you give up: the panel app and its kiosk mode, QR pairing, themes, the project's own pages, offline operation, and the cloud tunnel. You host it, you keep it running, and a space with no internet needs it on the local network.

For one custom screen inside a project you are already building, a custom control is less work. For a product of your own that happens to control AV, the API is the right door: set an API key (see [Deployment](deployment.md#authentication)) and send it in the `X-API-Key` header.
