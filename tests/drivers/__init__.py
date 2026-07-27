"""Invented driver classes used by core platform tests.

These are not products and never ship. They exist so tests that need a
working driver — one that really opens a transport, really parses frames,
really polls — can have one without depending on the community driver
library being installed. Registered into the driver registry by
``tests/conftest.py`` at import time.
"""
