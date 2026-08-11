# OpenAVC v0.26.0

A space can now ask for help and find out that someone heard. Panels can carry
your own CSS instead of only the built-in themes. The UI Builder tells you when
a control will draw wrong before anyone stands in front of it. And the IDE and
the simulator both work properly from a laptop that is not the server.

## Ask for help

A panel button, a script, or a trigger can now raise a help request to whoever
supports the space.

It is a macro step, not a stock control, so nothing appears on a panel that you
did not put there. Wiring it as a step means it works from all three places: a
button press, a script, and a trigger firing with nobody in the room. A space
that has failed to power on three times can ask before anyone thinks to.

The answer comes back. Acknowledging it sends word down to the panel, so the
room can show "Help requested 9:02. Ben acknowledged 9:04." A button with no
visible effect is a black hole and gets pressed four more times.

The message is written as a sentence and fills in as one, so a trigger can say
what is actually wrong instead of "someone pressed help." Use `$var.room_name
projector failed to power on` and that is what arrives.

If the cloud cannot be reached, the panel says so rather than claiming help is
on the way. Nothing is queued: a request that arrives forty minutes later
arrives after the class ended.

## Your own styling on the built-in controls

Every project can carry a stylesheet that applies on top of the theme, so the
controls we ship can be made to look like yours. Ordinary CSS, targeting real
class names.

The UI Builder has an editor for it, beside Theme and Settings, with the panel
live beside your code as you type. Name a class in the stylesheet and it turns
up as a chip under Properties, Style, Custom classes, ready to click onto any
element. The theme colours are exposed as CSS variables, so a rule can follow
the theme instead of fighting it.

## The UI Builder tells you what will draw wrong

Page review now catches a set of layouts that used to reach a panel looking
broken with nothing anywhere to say so.

- A control drawn too small for what it contains. The warning names the size it
  is, the size it needs, which internal part breaks first, and the percentage to
  give it.
- A control with nothing to draw at all: an image with no source, a label with
  no text and nothing bound to supply one, a navigation button with nowhere to
  go, a select with no options.
- A binding this element type's renderer never reads, which looks like a stale
  value rather than a setting that was never wired.
- A matrix whose key patterns do not match anything, checked like any other
  binding now.

A Cancel button that says "go back" validates as well. That has always worked on
a panel, but the checker did not know the spelling and reported it as a page
that does not exist.

## Panels

A label can show a state's words instead of its raw value. Showing a device as
ONLINE or OFFLINE is on nearly every panel and used to need two stacked labels
with opposite visibility rules, or a button dressed up to look like a label.

Preview shows a dialog as a dialog. A confirm dialog with a Cancel button worked
on real glass and looked broken in Preview, which is the surface you use to
check whether a panel works.

Simulating a press on a toggle now resolves the toggle the way a real press
does, so a working toggle no longer reports as broken. Preview can also reach
master elements, and check a panel without firing the commands behind it.

## Working from another machine

Commissioning happens with the IDE on a laptop and the server in the rack, and
two things assumed otherwise.

The browser's own sign-in dialog could still appear over the UI Builder. The
simulator opened at an address that named your laptop rather than the server, so
it did not open at all from anywhere else. Both are fixed. The simulator is now
served by the main server and works from another machine and through the cloud
tunnel.

The simulator also opens correctly on a system that has no password set.

## Remote support

When you grant OpenAVC access to a system from the portal, the session now gets
past that system's own sign-in. Before, the grant reached the door and stopped,
and the only way through was putting your instance password on a support thread,
which is a worse thing to hand over than the access itself. The grant is the
credential now, it never leaves the box, and revoking it is enough.

Host network configuration still asks for the password. A support session is not
the console.

## Fixes

A tunnelled request now reports where it actually came from. Requests arriving
through the cloud tunnel could present a caller-supplied address to checks that
decide what a local caller may do.

Quitting the OpenAVC menu bar app on macOS no longer leaves you with no way to
open it again. Quit closes the icon only, which is correct, because a status
icon must not be able to take a space offline.

A child entity bound on a panel is checked against the roster the driver
declares, so a typo in a channel or output number is caught while you are
building rather than at the panel.
