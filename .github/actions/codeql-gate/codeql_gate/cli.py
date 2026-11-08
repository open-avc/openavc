"""Exit 0 only for verified code; 1 for findings; 2 for incomplete evidence."""
import argparse
import re
import time

from .api import GitHub, Unavailable
from .evidence import Findings, Pending, verify


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--languages", required=True, help="Comma-separated CodeQL language identifiers")
    parser.add_argument("--after-landing", action="store_true", help="Permit a proven descendant if a newer push superseded this scan")
    parser.add_argument("--wait", type=int, default=900, help="Maximum polling seconds; 0 checks once")
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("Expected owner/repository")
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("--sha must be a full commit SHA")
    if args.wait < 0 or not args.languages.strip():
        parser.error("Nonnegative wait and at least one language required")
    return args


def main():
    args = arguments()
    deadline = time.monotonic() + args.wait
    previous = None
    while True:
        try:
            print(verify(GitHub(args.repository), args.sha, args.branch,
                         set(args.languages.split(",")), args.after_landing), flush=True)
            return 0
        except Pending as exc:
            message = f"CodeQL verification incomplete: {exc}"
            if message != previous:
                print(message, flush=True)
                previous = message
            if time.monotonic() >= deadline:
                return 2
            time.sleep(min(20, max(0, deadline - time.monotonic())))
        except Findings as exc:
            print(f"CodeQL needs attention:\n{exc}", flush=True)
            return 1
        except (Unavailable, KeyError, TypeError, ValueError) as exc:
            print(f"CodeQL verification incomplete: {exc}", flush=True)
            return 2
