# OpenAVC v0.16.0

Most of this release is control surface work. The Stream Deck integration got a
rebuilt editor, network-attached deck support, and full support for the dial
and touchscreen models. There is also a new controller network settings page,
a setup screen for dedicated displays, and a round of fixes.

## Stream Deck

Supported models: Mini, Mini MK.2, Original, MK.2, Neo, XL, XL V2, Plus,
Pedal, and Studio.

- The Stream Deck view in the Programmer renders the connected deck live. What
  the editor shows is what the hardware is showing. Click a key, dial, touch
  strip, or info screen to edit it in the inspector. Shift+click a key to press
  it for real. Switching page tabs switches the physical deck.
- Config changes apply live. Decks no longer blank or reconnect on save.
- Network decks. A deck can connect through an Elgato Network Dock (PoE or
  USB-C power), and the Stream Deck Studio connects over its built-in Ethernet
  port. Decks on the local network segment are discovered automatically;
  otherwise add by IP address. A network deck behaves exactly like a USB deck,
  and works from Docker and VM deployments where USB passthrough was not an
  option. Only decks you explicitly add are used.
- Dials adjust values with configurable step and range, and have separate push
  actions. The touch strip handles taps, long-presses, and swipe-to-adjust per
  zone.
- Live meters and value readouts can be placed on keys and screens. The info
  screen can show two live values, plus a clock when idle.
- Virtual decks. Build a layout with no hardware attached, then transfer it to
  a real deck later, including between different deck models.
- Locked keys hold the same position and assignment on every page. A key that
  navigates to a page lights up while that page is active, so a locked row of
  page keys works like tabs.
- Page names, page duplicate and clear, and arrange tools for copy, paste,
  move, and swap.
- Per-deck brightness, idle dimming rules, and default color settings.
- Macros can drive the deck (switch pages, flash a key), and keys show run
  feedback while their macro executes.

Update the Stream Deck plugin from the plugin browser to get all of this on an
existing system.

## Controller network settings

On Linux systems with NetworkManager, including the Raspberry Pi image, the
controller's IP configuration can be set from Programmer Settings: static IP,
gateway, and DNS. Validation stops you from saving a configuration that would
cut off your own session.

## Setup screen for dedicated displays

A controller driving a dedicated display shows a setup screen while the project
has no panel content: the address to browse to and how to get connected. Once
panel content loads, the display switches to the panel automatically.

## Fixes and improvements

- The Driver Builder flags command shapes that do not match the driver's
  transport and validates OSC argument values.
- Secret fields in device dialogs are masked correctly. The device detail view
  validates setting edits and fixes log tailing.
- UI Builder: fixed binding validation, duplicate references, and element id
  collisions.
- Convert to Script generates code that matches what the macro engine actually
  does, and scripts keep the macro call chain across the script boundary.
- The Updates view shows updates staged from the cloud, labels rollbacks
  clearly, and no longer leaves a stuck progress dialog.
- SNMP discovery is hardened against malformed device responses.
- Projects are stored in the data directory alongside drivers and plugins.
  Existing projects migrate automatically on first start.
