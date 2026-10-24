#!/usr/bin/env bash
# run.sh — what the NanoClaw task calls four times a day.
#
# Runs `python -m runner` from the project root inside a venv that this script
# owns (<project>/.venv), creating it from requirements-runner.txt when missing
# or when that file changes. Appends a timestamped header plus the runner's
# combined output to data/runner.log, prints the runner's final two lines
# (`DIGEST: …` and `VERDICT: …`) to stdout, and exits with the runner's exit
# status. Any arguments (e.g. --dry-run --max 2000) are passed straight through.
#
# Works from any working directory, inside the agent container and on the
# Ubuntu host alike. Derived from weatherToBattery's scripts/run-nightly.sh.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

VENV="$PROJECT_ROOT/.venv"
LOG="$PROJECT_ROOT/data/runner.log"
REQUIREMENTS="$PROJECT_ROOT/requirements-runner.txt"

# Fail before anything touches a mailbox if the log cannot be written: the
# caller reports from this log, so a run it cannot record must not happen.
if ! mkdir -p "$PROJECT_ROOT/data" 2>/dev/null || ! { : >> "$LOG"; } 2>/dev/null; then
  echo "cannot write $LOG; refusing to run" >&2
  exit 1
fi

# Behind the NanoClaw/OneCLI proxy every HTTPS connection is re-signed with the
# gateway's CA. httpx reads SSL_CERT_FILE; requests and pip do not, so point
# them at the same bundle.
if [ -n "${SSL_CERT_FILE:-}" ]; then
  export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
  export PIP_CERT="$SSL_CERT_FILE"
  export CURL_CA_BUNDLE="$SSL_CERT_FILE"
fi
export PYTHONUNBUFFERED=1

log_header() {
  printf '=== %s run %s ===\n' "$(date -Is)" "$*" >> "$LOG"
}

# A venv counts as ready only when the stamp written after a successful
# `pip install` exists AND it records the hash of the requirements file that
# was installed; a changed requirements file or a venv that failed part-way is
# rebuilt instead of running against missing packages.
STAMP="$VENV/.requirements-installed"
WANT="$(sha256sum "$REQUIREMENTS" | cut -d' ' -f1)"
log_header "$@"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  rm -rf "$VENV"
  {
    echo "creating venv at $VENV"
    python3 -m venv "$VENV" && "$VENV/bin/pip" install -q -r "$REQUIREMENTS"
  } >> "$LOG" 2>&1
  status=$?
  if [ "$status" -ne 0 ]; then
    rm -rf "$VENV"
    echo "venv setup failed (exit $status); see $LOG" >&2
    tail -n 5 "$LOG" >&2
    exit "$status"
  fi
  printf '%s\n' "$WANT" > "$STAMP"
fi

output="$("$VENV/bin/python" -m runner "$@" 2>&1 | tee -a "$LOG"; exit "${PIPESTATUS[0]}")"
status=$?

# The runner's final two lines are the contract the caller reads.
printf '%s\n' "$output" | tail -n 2
exit "$status"
