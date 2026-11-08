# CodeQL evidence gate

A read-only gate for repositories using GitHub CodeQL default setup. Requires
Python 3.9+, `gh`, and a token with contents, actions and security-events read.
Call the action pinned to a full commit SHA; give it the immutable commit being
published and every configured language. Publishing jobs must `needs` the gate.

The gate requires a successful latest CodeQL run and complete analyses on the
configured default branch. It reads each analysis's SARIF snapshot and checks
triage on every finding, including findings now marked fixed on a newer commit.
Only dismissed alerts with a reason **and comment** are accepted. Missing data,
API failures, partial analysis and a 15-minute timeout fail closed. No scanner
configuration or alert state is changed. Exit 1 means findings; 2 means evidence
is incomplete. Fix the code and rescan, or document a justified dismissal in
GitHub; do not dismiss a real issue merely to publish.

A deployment merge may use a scanned immediate parent only when GitHub confirms
identical Git trees. The CLI's `--after-landing` also accepts a proven descendant
when concurrent pushes supersede a scan; release workflows never use that option.
The verified revision and evidence link are printed. Advanced CodeQL workflows
require an explicit extension of the run/analysis matching before using this gate.

Run tests from this directory with `python -m unittest discover -s tests -v`.
The separate **CodeQL gate checks** workflow can be dispatched to exercise the
real read-only gate with a GitHub Actions token without publishing anything.
