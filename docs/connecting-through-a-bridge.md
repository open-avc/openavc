# Connecting Devices Through a Bridge

Some equipment has no network control at all: it takes an RS-232 serial cable, or it only responds to its infrared remote. A **bridge** puts those ports on the network so OpenAVC can reach the device over Ethernet. A bridge is a small box with serial and/or IR ports; you add it once, and other devices bind to its ports.

This guide covers serial bridges and IR bridges. The first supported models are the Global Cache iTach IP2SL (Ethernet to RS-232) and iTach IP2IR (Ethernet to infrared, three emitter ports).

## How it works

A bridge is a normal device in your project. You add it once, like any other device. Then any serial device you want to reach through it picks **Through a bridge** in its own Connection settings and chooses one of the bridge's ports. OpenAVC routes that device's traffic over the bridge automatically. To the rest of your project (macros, the panel, scripts) the serial device behaves exactly like a network device.

This is the same model professional control systems use for IR and serial ports: the gateway is one device, and other devices bind to its ports.

## What you need

- A serial bridge on the network with a known IP address (set a static IP or a DHCP reservation so it does not move).
- The bridge's driver installed (for the iTach IP2SL, install it from **Browse Community** in the Driver Library, or let Discovery add it for you).
- The serial device wired to the bridge with the correct RS-232 cable, and a driver for that device. If the device has no driver and you only need a few commands, add a **Generic Serial Device** and define its commands and responses on the device page instead. See [No-Code Commands and Responses](devices-and-drivers.md#no-code-commands-and-responses).

## Step 1: Add the bridge

You can add the bridge two ways:

- **Discovery.** Open **Devices**, select the **Discovery** tab, and run a scan. A supported bridge is identified automatically (the iTach announces itself on the network). Add it from the results.
- **Manually.** Click **Add Device**, pick the bridge driver, and enter its IP address.

Once added, open the bridge's device card. It shows a **Bridge Ports** section listing each port, what is currently bound to it, and a link to open the unit's own web page.

## Step 2: Connect a device through the bridge

1. Add (or edit) the serial device that is wired to the bridge, and pick its driver.
2. In **Connection settings**, the device shows a picker with three choices: **Network (IP)**, **Direct serial**, and **Through a bridge**. Choose **Through a bridge**.
3. Pick the bridge from the list, then pick the port the device is wired to (for the iTach IP2SL there is one port, "RS-232 Port 1").
4. Set the serial line settings the device expects: baud rate, parity, data bits, and stop bits. These come straight from the device's manual (for example 9600 8N1).
5. Save.

OpenAVC connects the device through the bridge and applies the serial settings to the hardware for you. The device card shows connected, and you control it like any other device.

> The connection picker only appears for drivers that support serial. A network-only device does not show it.

## Controlling an IR device through a bridge

An **IR device** is anything you would normally point a remote at: a TV, a cable box, a Blu-ray player, an AV receiver. Instead of a serial cable, you plug an IR emitter into one of the bridge's IR ports and stick it over the device's remote sensor. In OpenAVC an IR device is a set of named codes (Power On, Volume Up, Input HDMI1). Each code becomes a button you can put on a panel or call from a macro.

### Add the IR device

1. Add the IR bridge first (Step 1 above), the same way you would a serial bridge.
2. Click **Add Device** and pick **IR Device** (or a ready-made IR driver for your product, if one exists in the Driver Library).
3. In **Connection settings**, IR devices are always through a bridge, so you only pick the bridge and the emitter port the device is wired to (for example "IR Out 1"). Save.

### Build the code set

Open the device and find the **IR Codes** section. There are several ways to add a code, and you can mix them:

- **Learn from the remote.** Click **Learn**, point the original remote at the bridge's learning window, and press a button. OpenAVC captures the code; give it a name (Power On) and save. Turn on continuous capture to walk through a whole remote quickly, naming each button as you press it.
- **Paste a Pronto code.** Many remote-code sites publish codes in Pronto hex. Paste one in and name it.
- **Type a raw code.** If you have a Global Cache `sendir` string, paste that; OpenAVC converts it.
- **Search a code database.** Look up your brand and device, pick a function, and OpenAVC fetches that one code.

Each code has a **Test** button that fires it through the bridge right now, so you can confirm the emitter is aimed correctly and the code works before you save it. You can rename, reorder, and delete codes at any time. A ready-made IR driver comes with its codes already filled in, and you can add your own on top.

Once saved, every code shows up as a normal command on the device, so you bind panel buttons and macro steps to it exactly like any other device command.

> IR is one-way: the bridge sends codes but gets nothing back, so an IR device has no status feedback. It shows as online whenever its bridge is online.

## Seeing the whole picture

Two places show how everything is wired:

- **The bridge's device card** lists each port and the devices bound to it.
- **The Bridge topology panel** at the top of the device list shows a tree of every bridge, its ports, and the devices on each port. Click a name to jump to that device. The panel only appears when your project has at least one bridge.

## Using the bridge on its own

A bridge device works on its own, too. Its card has the standard command sender, so you can query the unit (for example, read its firmware version) without binding anything to it. An IR bridge's card also has a **Learn IR** tool and a raw emit test, so you can capture or fire a code straight from the bridge for troubleshooting, before any IR device is bound to it. The link to the unit's web page lets you reach the manufacturer's own configuration screens.

## Troubleshooting

- **The device will not connect.** Confirm the bridge itself is online (its own device card shows connected). Check the serial settings match the device's manual exactly, including baud rate and parity.
- **No response from the device.** This is almost always wiring or line settings. RS-232 needs the correct cable. If a straight-through cable gives nothing, try a null-modem (crossover) cable, or the reverse. Double-check baud rate, data bits, parity, and stop bits against the manual.
- **The bridge is not in the bridge list.** Make sure the bridge device is added to the project first. The list shows project devices whose driver advertises bridge ports.
- **Only one connection at a time.** A single serial pass-through port carries one connection. Bind one device per serial port.
- **An IR code does nothing.** Aim matters: the emitter has to sit over the device's remote sensor. Use the code's **Test** button while you position it. If a learned code is unreliable, learn it again holding the remote steady and close to the bridge's learning window.
- **Learning will not capture.** Only one learn session runs per bridge at a time, and learning pauses while the bridge is busy. Close any other learn window, then try again.

## See Also

- [Devices and Drivers](devices-and-drivers.md). Adding equipment, testing, and the driver library
- [Creating Drivers](creating-drivers.md). Building drivers, including multi-transport drivers and bridges
