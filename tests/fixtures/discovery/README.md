# Discovery probe response fixtures

Captured response bytes for driver-declared `udp_broadcast_probe` /
`tcp_active_probe` blocks.

One fixture per driver, named by driver id:

- `<driver_id>.bin` — raw captured bytes (binary protocols).
- `<driver_id>.txt` — ASCII responses (line endings preserved).

`tests/test_discovery_probes.py` discovers each loaded driver with a
declared probe block, looks up the matching fixture here, and replays
it through `probe_runner._matches` / `_apply_extract` to confirm the
declaration matches the captured wire format and extract rules pull
the expected fields.

Adding a new fixture requires no test-code change — the runner picks
it up automatically the next time pytest collects.
