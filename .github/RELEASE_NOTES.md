## Device Discovery

Fixed the network scanner missing open ports on projectors, DSPs, and other embedded AV devices. The port scanner was opening too many simultaneous TCP connections, overwhelming devices with limited network stacks. PJLink projectors were the most common casualty. Discovery now throttles connections per host and reliably finds all open ports.

## UI Builder

- Fixed drag-and-drop placing elements to the right of where you drop them. Elements now land where the preview shows during the drag, both when adding new elements from the palette and when moving existing elements on the canvas.
- Fixed button icons disappearing when visual feedback updates the button text

## Panel

- Fixed the clock element not updating when switching between Time, Date, and DateTime modes
