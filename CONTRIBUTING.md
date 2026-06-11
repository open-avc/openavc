# Contributing to OpenAVC

Everyone is welcome to contribute. Bug reports, documentation fixes, and
testing on real AV equipment are just as valuable as code.

Drivers and plugins have their own repos:

- Device drivers go to [openavc-drivers](https://github.com/open-avc/openavc-drivers)
  ([contributing guide](https://github.com/open-avc/openavc-drivers/blob/main/docs/contributing-drivers.md))
- Plugins go to [openavc-plugins](https://github.com/open-avc/openavc-plugins)
  ([contributing guide](https://github.com/open-avc/openavc-plugins/blob/main/docs/contributing-plugins.md))

If you have never contributed to an open source project before,
[first-contributions](https://github.com/firstcontributions/first-contributions)
is a hands-on tutorial for the fork and pull request workflow used here and
on most of GitHub.

## Reporting bugs

Open an issue. Please include:

- What you did, what you expected, and what happened instead
- Your OS and how you installed OpenAVC (Windows installer, Docker, Pi image,
  Linux script)
- Log output if you have it (the Log view in the Programmer IDE)

"It doesn't work" is hard to act on. Steps to reproduce are the most useful
thing you can provide.

## Suggesting features

Open an issue describing the problem you're trying to solve, not just the
feature you want. Knowing the use case usually leads to a better design.
[Discord](https://discord.gg/FHcuxG5aTa) is a good place to talk through
ideas first.

## Submitting changes

The standard GitHub workflow:

1. Fork the repo and create a branch off `main`.
2. Make your changes.
3. Run the linter and tests (below).
4. Push the branch to your fork and open a pull request against `main`.

For bug fixes and small improvements, go right ahead. For anything bigger
(new features, refactors, behavior changes), open an issue first so we can
agree on the approach before you spend real time on it.

Keep pull requests small and focused. One fix per PR is much easier to
review than five. If your change adds a runtime capability that the
Programmer IDE should expose, update the IDE in the same PR rather than
leaving it backend-only.

## Development setup

You need Python 3.11+ and Node 20+.

```bash
git clone https://github.com/<your-username>/openavc.git
cd openavc

# Backend
pip install -r requirements.txt
pip install -e ".[test]"

# Frontend (required once before first run)
cd web/programmer
npm ci
npm run build
cd ../..

# Start the server
python -m server.main
```

The Programmer IDE is at http://localhost:8080/programmer and the touch
panel at http://localhost:8080/panel.

The web UIs are React apps compiled to static files. The server serves the
compiled output, so if you change anything under `web/programmer/` or
`web/simulator/`, run `npm run build` there again before testing.

## Linting and tests

```bash
ruff check server/ tests/
pytest tests/ --ignore=tests/perf
```

CI runs both on every pull request, on Linux and Windows, plus the frontend
builds. Everything has to pass before merge. `tests/perf` is excluded
because it holds wall-clock benchmarks that are unreliable on busy machines.

If you fix a bug, add a test that fails without the fix. If the change
affects something users see, update the matching page in `docs/`.

## Dependencies

Think hard before adding one. OpenAVC keeps its dependency footprint small
and prefers pure Python.

Any new dependency must:

- Have an MIT-compatible license (MIT, BSD, Apache-2.0, ISC). GPL, LGPL,
  and AGPL can't be accepted, no exceptions.
- Work on Windows and Linux, x86_64 and ARM64.

## Cross-platform rules

OpenAVC runs on everything from a Raspberry Pi to a rack server, on Windows
and Linux. Don't assume a platform. Platform-specific features like serial
ports detect their environment at runtime and degrade gracefully; follow
that pattern.

## License

OpenAVC is MIT licensed. By submitting a pull request you agree that your
contribution is licensed under the same terms.

## Questions

Ask on [Discord](https://discord.gg/FHcuxG5aTa) or open an issue.
