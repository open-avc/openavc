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
            "renderer_url": "index.html",
            "default_size": {"col_span": 3, "row_span": 2},
            "config_schema": [
                {
                    "key": "title",
                    "label": "Title",
                    "type": "text",
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
| `type` | Yes | Unique element type name within this plugin |
| `label` | Yes | Human-readable name shown in the Element Palette |
| `renderer` | Yes | Always `"iframe"` |
| `renderer_url` | Yes | Path to the HTML file (relative to the plugin's `panel/` directory) |
| `default_size` | Yes | Default grid size when dragged onto the canvas: `{"col_span": N, "row_span": N}` |
| `config_schema` | No | Array of configuration fields for the IDE Properties panel (same field types as plugin config: text, number, toggle, select, state_key, macro, device) |

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

### postMessage API

The panel and the plugin iframe communicate through `window.postMessage`. All messages are JSON objects with a `type` field.

**Panel to iframe (incoming messages):**

| Message Type | When Sent | Payload |
|-------------|-----------|---------|
| `openavc:init` | Once, when the iframe loads | `{config, theme, state}`: initial configuration, current theme variables, and initial state values |
| `openavc:state` | On every state change | `{key, value}`: the state key that changed and its new value |
| `openavc:theme` | When the panel theme changes | `{variables}`: full set of theme CSS variables |

**iframe to panel (outgoing messages):**

| Message Type | Purpose | Payload |
|-------------|---------|---------|
| `openavc:command` | Send a device command | `{device, command, params}` |
| `openavc:set_state` | Write a state value | `{key, value}` |
| `openavc:navigate` | Navigate to a page | `{page}` |

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
                    document.body.style.backgroundColor = msg.theme?.panel_bg || '#1a1a2e';
                    if (config.state_key && msg.state?.[config.state_key] !== undefined) {
                        document.getElementById('value').textContent = msg.state[config.state_key];
                    }
                    break;

                case 'openavc:state':
                    if (msg.key === config.state_key) {
                        document.getElementById('value').textContent = msg.value;
                    }
                    break;

                case 'openavc:theme':
                    document.body.style.backgroundColor = msg.variables?.panel_bg || '#1a1a2e';
                    break;
            }
        });
    </script>
</body>
</html>
```

### Security

Plugin iframes are sandboxed with `allow-scripts allow-same-origin`. This means:

- The iframe can run JavaScript and make network requests to the same origin
- The iframe **cannot** access the parent panel DOM
- The iframe **cannot** navigate the parent page
- The iframe **cannot** open popups

All interaction with the panel goes through the postMessage API. This prevents a plugin from accidentally or intentionally breaking the panel interface.

---

## Contributing Plugins

For the full guide on plugin structure, manifest format, testing, and submitting to the community repository, see the [Contributing Plugins](https://github.com/open-avc/openavc-plugins/blob/main/docs/contributing-plugins.md) guide.

---

## See Also

- [Plugins](plugins.md). Installing and configuring plugins as an end user
- [Creating Drivers](creating-drivers.md). Building device drivers
- [Scripting API Reference](scripting-api-reference.md). The `openavc` module reference
