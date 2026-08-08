# OpenAVC v0.25.0

Mostly an internals release. The Python package moved from `server` to `openavc`, so
drivers and plugins now import the platform under the product's own name. Alongside
that, the UI Builder learned to tell you what a page will actually draw wrong before
you load it on a panel.

## UI Builder page review

The Builder now checks a page against how the panel really renders it, and flags the
problems on the canvas, in Validate, and in the Layout panel:

- Controls sized too small to work. Each control type carries a measured minimum, and
  the warning tells you the size that would work, in the percentages you author in.
- Controls that cannot fit inside their container at any percentage, which blames the
  container rather than the control.
- Elements overlapping each other, or sitting outside the box that holds them.
- Elements with no box at all, or drawn at a few pixels square.
- Touch targets under the finger-size rule.
- Style measurements larger than the element they are set on.
- Element types the panel cannot draw, with the ones it can named in the message.
- Bindings this element type's renderer never reads, and bindings that point at a
  macro, page, device, or command that is not there.
- Ranges wider than the device's driver declares.

## Panels and devices

- Labels and gauges can round the numbers they display.
- Devices keep retrying a lost network connection instead of giving up after an hour.
  A device address is a fact about the space, so only faults that need a person to
  clear them stop the retry.
- `ui.navigate` is the single spelling for a page move in macros and UI actions.
- Both the macro editor and the UI action editor offer the same action list.

## Discovery

- A control interface pinned to an address that no longer answers is surfaced instead
  of hidden, with a one-click way to clear it.
- Declining a suggested driver stays declined until something changes.

## Windows

The installer waits for the server to accept connections before it reports success.

## For driver and plugin authors

Imports change from `server.*` to `openavc.*`:

```python
from openavc.drivers.base import BaseDriver
```

Community drivers and plugins in the catalog are already updated and carry a minimum
platform version, so an older system refuses the download with a clear message rather
than installing something it cannot load. If you have written your own drivers or
plugins, update their imports before moving a system to this release. A driver that
still uses the old name fails to load with a log line telling you what to change.

## Upgrading

On Linux, if you are on 0.20.0 or older, re-run the installer rather than using in-app
update.
