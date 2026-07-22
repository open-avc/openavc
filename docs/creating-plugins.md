# Creating Plugins

Developer guide for building OpenAVC plugins. Plugins are Python packages that extend the platform with system-wide integrations, control surfaces, and services.

> For installing and using plugins as an end user, see [Plugins](plugins.md).

## Plugin Extensions

Plugins can contribute content to several places in the Programmer IDE and the touch panel.

### Plugin Views

Plugins can add their own views to the sidebar (below the standard sections). These might show a monitoring dashboard, a routing matrix, or a live state table. Plugin views appear automatically when the plugin is running.

### Status Cards

Plugins can add status cards to the Dashboard. These show key metrics (connection status, message counts, device counts) as compact cards alongside the standard system status.

### Device Panels

Plugins can add panels to the device detail page. These appear below the standard device sections and only show when relevant (e.g., a Dante plugin adds audio channel info to Dante-compatible devices).

### Context Actions

Plugins can add action buttons to toolbars. These might appear globally (e.g., "Scan Dante Network") or on specific device pages (e.g., "Identify on Dante"). Clicking a context action triggers the plugin to perform the action.

### Panel Elements

Plugins can provide custom panel elements that appear on the touch panel alongside built-in elements like buttons, labels, and faders. Panel elements are rendered in sandboxed iframes, giving the plugin full control over its visual presentation.

Use cases include video previews (NDI/IP camera), custom routing matrices, live audio meters, floor plan overlays, or any domain-specific widget the built-in elements don't cover.

Plugin panel elements appear in the Element Palette under a **Plugins** category. Drag them onto the canvas like any other element. In the Properties panel, you can configure the plugin-specific settings (e.g., source name, display mode) that the plugin author defined.

---

## Panel Element Development

Plugin panel elements use an **iframe-based renderer**. The plugin provides an HTML page that runs inside a sandboxed iframe on the touch panel. The panel and the iframe communicate through a `postMessage` API. The panel sends state updates and configuration to the iframe, and the iframe can send commands and state changes back.

This architecture keeps plugins fully isolated from the panel DOM. A plugin cannot break the panel layout or interfere with other elements.

### Defining Panel Elements

Panel elements are declared in the plugin's `EXTENSIONS` dictionary under the `panel_elements` key:

```python
EXTENSIONS = {
    "panel_elements": [
        {
            "type": "status_display",
            "label": "Status Display",
            "renderer": "iframe",
            "default_size": {"col_span": 3, "row_span": 2},
            "config_schema": [
                {
                    "key": "title",
                    "label": "Title",
                    "type": "string",
                    "default": "System Status"
                },
                {
                    "key": "state_key",
                    "label": "State Key",
                    "type": "state_key",
                    "default": ""
                },
                {
                    "key": "style",
                    "label": "Display Style",
                    "type": "select",
                    "options": ["compact", "detailed", "minimal"],
                    "default": "compact"
                }
            ]
        }
    ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Unique element type name within this plugin. **This also names the renderer file** — the panel loads `panel/<type>.html` (see note below). |
| `label` | Yes | Human-readable name shown in the Element Palette |
| `renderer` | Yes | Always `"iframe"` |
| `default_size` | Yes | Default grid size when dragged onto the canvas: `{"col_span": N, "row_span": N}`. The UI Builder uses this on drop and on click-to-add; if you omit it the element falls back to 4×3 cells. |
| `config_schema` | No | Array of configuration fields shown in the UI Builder Properties panel. Field types match plugin config (see "Config field types" below): `string`, `text`, `integer`, `float`, `boolean`, `select`, `state_key`, `device_ref`, `macro_ref`. See "Select options" below for static vs. dynamic dropdowns. |
| `sandbox_permissions` | No | Extra `iframe.sandbox` tokens beyond the default `allow-scripts`. See "Iframe Permissions" below for the whitelist. |
| `allow_features` | No | Permissions-Policy tokens applied via the iframe's `allow` attribute. See "Iframe Permissions" below for the whitelist. |

**Renderer file:** the panel loads `panel/<type>.html` for each element — an element with `type: "status_display"` loads `panel/status_display.html` from your plugin directory. Name the HTML file to match the element's `type`. (There is no separate URL field; the file name is the contract.)

**Config field types:** the Properties panel renders each `config_schema` field by its `type`, the same way the plugin CONFIG_SCHEMA form does:

| Type | Control |
|------|---------|
| `string` | Single-line text input |
| `text` | Multi-line textarea |
| `integer` / `float` | Number input |
| `boolean` | Checkbox |
| `select` | Dropdown (see "Select options" below) |
| `state_key` | State-key picker |
| `device_ref` | Device picker |
| `macro_ref` | Macro picker |

### Select Options

A `select` field can populate its dropdown from a static list or from runtime state.

**Static options** — fixed list set at declaration time:

```python
{"key": "fit", "label": "Fit", "type": "select",
 "options": ["contain", "cover"], "default": "contain"}
```

**Dynamic options from state** — list driven by a plugin state key. State values must be flat primitives, so the plugin publishes the option list as a JSON-encoded string of `[{"value": ..., "label": ...}, ...]`:

```python
{"key": "stream_id", "label": "Stream", "type": "select",
 "options_source": "plugin.my_plugin.stream_ids"}
```

And in the plugin code, when the list changes:

```python
self.api.set_state(
    "plugin.my_plugin.stream_ids",
    json.dumps([{"value": s.id, "label": s.name} for s in streams]),
)
```

The Properties panel re-renders the dropdown whenever the source state changes. If the currently-selected value isn't in the published list (e.g. the plugin hasn't started yet, or the user renamed the option), the stale value still shows as a selectable option so it doesn't silently switch. This mirrors the convention used by plugin macro action params.

### Iframe Permissions

By default, plugin iframes get only `sandbox="allow-scripts"` and no `allow` attribute. That's enough for most widgets — they can run JavaScript and call `postMessage` back to the panel — but it blocks anything that needs a same-origin context, autoplay-eligible media, or other delegated features. Two optional fields opt into extra permissions; the server filters both against whitelists, so anything outside these tables is silently dropped.

**`sandbox_permissions`** — extra iframe sandbox tokens:

| Token | What it enables |
|-------|-----------------|
| `allow-same-origin` | Iframe shares your origin; required for `fetch` against the OpenAVC server with session cookies, or for `localStorage` |
| `allow-forms` | Form submission |
| `allow-modals` | `alert()`, `confirm()`, `prompt()` |
| `allow-popups` | `window.open` |

Tokens that would let an iframe escape the sandbox (`allow-popups-to-escape-sandbox`, `allow-top-navigation`, `allow-pointer-lock`) are deliberately excluded.

**`allow_features`** — Permissions-Policy tokens:

| Token | What it enables |
|-------|-----------------|
| `autoplay` | `<video autoplay muted>` works without a user gesture |
| `encrypted-media` | EME (DRM playback) |
| `fullscreen` | `element.requestFullscreen()` |
| `picture-in-picture` | PiP API |

`camera`, `microphone`, `geolocation`, and similar sensor-access tokens are deliberately excluded — no v1 use case needs them, and they have non-obvious privacy implications.

Example: a video-streaming plugin needing same-origin fetches (to a WHEP signaling endpoint hosted on the same OpenAVC server) plus autoplay would declare:

```python
"sandbox_permissions": ["allow-same-origin"],
"allow_features": ["autoplay"]
```

Stay minimal — only request what you need. Unknown tokens log a warning in the system log so plugin authors can diagnose typos.

### File Structure

Plugin panel files (HTML, CSS, JavaScript, images) are served from:

```
/api/plugins/{plugin_id}/panel/{file_path}
```

Place your panel files in a `panel/` directory inside your plugin folder:

```
plugin_repo/
└── my_plugin/
    ├── __init__.py       # Plugin class
    └── panel/
        ├── index.html    # Main renderer page
        ├── style.css     # Styles
        └── app.js        # Logic
```

If your plugin's Python code spans several files, import the extra modules with a relative import (`from . import helpers` or `from .helpers import Thing`). Each plugin loads in its own isolated namespace, so a plain `import helpers` won't find a sibling file.

### postMessage API

The panel and the plugin iframe communicate through `window.postMessage`. All messages are JSON objects with a `type` field.

**Panel to iframe (incoming messages):**

| Message Type | When Sent | Payload |
|-------------|-----------|---------|
| `openavc:init` | When the iframe loads, and again whenever the iframe asks via `openavc:request-init` | `{config, theme, state, elementId, ext_token}`: the element's `plugin_config` values, the active theme's CSS variables, a snapshot of state keys in the plugin's namespace (`plugin.<plugin_id>.*`), this element's ID, and — for plugins that declare `ext_auth` — a token for calling the plugin's own `/ext/*` routes |
| `openavc:state` | When a key in the plugin's own namespace changes | `{key, value}`: the changed `plugin.<plugin_id>.*` key and its new value |

The init payload includes a snapshot of the plugin's own namespace (`plugin.<plugin_id>.*`) so the iframe can render its current state immediately. `openavc:state` updates are scoped to that same namespace — a plugin iframe sees only its own state, never other devices', variables', or other plugins' keys.

**iframe to panel (outgoing messages):**

Outgoing messages use `type: "openavc:action"` for both device commands and state writes, with an `action` field selecting the operation. Page navigation uses its own message type.

| Message Type | `action` | Purpose | Payload |
|-------------|----------|---------|---------|
| `openavc:action` | `device.command` | Send a device command | `{device, command, params}` |
| `openavc:action` | `state.set` | Write a state key | `{key, value}` |
| `openavc:navigate` | — | Navigate to a page | `{page}` |
| `openavc:request-init` | — | Ask the panel to re-send `openavc:init` with a freshly-minted `ext_token` — send it when an `/ext/*` call starts returning 401 mid-session (the token expired; panels often outlive the token lifetime) | — |

`openavc:action` requests are gated by the plugin's declared `capabilities`, mirroring the server-side checks for Python plugins: `device.command` requires `device_command`; `state.set` to a `plugin.<plugin_id>.*` key requires `state_write`; `state.set` to a `var.*` key requires `variable_write`. Writes to `device.*`, `system.*`, `isc.*`, `ui.*`, another plugin's namespace, or any action the plugin didn't declare a capability for are dropped.

### Example: Custom Status Display

A minimal panel element that shows a state value with a colored background:

**panel/index.html:**
```html
<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            margin: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: Inter, sans-serif;
            color: #fff;
            height: 100vh;
        }
        .status {
            text-align: center;
        }
        .title { font-size: 12px; opacity: 0.7; }
        .value { font-size: 24px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="status">
        <div class="title" id="title">Status</div>
        <div class="value" id="value">--</div>
    </div>
    <script>
        let config = {};

        window.addEventListener('message', (event) => {
            const msg = event.data;
            switch (msg.type) {
                case 'openavc:init':
                    config = msg.config || {};
                    document.getElementById('title').textContent = config.title || 'Status';
                    document.body.style.backgroundColor = msg.theme?.['--panel-bg'] || '#1a1a2e';
                    break;

                case 'openavc:state':
                    if (msg.key === config.state_key) {
                        document.getElementById('value').textContent = msg.value;
                    }
                    break;
            }
        });
    </script>
</body>
</html>
```

### Security

Plugin iframes are sandboxed with `allow-scripts`. This gives the iframe an opaque origin, which means:

- The iframe can run JavaScript
- The iframe **cannot** access the parent panel DOM
- The iframe **cannot** read or write cookies, localStorage, or any same-origin resources
- The iframe **cannot** navigate the parent page
- The iframe **cannot** open popups
- Network requests are subject to CORS like any cross-origin request

All interaction with the panel goes through the postMessage API. This prevents a plugin from accidentally or intentionally breaking the panel interface.

---

## Contributing Plugins

For the full guide on plugin structure, manifest format, testing, and submitting to the community repository, see the [Contributing Plugins](https://github.com/open-avc/openavc-plugins/blob/main/docs/contributing-plugins.md) guide.

---

## See Also

- [Plugins](plugins.md). Installing and configuring plugins as an end user
- [Creating Drivers](creating-drivers.md). Building device drivers
- [Scripting API Reference](scripting-api-reference.md). The `openavc` module reference
