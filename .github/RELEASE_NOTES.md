# OpenAVC v0.33.0-rc2

A preview build. Panels can dim themselves when nobody is using them, and the settings for that travel with the project instead of living on each system.

## Panel display

* **A panel can dim itself when nobody is using it, and it is on by default.** A panel left on the same page all day and all night can leave a faint permanent ghost of that page on the screen. Dimming the picture while nobody is touching it slows that down considerably, so it now happens on its own: five minutes with no touch, dimmed to 20%. Change it in Settings > Panel Display, on hardware where OpenAVC drives its own screen.
* **Set the panel's brightness from the Programmer.** It was previously only reachable from the panel's own maintenance menu. Until you set it, each panel keeps whatever its own screen is set to, and clearing it hands control back.
* **How dim, and for how long, are yours to set.** The dim level is a percentage of the panel's normal brightness rather than a fixed setting, so a panel already turned down for a dark space dims further instead of getting brighter.
* **Set the dim level to 0 to black the screen out.** The backlight goes as low as the panel allows and the page is covered, so it reads as switched off from across the room. A touch brings it straight back. The screen is never put to sleep: on panel hardware a sleeping screen cannot be woken by touching it, only by the power button, which on a wall-mounted panel is no better than a dead one.
* **A meeting in progress keeps the panel bright.** A room in use generates no touches, so a plain timer would dim the panel in the middle of a presentation. Point **Stay bright while** at whatever your project already sets when the space is in use, such as a system-on variable or a display's power state.
* **The waking touch does not press anything.** By default the first touch on a dimmed panel only brings the brightness back, so nobody mutes a live room by tapping a screen they could not read. There is a setting if you would rather it act on the control underneath.

## Settings that travel with the project

* **Panel display settings are stored in the project file.** Set them once and every system you deploy that project to gets them: a cloud template pushed to a hundred panels carries them, and so does a project you export and open somewhere else. Previously they would have had to be set on each system by hand.
* **The device retry interval travels too.** How often OpenAVC retries a device that has gone offline is the rate of connection attempts your IT department sees, so it is a property of the site rather than of one system. It is now in Settings > Devices, and empty means "use this system's own setting".
* **Settings that decide how a system is reached deliberately do not travel.** Network address and ports, passwords and API keys, cloud pairing and certificates stay with the individual system. A project file moves around, so a mistake in one of those could take a whole site off the network at once with no way to fix it from the panel.

## Panel controls

* **A control whose device is unreachable stops showing a value.** A reading from a device that is no longer answering is not a reading, so controls now show the value as unavailable rather than continuing to display the last thing they heard.
* **A crosspoint marks its dead rows**, matching what the other matrix styles already did.
* **A toggle button shows whether the thing it controls is on.**

## Programmer

* **The design canvas draws a control the way it is designed**, rather than as the room currently finds it.
* **Typing into a macro step field keeps the field.**
* **Validate checks a macro step the same way it checks a control**, so a problem in a macro is caught where a problem in a button already was.
* **Checks that pass a control before it has been built now say so**, instead of reporting a clean result that had not actually looked at anything.
* **Simulator cards are sized by their own contents**, so a card no longer stretches to match an unrelated one.
* **The simulator picks a port it can actually use.** On Windows it could pick one the operating system had reserved and fail to start with an error that did not explain why.

## Upgrading

* **Panel display settings need appliance shell 0.3.21 or newer.** OpenAVC holds the settings and the panel software does the dimming, so on an older panel build these settings have no effect.
* **Projects are updated automatically** to the new format the first time they are opened. Nothing needs re-authoring.

## About this preview

This is a preview release for validating the panel display work on appliance hardware. It is not offered to systems on the stable update channel.
