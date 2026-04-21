## Theme Studio

The theme editor has been completely rebuilt as Theme Studio, a full-screen workspace for designing panel themes. A live-scaled preview shows every element type as you edit, so you see exactly what the panel will look like without switching back and forth.

- **Quick Adjust** controls for brand accent color, corner roundness, surface style, and typography let you reshape a theme in seconds.
- **Direct manipulation.** Click any element in the preview to jump straight to its settings.
- **Inheritance indicators.** Modified values are highlighted with a per-row reset button, so you always know what you've changed from the saved theme.
- **Contrast checker** flags readability issues between text and background colors.
- **Rich preset gallery** renders each built-in theme as a visual card showing its actual colors, fonts, and surface style.

Every panel element is now fully driven by CSS variables from the theme. No more hardcoded colors leaking through buttons, faders, level meters, matrices, or status LEDs.

## UI Builder: Live Panel Canvas

The design canvas in the UI Builder now runs the real panel renderer inside an iframe instead of a set of simplified React previews. What you see in the editor is what the panel actually looks like, down to the pixel. Edit mode, preview mode, and the live panel all render from the same code path.

This also means theme changes, element styling, and layout tweaks are immediately visible in the canvas without saving first.

## UI Builder: Outline Panel and Project Validation

A new **Outline** tab alongside the element palette gives you a searchable layers list with z-order controls and per-element locking. Lock an element to prevent accidental selection and dragging on a crowded page.

The toolbar now has a **Validate Project** button that scans for dangling references: devices that were removed but still bound to elements, macros that were deleted but still triggered by buttons, pages that no longer exist but are still referenced in navigation. Results are clickable and take you directly to the broken element.

## UI Builder Polish

- Multi-select copy/paste with Ctrl+C/V.
- Escape cancels in-progress resize and drag operations.
- Scoped undo with descriptions (settings, master elements, page groups, macros, variables all tracked separately).
- Autosave with Ctrl+S, manual save button, and a four-state save indicator.
- Inline element ID rename that automatically rewrites references across bindings, visibility conditions, master elements, macro steps, and trigger conditions.
- Alignment buttons work on multi-selections.
- Click palette items to add at the next free grid cell (not just drag-and-drop).
- Visibility conditions support AND/OR logic.
- Image elements get object-fit control (contain, cover, fill).
- New elements inherit their appearance from the active theme instead of showing hardcoded defaults.

## Dedicated Panel Support

The server-side pairing page and setup guides now cover dedicated panel deployment for wall-mounted tablets. Full Android and iOS guides walk through basic setup, full lockdown (Android Device Owner, iOS Guided Access, and Autonomous Single App Mode), fleet provisioning via QR, and troubleshooting.

The Android Panel app is built and available as a signed APK: mDNS auto-discovery, QR pairing, full-screen WebView panel, dedicated panel mode with Device Owner lockdown, boot-to-panel auto-start, and admin PIN exit flow. iOS is next.

## New Macro Step: wait_until

Macros can now pause execution until a condition is met. The `wait_until` step watches a state key and resumes when the value matches, with a configurable timeout and optional fallback steps if the condition is never satisfied. Useful for sequences that depend on a device reaching a certain state before continuing.

## Matrix and Plugin Improvements

- Matrix elements now have `show_lock` and `show_mute` toggles so designers can hide lock or mute buttons on a per-matrix basis.
- Plugin panel elements that declare a `config_schema` now get a typed form in the properties panel (text, number, boolean, select) instead of a raw JSON editor.

## Image Tinting and Frameless Buttons

Image elements support color tinting so a single icon asset can match any theme. Buttons can be set to frameless mode for transparent, icon-only tap targets.
