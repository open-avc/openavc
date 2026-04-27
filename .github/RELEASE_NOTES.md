## Branding and Theming

The Programmer IDE and Simulator now use the OpenAVC brand palette. The blue accent is replaced with the sage green from the logo across all views, buttons, and status indicators. Both apps show the OpenAVC favicon and logo. A dark/light theme toggle is available in System Settings.

## Add Device Dialog

Selecting a driver now auto-fills the Device ID and Display Name. IDs are generated from the driver category (e.g., `projector_1`, `display_2`) and names from the driver name, with automatic numbering to avoid conflicts. Both fields are editable if you want to override them. Driver and Device ID are now marked as required.

## Simulator

- Device cards with many controls (like the Behringer X32) no longer clip at the bottom. The controls area scrolls independently within each card.
- Closing the simulator browser tab now shuts down the simulator process after a 5-second grace period. The main server detects the exit and cleans up simulation state automatically. Refreshing the tab within 5 seconds cancels the shutdown.
- OSC simulators now correctly apply value maps when responding to state queries and processing incoming commands. This fixes initial state mismatch between the simulator and main server on startup for drivers with inverted value mappings.

## Pi Image Build

Fixed the build script referencing `update-helper.sh` via a relative path that breaks when pi-gen copies the stage into its own directory tree. The file is now staged into the build files directory alongside everything else.
