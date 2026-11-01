#!/usr/bin/env bash
# add-event.sh — what Dr Claw runs to put one event in the Outlook calendar.
#
#   scripts/add-event.sh --title "Christopher Moy's Birthday" --date 2026-11-05 --yearly
#   scripts/add-event.sh --title Dentist --start 2026-11-06T14:00 --end 2026-11-06T15:00
#
# Runs `python -m runner.addevent` with the arguments given, in the venv that
# scripts/run.sh owns (<project>/.venv). It prints one line, CREATED:, EXISTS: or
# ERROR:, and exits 0 when the event is in the calendar, 1 otherwise. Works from
# any working directory, inside the agent container and on the Ubuntu host alike.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

VENV="$PROJECT_ROOT/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "ERROR: no venv at $VENV; run scripts/run.sh once to create it" >&2
  echo "ERROR: calendar tooling not installed"
  exit 1
fi

# Behind the NanoClaw/OneCLI proxy every HTTPS connection is re-signed with the
# gateway's CA. httpx reads SSL_CERT_FILE; keep the other names in step with run.sh.
if [ -n "${SSL_CERT_FILE:-}" ]; then
  export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
  export CURL_CA_BUNDLE="$SSL_CERT_FILE"
fi
export PYTHONUNBUFFERED=1

exec "$VENV/bin/python" -m runner.addevent "$@"
