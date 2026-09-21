# OpenAVC v0.35.0

Mostly fixes, plus two things macros can now do with panel controls.

## UI Builder

* A control can have a Tag. A macro run from that control reads it as `$trigger.tag`, so nine source buttons tagged 1 to 9 can share one macro instead of needing one each. `$trigger.element` gives the control's ID whether or not it has a tag.
* A macro step, or a control's own Set action, can change a control's label, visibility, background colour, text colour and opacity. Pick the control from the UI groups in the variable picker. Clearing the value puts the control back.
* A button using a transparent image and a gradient drew with no background at all. Same for an element background image.
* Project format moves to 0.14.0 for the Tag. A project saved here still opens on v0.34.0.

## Programmer

* A new blank project no longer keeps the previous project's uploaded files, custom controls and themes.
* The three-dot menu on the last row of the Project Library was cut off, putting Delete, Duplicate and Export out of reach.

## Device control

* A device with Line ending set to None would not connect, on TCP or serial.
* A connection that fails because of a bad setting now says the settings are invalid and stops retrying, instead of reporting a dropped connection and reconnecting forever.

## Thanks

Special thanks to Ziki, whose detailed report found most of the fixes in this release.
