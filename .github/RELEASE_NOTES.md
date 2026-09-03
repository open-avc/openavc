# OpenAVC v0.33.0-rc1

A preview build. A panel can now dim itself when nobody is using it, and several controls stop showing a value they cannot stand behind.

## Panel display

* **A panel can dim itself when nobody is using it.** A panel left on the same page all day and all night can leave a faint permanent ghost of that page on the screen. Dimming the picture while nobody is touching it slows that down considerably. Turn it on in Settings > Panel Display, on hardware where OpenAVC drives the screen itself.
* **How dim, and for how long, are yours to set.** Choose the minutes of no touch before it fades, and how far down it goes. The level is a percentage of the panel's normal brightness rather than a fixed setting, so a panel already turned down for a dark space dims further instead of getting brighter.
* **A meeting in progress keeps the panel bright.** A room in use generates no touches, so a plain timer would dim the panel in the middle of a presentation. Point **Stay bright while** at whatever your project already sets when the space is in use, such as a system-on variable or a display's power state, and the panel holds full brightness until it clears.
* **The waking touch does not press anything.** By default the first touch on a dimmed panel only brings the brightness back, so nobody mutes a live room by tapping a screen they could not read. Turn on **Waking touch also presses the button** if you would rather it act on the control underneath.
* **The screen never switches off, only dims.** A dark panel looks broken to somebody walking into the space.

## Panel controls

* **A control whose device is unreachable stops showing a value.** A reading from a device that is no longer answering is not a reading. Controls now show that the value is unavailable rather than continuing to display the last thing they heard.
* **A crosspoint marks its dead rows.** Rows whose device is unreachable are marked as such, matching what the other matrix styles already did.

## Programmer

* **The design canvas draws a control the way it is designed.** It was drawing some controls as the room currently finds them, so a control looked different while you were building it than it would once deployed.
* **Typing into a macro step field keeps the field.** A step field could lose focus while you were typing in it.
* **Simulator cards are sized by their own contents**, so a card no longer stretches to match an unrelated one.
* **The simulator picks a port it can actually use.** On Windows it could pick one the operating system had reserved, and fail to start with an error that did not explain why.

## Documentation

* Download links point at the download page rather than a list of release files.

## About this preview

This is a preview release for validating the panel display work on appliance hardware. It is not offered to systems on the stable update channel.
