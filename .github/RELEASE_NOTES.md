# OpenAVC v0.15.1

A small follow-up to 0.15.0 that keeps the room panel login-free and tightens
how the Programmer handles passwords.

## Wall panels stay open

On a controller with an admin password set, opening the room panel on a wall
tablet could trigger the browser's built-in login popup, a username and password
box the panel was never meant to show. The panel now loads its theme and plugin
data without ever prompting, so tablets and phones reach the controls the moment
they connect.

## Clearer first-run setup

First-time setup now asks for an admin username alongside the password,
prefilled with "admin." It's the username you'll type on the Programmer sign-in
screen, so the login no longer has an empty field with nothing to enter.

## Passwords aren't pre-filled

Password and secret fields in the Programmer no longer show pre-filled dots or
get refilled by the browser's saved-password autofill. A field stays empty
unless you type in it.

## Driver Builder reliability

The Driver Builder warns before discarding an unsaved driver you're still
editing, and keeps your edits straight when a save and a change overlap.
Imported or pasted drivers are checked the same way the form editor checks them,
so a bad file loads into the editor to fix instead of failing silently.
