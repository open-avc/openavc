# UI Builder

Design touch panel pages for your AV spaces using the visual UI Builder.

> The UI Builder is the OpenAVC equivalent of Crestron VT Pro or Extron GUI Designer, but running in your browser.

## Layout

**Left panel: Element Palette.** Drag elements onto the canvas:
- **Controls**: Button, Slider, Select, Text Input
- **Display**: Label, Status LED, Image, Spacer
- **Navigation**: Page Nav, Camera Preset

**Center panel: Canvas.** Visual representation of the panel page:
- Grid overlay shows the layout grid
- Drag elements to reposition
- Drag corners to resize
- Page tabs at the top for multi-page designs
- Screen preset selector (7" Tablet, 10" Tablet, iPad, 1080p)
- Preview mode toggle to test interactions with live state

**Right panel: Properties.** Configure the selected element:
- Basic properties (ID, type, label)
- Layout (grid position and span)
- Style (colors, font size, border radius)
- Bindings (the critical programming section, where logic meets the UI)

## Element Types

| Type | Purpose | Typical Use |
|------|---------|-------------|
| **Button** | Press/release events with visual feedback | Power on, source select, volume up |
| **Label** | Text display, can bind to state for dynamic content | Room name, projector status, current source |
| **Slider** | Range input (sends value on change) | Volume, lighting level, shade position |
| **Select** | Dropdown selector | Input selection, preset recall |
| **Status LED** | Colored indicator mapped to state values | Projector warming, system active, mute indicator |
| **Page Nav** | Button that navigates to another page | "Advanced", "Lighting", "Camera" |
| **Text Input** | Text entry field | IP address entry, room name |
| **Image** | Static image display | Logo, room diagram, floor plan |
| **Spacer** | Empty grid cell | Layout spacing and alignment |
| **Camera Preset** | Camera preset button with optional thumbnail | PTZ camera preset recall |
| **Gauge** | Circular arc meter with value binding | Temperature, signal level, volume position |
| **Level Meter** | Segmented bar (audio VU style) | Audio levels, signal strength |
| **Fader** | Mixing console style fader with handle | Audio volume, lighting level |
| **Group** | Visual frame with label (sits behind elements) | "Audio Controls", "Display Settings" section |
| **Clock** | Time, date, countdown, elapsed, meeting timer | Current time display, meeting countdown |
| **Keypad** | Numeric 0-9 pad with display | TV channel entry, passcode input |
| **List** | Scrollable list (static, selectable, multi-select, action) | Source list, room schedule, preset recall |
| **Matrix** | Crosspoint or dropdown routing matrix | Video/audio switcher routing |

## Pages

Click the **+** tab to add pages. Common patterns for AV rooms:

- **Main**: Primary controls (power, source select, volume)
- **Display**: Individual display controls, input routing
- **Audio**: Volume, mute, DSP presets, mic controls
- **Lighting**: Lighting presets and manual level control
- **Camera**: PTZ camera presets, directional controls
- **Advanced**: Technical controls, diagnostics, IP info

Most rooms need 2-4 pages. Start with a Main page that handles the 80% use case, then add pages for less common tasks.

## Overlays & Sidebars

Click the **+** dropdown to create an **Overlay** or **Sidebar** page:

- **Overlay**: Floats centered on top of the current page with a dim/blur backdrop. Use for confirmation dialogs, settings panels, PIN entry.
- **Sidebar**: Slides in from the left or right edge. Use for settings drawers, advanced options.

Navigate to an overlay the same way as any page (page_nav target or button navigate action). The current page stays visible underneath. Use `$back` as the target page to dismiss the overlay and return to the page below.

Overlay/sidebar properties (width, height, position, backdrop, animation) are editable in the properties panel when the overlay page is selected and no element is selected.

## Bindings

Bindings wire UI elements to actions and state. This is where most of the programming happens. It replaces the signal routing you would do in SIMPL or GC.

### Press Binding (buttons)

What happens when the button is pressed. Five action types are available:

- **Run Macro**: execute a named macro (best for multi-step sequences)
- **Device Command**: send a command directly (pick device, command, params)
- **Set Variable**: set a user variable value
- **Navigate to Page**: switch to another page
- **Script Function**: call a Python function

Example: A "Laptop" source button might run a macro called `select_laptop` that switches the matrix, sets the scaler input, and updates a variable tracking the current source.

### Button Modes

Each button has a **mode** that controls how presses are handled:

| Mode | Behavior | Use Case |
|------|----------|----------|
| **Tap** | Fires action on press (default) | Most buttons: source select, power on |
| **Toggle** | Fires On Action or Off Action based on current state | Power on/off, mute/unmute |
| **Hold Repeat** | Fires action repeatedly while held at a configurable interval | Volume ramp, camera pan/tilt |
| **Tap / Hold** | Short press fires tap action, long press fires long press action | Quick action vs advanced action |

**Toggle** is state-aware. You pick a state key (any variable or device property), and the button reads it to decide which action to fire. If the state says "off," pressing fires the On Action. If "on," pressing fires the Off Action. You can also set **On Label** and **Off Label** so the button text changes automatically. Toggle works on both web panel buttons and physical control surfaces (Stream Deck).

Hold Repeat has a configurable repeat interval (default 200ms). Tap/Hold has a configurable threshold (default 500ms). Presses shorter than the threshold are taps, longer are long presses.

### Text Binding (labels)

What text to display:

- **Static**: fixed text like "Main Projector"
- **State Variable**: bind to a state key so the label updates in real time

Example: Bind a label to `device.projector_main.lamp_hours` to show the current lamp hours without any programming.

### Feedback Binding (buttons)

Visual feedback based on state. This is how buttons light up to show the current selection, the equivalent of feedback joins in Crestron.

- **Source**: pick a category (Variables, Devices, Plugins, System) then the specific state key
- **Condition**: when the state key equals a value, the button is "active." For boolean keys you get an ON/OFF toggle; for string keys you get a dropdown of known values.
- **Active appearance**: background color, text color, and optional label text when the condition is true
- **Inactive appearance**: background color, text color, and optional label text when the condition is false
- **Live preview**: the editor shows the current value and whether the condition is active or inactive right now

**Conditional labels** let the button text change based on state. For example, a power button can show "ON" with a green background when the projector is on, and "OFF" with a dark background when it's off.

Example: On the source select buttons, set feedback so that when `var.current_source` equals `"laptop"`, the Laptop button shows as highlighted and all others show as dimmed.

**Multi-State Feedback:** For devices with more than two states (e.g., projector power: on/off/warming/cooling), use multi-state feedback instead of a simple active/inactive condition. Define a state map where each value gets its own color, icon, and label. The editor shows a visual map builder. Add a row per state value, pick colors, and the button updates per state. This eliminates the need for scripts or complex variable bindings to handle transitional states like warming and cooling.

## Button Display Modes

Buttons support five display modes, selectable in the Properties panel:

| Mode | Shows |
|------|-------|
| **Text** | Label text only (default) |
| **Icon + Text** | Icon alongside the label |
| **Icon Only** | Icon with no text |
| **Image** | Background image fills the button |
| **Image + Text** | Background image with text overlay |

For image modes, upload assets using the Asset Picker and select a default image. Optionally set an **active image** that swaps in when feedback is active (e.g., a lit-up button graphic when the projector is on). The **Image Fit** control (`cover`, `contain`, `fill`) determines how the image scales within the button area.

### Direct UI Control from Macros and Scripts

In addition to feedback bindings, macros and scripts can directly control UI element appearance using `ui.*` state keys:

```
state.set → ui.btn_power.label → "WARMING..."
state.set → ui.btn_power.bg_color → #FFC107
state.set → ui.btn_power.text_color → #000000
state.set → ui.btn_power.visible → false
state.set → ui.btn_power.opacity → 0.5
```

These overrides take priority over feedback bindings. Use them when you need more control than the feedback condition provides, for example showing different text for each stage of a multi-step startup sequence.

### Color Binding (status LEDs)

Map state values to indicator colors:

```
device.projector_main.power:
  "on"      -> green (#4CAF50)
  "warming" -> amber (#FF9800)
  "cooling" -> amber (#FF9800)
  "off"     -> gray (#9E9E9E)
```

### Value/Slider Binding

- **Change binding**: what happens when the slider moves (send device command with `$value` as the parameter)
- **Value binding**: what state key drives the slider position (for two-way feedback)

Example: Bind a volume slider's change event to `devices.dsp_1.set_level` with `$value`, and bind its value to `device.dsp_1.output_level`. The slider sends level changes to the DSP and reflects the actual level reported back.

## Properties Panel

The right panel in the UI Builder configures the selected element. Beyond the basic properties and bindings described above, the Properties panel includes these sections:

### Icons

Buttons, labels, page nav, and camera preset elements support icons. Set these in the **Icon** section of the properties panel:

- **Icon**: choose a [Lucide](https://lucide.dev) icon name (e.g., `power`, `volume-2`, `monitor`)
- **Icon Position**: `left`, `right`, `top`, `bottom`, or `center` (center hides the label for icon-only buttons)
- **Icon Size**: pixel size of the icon
- **Icon Color**: icon color (inherits text color if not set)

The **Icon Picker** in the properties panel lets you search and preview Lucide icons visually.

### Style

The **Style** section has subsections for fine-grained control over element appearance:

- **Border**: width, color, and style (solid, dashed, dotted, etc.)
- **Shadow**: presets: `sm`, `md`, `lg`, `glow`, `inset`
- **Gradient**: two-color linear gradient (start color, end color, direction)
- **Padding**: all sides, or horizontal/vertical independently
- **Typography**: vertical alignment, text transform (uppercase, lowercase, capitalize), letter spacing, line height
- **Background Image**: select an image asset with controls for size, position, and opacity (see Asset Management below)
- **Overflow**: control how content that exceeds the element bounds is handled (visible, hidden, scroll)

### Asset Management

Projects can include uploaded image assets (PNG, JPG, SVG, etc.) stored in the project's `assets/` directory. Use the **Asset Picker** in the Background Image style section to upload and select images.

- Assets are referenced as `assets://filename` in the project file
- The Asset Picker shows thumbnails of all uploaded images
- Assets are included automatically when you export a project as `.zip`

## Preview Mode

Toggle preview mode (button at the top of the canvas) to hide the grid overlay and test your panel with live device state. Button presses send real commands, sliders move real faders, and state feedback updates in real time. Use this to verify your bindings before deploying to a production touch panel.

## Panel Settings

Click the gear icon in the UI Builder toolbar to open Panel Settings:
- **Theme**: select a theme from the Theme Picker (see below)
- **Accent Color**: primary color used for buttons and highlights
- **Font**: panel font family
- **Lock Code**: optional PIN to prevent unauthorized access on a deployed panel
- **Idle Timeout**: seconds of inactivity before the panel returns to the idle page
- **Idle Page**: which page to display when the idle timeout triggers
- **Orientation**: landscape or portrait layout

## Themes

The Theme Picker in Panel Settings shows visual cards with color swatches for each available theme. OpenAVC ships with 8 built-in themes:

| Theme | Style |
|-------|-------|
| **Dark Default** | Standard dark theme with blue accent |
| **Midnight Blue** | Deep navy with bright accents |
| **Warm Charcoal** | Warm dark tones for residential settings |
| **Light Modern** | Clean light theme for well-lit spaces |
| **High Contrast** | WCAG-compliant with bold colors for accessibility |
| **Slate** | Neutral gray tones for corporate environments |
| **Luxury** | Rich dark tones for high-end installations |
| **Minimal** | Stripped-back design with subtle colors |

Select a theme to apply it immediately. The canvas preview updates in real time.

**Theme overrides** let you tweak individual variables (background, accent, text color, etc.) without creating a full custom theme. Click **Customize** on any theme card to adjust specific values while keeping the rest of the theme intact.

Custom themes can be created, exported as `.avctheme` files, and shared across projects or installations.

## Master Elements

Master elements persist across page changes. Use them for elements that should always be visible regardless of which page the user is on: a company logo, a navigation bar, a clock, or a status indicator row.

To create a master element, select an element and click **Make Master** in the properties panel (or right-click the element and choose **Make Master**). Master elements render below page elements, so page content appears on top.

Each master element has a **Pages** filter that controls where it appears:

- **All pages** (`"*"`): the element shows on every page, including overlays
- **Specific pages**: select which pages the element should appear on (e.g., only the main control pages, not the settings page)

Common use cases:

| Master Element | Pages | Purpose |
|----------------|-------|---------|
| Company logo | All pages | Branding in the corner |
| Navigation bar | All pages | Page_nav buttons at the bottom |
| Clock | All pages | Current time display |
| Room name label | Control pages only | Hide on settings/advanced pages |
| Status indicator row | All pages | Connection status LEDs |

## Page Groups

When a project has many pages, the page tabs at the top of the canvas can get crowded. Page groups let you organize pages into collapsible sections in the builder toolbar.

To create a group, click the folder icon in the page tab bar and give the group a name (e.g., "Control", "Settings", "AV Routing"). Then drag page tabs into the group. Groups are purely organizational and do not affect the panel at runtime. Collapse a group to hide its pages while you work on a different section.

## Alignment Tools

The builder toolbar includes 6 alignment buttons for precise element placement:

| Button | Action |
|--------|--------|
| Align Left | Snap the element's left edge to the nearest grid column |
| Align Center (H) | Center the element horizontally on the page grid |
| Align Right | Snap the element's right edge to the nearest grid column |
| Align Top | Snap the element's top edge to the nearest grid row |
| Align Middle (V) | Center the element vertically on the page grid |
| Align Bottom | Snap the element's bottom edge to the nearest grid row |

Select an element on the canvas, then click any alignment button. Alignment is relative to the full page grid.

## Element Templates

The Element Palette includes a **Templates** section with pre-built element combos. Templates insert multiple coordinated elements at once, saving time on common AV control patterns.

| Template | Elements Created | Typical Use |
|----------|-----------------|-------------|
| **Volume Control** | Fader + Mute button + "Volume" label | Audio zone control |
| **Source Selector** | 4 input buttons (HDMI 1-4) | Switcher input selection |
| **Power Toggle** | Power button + Status LED | Projector or display power |
| **Room Header** | Room name label + Clock | Top bar for any room panel |
| **Mixer Strip** | Level meter + Fader + Mute button + Channel label | Audio mixer channel |

Drag a template from the palette onto the canvas. The elements are placed together and pre-configured with sensible defaults. After placing, click each element to customize its bindings, labels, and colors for your specific devices.

## Page Transitions & Animations

Add visual polish to your panel with page transitions and element animations. Configure these in the **Panel Settings** dialog (gear icon in the toolbar).

**Page transitions** control how the panel animates when switching pages:

| Transition | Effect |
|------------|--------|
| `none` | Instant page switch (default) |
| `fade` | Cross-fade between pages |
| `slide-left` | Current page slides out left, new page slides in from right |
| `slide-right` | Current page slides out right, new page slides in from left |
| `slide-up` | Current page slides up, new page slides in from bottom |
| `scale` | Current page shrinks, new page grows in |

**Element entry animations** control how elements appear when a page loads:

| Animation | Effect |
|-----------|--------|
| `none` | Elements appear instantly (default) |
| `fade` | Elements fade in |
| `fade-up` | Elements fade in while sliding up slightly |
| `scale` | Elements grow from small to full size |
| `stagger` | Elements animate in one after another with a delay |

**Transition duration** (in ms) controls the speed of page transitions. **Stagger delay** (in ms) sets the interval between each element when using the stagger animation. Lower values make elements appear faster in sequence.

## Theme Editor

The **Theme Editor** tab in Panel Settings gives you full control over theme colors and element defaults.

**Color palette:** Edit every theme color variable: panel background, accent, button colors, status colors (success, danger, warning), surface colors, and text colors. Changes preview in real time on the canvas.

**Element defaults:** Customize default styles per element type. For example, set all buttons to have rounded corners and a subtle border, or make all labels transparent with no background. These defaults apply to elements that do not have explicit style overrides.

**Contrast checker:** A built-in WCAG accessibility checker evaluates text-on-background contrast for every color combination in the theme. It flags combinations that do not meet AA or AAA accessibility standards, helping you build panels that are readable for all users.

**Save as custom theme:** After editing, click **Save as Custom Theme** to store your changes as a new named theme. Custom themes appear alongside the built-in themes in the Theme Picker.

**Export/import:** Click **Export** to download the theme as an `.avctheme` file. Share it with colleagues or use it on other OpenAVC installations. Click **Import** to load an `.avctheme` file into the theme library.

## Page Backgrounds

Each page can have its own background color, image, and gradient overlay. Configure page backgrounds by clicking on the canvas with no element selected. The Properties panel shows the page-level settings.

| Property | Description |
|----------|-------------|
| **Background Color** | Solid color behind everything on the page |
| **Background Image** | An uploaded asset (use the Asset Picker to select) |
| **Image Opacity** | Reduce image opacity for readability (0.0 to 1.0) |
| **Gradient Overlay** | A two-color gradient rendered on top of the image |

A common pattern is to set a full-bleed background photo with reduced opacity and a dark gradient overlay. This creates an attractive background that does not interfere with button readability.

## Keyboard Shortcuts

Press **?** while in the UI Builder to see all available keyboard shortcuts. Common shortcuts include Ctrl+Z (undo), Ctrl+Y (redo), Ctrl+C (copy), Ctrl+V (paste), Delete (remove selected element), and arrow keys (nudge position).

## See Also

- [Programmer IDE Overview](programmer-overview.md). IDE layout, state concepts, and typical workflow.
- [Macros and Triggers](macros-and-triggers.md). Command sequences and automation conditions.
- [Variables and State](variables-and-state.md). User variables, device states, and activity monitoring.
