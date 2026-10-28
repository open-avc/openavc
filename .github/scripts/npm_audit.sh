#!/usr/bin/env bash
# Audits what one frontend bundle ships: `npm audit --omit=dev` in the
# directory given as the only argument.
#
# The registry's advisory endpoint fails a request now and then for reasons on
# npm's side, so a failed request is tried again, up to three times. A report
# that finds a vulnerability is never retried: the first one fails the job.
set -u

dir=$1
attempts=3

for attempt in $(seq 1 "$attempts"); do
  output=$(cd "$dir" && npm audit --omit=dev 2>&1)
  status=$?
  printf '%s\n' "$output"
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  # npm prints this line only when the request itself failed, never for a
  # report that found something.
  case $output in
    *"audit endpoint returned an error"*) ;;
    *) exit "$status" ;;
  esac
  if [ "$attempt" -lt "$attempts" ]; then
    echo "The advisory endpoint returned an error (attempt $attempt of $attempts); trying again in 15 seconds."
    sleep 15
  fi
done

echo "The advisory endpoint failed $attempts times in a row. This is npm's service, not a dependency."
exit "$status"
