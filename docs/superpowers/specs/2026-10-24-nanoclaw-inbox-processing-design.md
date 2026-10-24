# Autonomous inbox processing under NanoClaw

**Date:** 2026-10-24
**Status:** approved in conversation, awaiting written review

## Summary

Move the always-on email processing from a Windows scheduler running Claude
Code headlessly (see `autonomous-processing.md`) to a deterministic Python
runner executed every 15 minutes inside the owner's NanoClaw agent, "Dr Claw",
with a Slack digest only when there is something to say. The classification
rules (`rules.yaml`), the conservative autonomous policy and the per-account
run state keep their shape. What changes is that code, not a model, does the
fetching, matching and acting; the model only renders a digest.

Credentials never enter the container: Gmail is a built-in OneCLI connection
(already connected and granted to Dr Claw on 2026-10-24), and Outlook.com is
reached through a OneCLI custom secret that a small host-side relay keeps
fresh from a Microsoft refresh token.

This is the second NanoClaw automation after the weatherToBattery nightly
run and reuses its pattern: a mounted project folder, a wrapper that owns a
venv, a recurring task on Dr Claw's group, and reports to the owner's Slack
DM.

## Goals

- Every 15 minutes, new mail in both accounts is classified by `rules.yaml`
  and only safe actions are applied: label, archive, mark read, hold. Nothing
  is deleted or moved to Deleted Items, ever, by this runner.
- The owner gets a Slack digest only when a run found important mail, held
  anything a trash rule matched, left anything unmatched for review, or hit
  an error. Quiet runs cost no model tokens.
- The first run sweeps a bounded backlog of seven days once, with the same
  conservative actions, then runs incrementally.
- Secrets stay out of the container: no OAuth tokens, client secrets or
  refresh tokens in the mount, the prompt or the agent's environment.
- Runs are idempotent and resumable: a killed or repeated run never
  double-processes and never skips mail.

## Non-goals

- The interactive "clean my inbox" flow, rule creation or rule suggestions by
  the model, retention sweeps and calendar side effects. All stay out; the
  runner applies existing rules only.
- Replacing the Outlook or WhatsApp MCP servers for interactive use. They stay
  in the repository untouched; nanoclaw does not use them.
- Sending mail.
- OneCLI Enterprise features. The Community edition has no Outlook connection,
  which is why the relay exists.

## Current state (verified 2026-10-24)

- This machine has none of email-manager's runtime: no `.mcp.json`, no
  `state.json`, no Gmail OAuth files, no Outlook token file. The repository is
  a plain checkout on `master`, last commit 2026-07-19. The autonomous mode
  described in `autonomous-processing.md` ran elsewhere.
- NanoClaw v2 runs in WSL2 Ubuntu at `/home/gethi/nanoclaw`. Dr Claw is agent
  group `ag-1792842294321-ro8wyt` (folder `dm-with-gethin`), its OneCLI agent
  id is `4ae0ed0c-a511-4ef5-be2a-06d0c5b489fe`, its Slack DM destination is
  named `gethin`, its `cli_scope` is `global`, and its image already carries
  `python3` and `python3-venv` from the battery work.
- OneCLI 1.45.0 Community, bound on `0.0.0.0`, dashboard at
  `http://localhost:10254` from Windows, proxy at `172.17.0.1:10255` for
  containers, Postgres unpublished. Available apps include Gmail and no
  Microsoft app.
- Gmail connection `932e526c-65ec-42b5-945a-ad73cbb5f255` is connected for
  the owner's account and granted to Dr Claw; a call to
  `gmail.googleapis.com/gmail/v1/users/me/profile` from Dr Claw's container
  returned the mailbox profile.
- A generic OneCLI secret with host pattern `graph.microsoft.com`, header
  `Authorization`, format `Bearer {value}`, attached to Dr Claw, is injected:
  a dummy value produced Microsoft's own `InvalidAuthenticationToken` from
  inside the container. Without such a secret the gateway refuses the host
  with `credential_not_found`.
- The proxy re-signs every HTTPS connection; Python's `requests` needs
  `REQUESTS_CA_BUNDLE=$SSL_CERT_FILE` (the battery wrapper already sets it).
- NanoClaw task gate scripts run inside the agent container with the agent's
  environment, 30-second timeout, 1 MB output, last stdout line must be
  `{"wakeAgent": <bool>, "data": {...}}`; a cadence above four fires a day is
  accepted only with a gate script; repeated script errors back the series
  off and auto-pause it after eight.
- `rules.yaml`: 184 rules, `match_field` sender (153) or subject (20) and a
  few header rules; actions keep 62, trash 94, archive 12, calendar_event 5;
  labels `em/spam`, `em/promotions`, `em/important`, `em/notifications`,
  `em/receipts`, `em/newsletters`; `settings.privacy_mode: headers_first`.

## Design

### 1. Components and layout

**Repository changes (this repo).**

- `runner/` — Python package, `python -m runner`. Modules:
  - `rules.py`: load and validate `rules.yaml`; `match(message, rules) ->
    Rule | None` (first match in file order, `sender`/`subject`/`header`
    fields; header rules only when the message carries raw headers).
  - `policy.py`: the autonomous mapping from a rule's action to provider
    operations (table in Section 2); pure function, no I/O.
  - `providers/gmail.py` and `providers/graph.py`: thin REST clients over
    `httpx` with a shared interface: `list_since(ts, limit)`, `add_label`,
    `remove_from_inbox`, `mark_read`, `move_to_folder`, `ensure_labels()`.
    No credentials: the proxy injects them.
  - `state.py`: `state.json` read/write (shape below), per-account `last_run`
    advanced only past successful messages, error-suppression timestamps.
  - `run.py`: the orchestration: window, fetch, match, act, summarise; honours
    `--max`, `--budget-seconds`, `--dry-run`, `--accounts`.
  - `gate.py`: wraps `run.py` and prints the NanoClaw JSON line; catches
    everything.
  - `digest.py`: renders the human digest text from a summary (used for
    `last-run-summary.md` and offered to the agent verbatim in `data`).
- `relay/` — Python package, `python -m relay`, host-side only (Section 3).
- `scripts/run.sh` — the venv-owning wrapper, a copy of weatherToBattery's
  `scripts/run-nightly.sh` with the script name changed: venv at
  `<project>/.venv` with a requirements-hash stamp, CA exports from
  `SSL_CERT_FILE`, refuses to run if `data/` is unwritable, passes arguments
  through to `python -m runner`, exits with its status. Logs to
  `data/runner.log`.
- `requirements-runner.txt` — `httpx`, `pyyaml`, `python-dateutil`; test
  extras `pytest`, `respx`. Kept separate from the MCP servers' npm packages.
- `.gitignore` additions: `.venv*/`, `data/`.
- `README.md`: a "Running under NanoClaw" section; `autonomous-processing.md`
  gains a note at the top that the scheduler and digest channel described
  there are superseded by the runner for this install.

**Host layout (Ubuntu).**

- `/home/gethi/email-manager`: clone of this repo, mounted read-write into Dr
  Claw's group at `/workspace/extra/email-manager` (container path
  `email-manager`, stored with `readonly: false`, allowlist root added). Holds
  `rules.yaml`, `state.json`, `data/` (runner log, run summaries,
  `last-run-summary.md`), `.venv`.
- `~/.config/email-manager/relay.env` (0600): `MS_CLIENT_ID`, `MS_CLIENT_SECRET`,
  `MS_TENANT=consumers`. `~/.config/email-manager/outlook-token.json` (0600):
  the current refresh token, written by the relay. Neither path is mounted.
- `~/.config/systemd/user/outlook-token-relay.{service,timer}`: runs
  `python -m relay refresh` every 30 minutes with the host venv
  `~/email-manager/.venv-host`.

### 2. What one run does

Inputs: `rules.yaml`, `state.json`, flags. Defaults: `--max 40`,
`--budget-seconds 20`, accounts from `state.autonomous.accounts` (default
both), backlog `settings.autonomous.backlog_days` (default 7) used only when an
account has no `last_run`.

1. Load and validate rules; load state (create it with no `last_run` values
   if missing). A parse failure aborts the whole run with an error summary
   and no account is touched.
2. For each account: window start = `accounts.<p>.last_run`, else now minus
   `backlog_days`. Fetch messages received after the window start, oldest
   first, in pages, stopping at `--max` candidates. Candidate filter:
   - Gmail: `q=after:<unix-ts> in:inbox`, with a client-side skip of
     anything already carrying any `em/*` label. On first use the runner creates
     the labels `em/important`, `em/newsletters`, `em/notifications`,
     `em/promotions`, `em/receipts`, `em/spam`, `em/held`, `em/review` if
     absent (idempotent lookup-then-create).
   - Outlook: `/me/mailFolders/inbox/messages?$filter=receivedDateTime ge
     <iso> and isRead eq false&$orderby=receivedDateTime asc&$top=<n>` with
     `$select` of id, receivedDateTime, from, subject, isRead. On first use
     the runner ensures top-level folders `em-important`, `em-newsletters`,
     `em-notifications`, `em-promotions`, `em-receipts` exist (list, then
     create the missing ones).
3. Match each candidate with `rules.match`. Header rules apply only to
   Gmail, where the message is fetched with `format=metadata` and the headers
   the rule names.
4. Apply the autonomous policy:

   | Rule action | Gmail | Outlook |
   |---|---|---|
   | `keep` | add `em/<label>` | mark read, leave in Inbox |
   | `archive` | add `em/<label>`, remove `INBOX` | move to `em-<label>` folder |
   | `trash` | add `em/<label>` **and `em/held`**, leave in Inbox | mark read, leave in Inbox, count as held |
   | `calendar_event` | add `em/<label>`, leave in Inbox, count as calendar-review | mark read, leave in Inbox, count as calendar-review |
   | unmatched | add `em/review` | mark read, leave in Inbox, count as review |

   `em/important` matches are additionally listed in the summary with sender
   and subject (headers only, per `privacy_mode: headers_first`). Each
   message's operations complete before the next message starts.
5. After each message succeeds, remember its `receivedDateTime`. When the
   account's batch ends (cap reached, budget exhausted, or no more mail),
   set `accounts.<p>.last_run` to the newest successful message's timestamp,
   or to the run start time if every candidate in a complete fetch succeeded.
   A message whose action failed is not counted as successful; `last_run`
   does not move past it, so the next run retries it.
6. Write `data/runs/<utc-timestamp>.json` (the summary) and append
   `digest.render(summary)` to `data/last-run-summary.md`.

**Summary shape** (also the gate's `data`):

```json
{
  "started": "2026-10-24T19:00:03Z", "finished": "...", "dry_run": false,
  "accounts": {
    "gmail":   {"fetched": 9, "processed": 9, "errors": 0, "capped": false},
    "outlook": {"fetched": 3, "processed": 3, "errors": 0, "capped": false}
  },
  "important": [{"account": "gmail", "from": "First Direct", "subject": "Payment due 20th"}],
  "held": 3, "review": 1, "calendar_review": 1,
  "errors": ["gmail: modify 18c3...: 429 rate limited"],
  "auth": {"gmail": "ok", "outlook": "unauthorized"}
}
```

**Wake rule** (`gate.py`): wake when `important` is non-empty, or `held > 0`,
or `review > 0`, or `calendar_review > 0`, or `errors` is non-empty, or any
`auth` value is not `ok` and that condition has not been reported in the last
six hours (`state.autonomous.last_reported.<condition>`). Otherwise
`{"wakeAgent": false, "data": {"quiet": true, "processed": N}}`.

**state.json** keeps the documented shape and adds:

```json
"autonomous": {
  "enabled": true, "accounts": ["gmail", "outlook"],
  "last_run": "...", "backlog_days": 7,
  "digest": {"channel": "slack", "target": "gethin"},
  "last_reported": {"outlook_unauthorized": "2026-10-24T19:00:03Z"}
}
```

### 3. Credentials

**Gmail.** Nothing in this repo. The runner calls
`https://gmail.googleapis.com/gmail/v1/users/me/...` unauthenticated; the
proxy injects the connection. A gateway `401` with `app_not_connected` is
mapped to `auth.gmail = "not_connected"` and its `connect_url` is carried into
the summary so the digest can show it.

**Outlook: the token relay** (`relay/`, host only).

- `python -m relay authorize`: builds the Microsoft authorization URL for
  `https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize` with
  the app's client id, `response_type=code`, scopes
  `offline_access User.Read Mail.ReadWrite`, PKCE, and redirect URI
  `http://localhost:3333/auth/callback` (the URI the Outlook MCP server
  registered on the same app); prints the URL; listens on `127.0.0.1:3333`
  for the callback (the Windows browser reaches it through WSL localhost
  forwarding); exchanges the code at `/consumers/oauth2/v2.0/token`; writes
  `outlook-token.json` with the refresh token, scopes and account hint, mode
  0600. One-time, interactive.
- `python -m relay refresh`: reads `relay.env` and `outlook-token.json`;
  posts `grant_type=refresh_token` to the token endpoint; on success stores
  the **rotated** refresh token Microsoft returns (consumer accounts rotate
  it) atomically, then upserts the OneCLI secret named `outlook-graph-relay`
  (`--type generic --host-pattern graph.microsoft.com --path-pattern '/*'
  --header-name Authorization --value-format 'Bearer {value}'`) with the new
  access token via `onecli secrets create` the first time and `onecli secrets
  update --id` after, and on first creation attaches it to Dr Claw's agent
  with `onecli agents grants attach-secret`. The secret id is cached in
  `outlook-token.json`. Exit non-zero on any failure, with the reason on
  stderr, so systemd records it.
- Timer: every 30 minutes; access tokens live 60–90 minutes, so a single
  missed refresh does not break the runner. `OnBootSec=2min` so a reboot
  refreshes promptly.
- Scopes exclude `Mail.Send` deliberately.

**Where secrets live.** Client id/secret and the refresh token: host files
with mode 0600 under `~/.config/email-manager/`, never in the repo, the
mount, nanoclaw's `.env`, or any prompt. Access token: inside OneCLI's vault
only. The container sees none of them.

### 4. NanoClaw wiring and the digest

- Mount allowlist gains `/home/gethi/email-manager` (`allowReadWrite: true`)
  alongside the battery folder; Dr Claw's `additional_mounts` gains
  `{hostPath: /home/gethi/email-manager, containerPath: email-manager,
  readonly: false}` (written through `scripts/q.ts` as before), then `ncl
  groups restart`.
- Recurring task `inbox-processing` on Dr Claw's group, recurrence
  `*/15 * * * *`, with `--script`:

  ```bash
  bash /workspace/extra/email-manager/scripts/run.sh --gate --max 40 --budget-seconds 20
  ```

  The wrapper's last stdout line is the runner's JSON line (the wrapper
  prints the script's final line, which is exactly that). The task prompt,
  used only when the gate wakes the agent:

  > The inbox runner has finished and attached its summary as data. Send ONE
  > message to the destination `gethin` containing `data.digest_text`
  > verbatim. Do not read mail, do not retry the runner, do not change any
  > file, do not add commentary.

  `gate.py` includes the rendered digest as `data.digest_text` so the agent
  has nothing to compose.
- Digest text (rendered by `digest.py`, mirrors `autonomous-processing.md`):

  ```
  📬 Email run 19:00
  Processed: 12 (Gmail 9, Outlook 3)
  ⭐ Important: 2
    • First Direct — "Payment due 20th"
    • HMRC — "Message in your account"
  🕒 Needs calendar review: 1
  ✋ Held for review (would-be trash): 3
  📝 Review (unmatched): 1
  ```

  Error form: `⚠️ Email run 19:00: <reason>. <N> messages processed before
  the error.` Auth form: `⚠️ Outlook needs re-authorising: run `python -m
  relay authorize` on the host.` or the Gmail reconnect link.
- First-time backlog sweep: after `scripts/run.sh --dry-run --max 1000`
  has been reviewed, a one-off task on Dr Claw's group runs `scripts/run.sh
  --max 1000` (no gate, no 30-second limit) and reports its digest. The
  recurring task is created only after that completes.
- Dr Claw's `instructions.prepend.md` gains an "Inbox processing" section:
  run only what the task gives you, never edit `rules.yaml`, `state.json`
  or mail, never delete or move mail yourself, never install packages in the
  mount; when asked about the job, read `data/last-run-summary.md` and
  `ncl tasks get`.

### 5. Failure handling

| Failure | Behaviour |
|---|---|
| One message's action fails | Counted in `errors`, skipped, `last_run` does not pass it; retried next run; the digest wakes with the error. |
| Gmail `app_not_connected` or Graph `401` | Account skipped this run; `auth.<p>` set; reported once per six hours via `last_reported`. |
| Relay cannot refresh | systemd unit fails; the symptom in nanoclaw is the Graph 401 above. `systemctl --user status outlook-token-relay` shows the reason. |
| Gate budget | The runner stops taking new candidates at `--budget-seconds`; in-flight message operations complete; `capped: true` in the summary. The wrapper plus runner must finish well inside 30 s; the budget is 20 s. |
| Runner crash | `gate.py` catches everything and prints `{"wakeAgent": true, "data": {"error": ..., "digest_text": "⚠️ ..."}}`; exit 0 so nanoclaw never records a script failure; `last_run` untouched. |
| `rules.yaml` unparsable | As a crash; no account touched. |
| Rate limits (Gmail 429, Graph 429) | Honour `Retry-After` once within the budget, otherwise count as an error for that message and continue. |
| Clock or window oddities | Windows are computed from message timestamps returned by the provider, never from local assumptions; `last_run` never moves backwards. |
| Ubuntu or nanoclaw down | Nothing runs; on recovery the window spans the gap and drains at 40 per fire. |

### 6. Testing

- **Unit, `tests/runner/`** (pytest, synthetic fixtures only):
  - rules: first match wins; sender vs subject; header rule skipped without
    headers; invalid rules file rejected.
  - policy: every action × provider cell of the Section 2 table; unmatched;
    the `em/held` downgrade; `calendar_event` counted.
  - state: first-run backlog window; `last_run` advances only past successful
    messages; cap and carry-over; never moves backwards; `last_reported`
    suppression at six hours.
  - gate: JSON last line for quiet, report-worthy, crash and auth cases;
    `digest_text` present whenever `wakeAgent` is true; a fake provider that
    sleeps is cut off at the budget.
  - providers (with `respx` mocked transport): Gmail list with paging, label
    ensure (existing and missing), modify add/remove; Graph list with
    `$filter`, mark read, move, folder ensure; 401/429 mapping.
  - digest: rendering of each section, empty sections omitted.
- **Relay, `tests/relay/`**: token exchange and rotated refresh token
  persisted atomically; create-then-update decision for the OneCLI secret
  (subprocess mocked); non-zero exit and no file change on failure.
- **Live, in order**: `run.sh --dry-run --max 1000` against both accounts,
  reviewed; the backlog sweep via a one-off task; one observed 15-minute
  fire; then an induced Outlook failure (rename `outlook-token.json`, wait
  for the next refresh to fail and the next fire to report once), restore,
  confirm recovery and that the error is not repeated within six hours.

## Files touched

In this repository: `runner/` (new), `relay/` (new), `scripts/run.sh` (new),
`requirements-runner.txt` (new), `tests/runner/`, `tests/relay/` (new),
`.gitignore`, `README.md`, `autonomous-processing.md` (one note),
`rules.yaml` only if validation finds a malformed entry (none expected).

Outside: `/home/gethi/email-manager` clone and host venv; two systemd user
units; two files under `~/.config/email-manager/`; one OneCLI secret and one
grant; NanoClaw mount allowlist, Dr Claw's container config and standing
instructions, one recurring task.

## Assumptions

Status: **verified**, **measured**, or **unverified**, each with the cost if
wrong.

- A generic OneCLI secret for `graph.microsoft.com` with `Bearer {value}` is
  injected for Dr Claw. **Verified** today (dummy token reached Microsoft).
- Gmail calls from Dr Claw's container are authenticated by the connection.
  **Verified** today (profile returned).
- `onecli secrets create/update` and `agents grants attach-secret` work
  non-interactively from the host. **Verified** today for create, attach and
  detach/delete; `update --id` exists in the CLI but was not exercised. If
  wrong: delete-and-recreate instead, which costs one extra grant per refresh.
- The Azure app registration still exists, accepts the `consumers` tenant,
  and has `http://localhost:3333/auth/callback` as a redirect URI.
  **Unverified** until the owner recovers its client secret. If wrong: add
  the URI or a new secret in the Azure portal; no design change.
- Microsoft consumer accounts rotate the refresh token on every refresh and
  the old one is then invalid. **Unverified** on this app; the relay always
  stores whatever token the response carries, which is correct either way.
- A browser on Windows reaches `127.0.0.1:3333` inside WSL for the one-time
  authorize callback. **Measured** today for 8099 and 10254 (same mechanism).
- The runner finishes a 40-message batch inside 20 s through the proxy.
  **Unverified**; label and move calls are single round-trips, so 40 messages
  is roughly 80–120 calls. If wrong: lower `--max`; the carry-over makes any
  cap correct, only slower to drain.
- Gmail's `q=after:<unix>` and Graph's `receivedDateTime ge` honour the
  timestamps the runner stores. **Verified** by API documentation; both
  accept the formats the design uses.
- The proxy passes `login.microsoftonline.com` through for the host relay.
  Not needed: the relay runs on the host without the proxy. **n/a**.
- NanoClaw's gate contract (JSON last line, 30 s, exit 0 = no backoff).
  **Verified** from `container/agent-runner/src/scheduling/task-script.ts`.
- `em/*` labels from the previous Gmail setup still exist and carry the same
  names. **Unverified**; the runner's ensure step creates any missing label,
  so the cost is only that the names must match the rules file, which they
  do by construction.
- The owner wants `em/review` applied to every unmatched Gmail message.
  **Verified** from `autonomous-processing.md`; it is the documented policy.
