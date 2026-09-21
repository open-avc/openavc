# OpenAVC v0.35.0

A control can tell the macro it runs which control it is, so a row of buttons can share one macro instead of needing one each. Changing a control's label, colour or visibility from a macro is now offered wherever you pick a value, instead of being something only a script could reach. On the device side, a device set to no line ending connects.

## UI Builder

* **A control can have a Tag**, and the macro it runs reads it as `$trigger.tag`. Nine source buttons tagged 1 to 9 can share one macro that sends the tag as the input number. That macro also reads `$trigger.element`, the control's ID, whether or not you set a tag, and `$trigger.value` for a control that has one.
* **Changing a control while the program runs no longer needs a script.** A macro step, or a control's own Set action, sets a control's label, visibility, background colour, text colour or opacity, and every panel showing it follows. The panel has always read these, but the key picker only offered one after something had already set it, so there was no way to reach them from a new project. The picker now lists every control in the project, grouped by control. Clearing the value returns the control to the way it was designed.
* **A gradient is no longer lost when a button also uses an image.** A button with a transparent image and a gradient drew with no background at all, while the same button with a flat colour was fine. The image now draws over the gradient. An element background image behaved the same way and was fixed with it.
* Project format moves to 0.14.0 for the Tag. Nothing in an existing project changes, and a project saved here still opens on v0.34.0, where a macro reads no tag.

## Programmer

* **A new blank project starts empty.** The previous project's uploaded files, custom controls and custom themes were left in it. A backup is still taken before the project is replaced, and it holds all three.
* **The Project Library's row menu opens fully on the last row.** It was cut off at the edge of the list, which put Delete, Duplicate and Export out of reach there.

## Device control

* **A device with Line ending set to None connects.** The setting was refused while the connection was being built, so the device never reached the network, on every attempt. Serial connections had the same fault.
* **A connection refused because of a setting says so and stops retrying.** It was reported as a dropped connection and reconnected indefinitely. It now reports invalid settings and names what to check.
