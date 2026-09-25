"""Device Audit: point OpenAVC at one device and write down what it does.

- ``session``: one audit at a time, its lifetime, the project devices it
  pauses, and every way it ends.
- ``footprint``: the network check, composed from the discovery scanners'
  own functions against one address.
- ``report``: the report file (``report.json``, ``summary.html``,
  ``timeline.txt``), redaction, and the recent reports kept on disk.
"""
