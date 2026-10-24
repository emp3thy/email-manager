# NanoClaw Inbox Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic Python runner, executed by Dr Claw four times a day inside NanoClaw, classifies new Gmail and Outlook.com mail with `rules.yaml`, applies only safe actions, and has Dr Claw post a Slack digest only when there is something to report; a host-side relay keeps the Outlook credential fresh in OneCLI.

**Architecture:** `runner/` (fetch, match, act, summarise, verdict) runs in Dr Claw's container through `scripts/run.sh`, calling the Gmail and Graph REST APIs with no credentials of its own because the OneCLI proxy injects them. `relay/` runs on the Ubuntu host under a systemd timer, refreshing a Microsoft token and writing it into a OneCLI custom secret granted to Dr Claw. A recurring NanoClaw task tells Dr Claw to run the wrapper and relay the digest file when the runner's last line says `VERDICT: REPORT`.

**Tech Stack:** Python 3.12+ (Windows dev 3.13, container 3.13, Ubuntu host 3.14), `httpx`, `pyyaml`, `python-dateutil`, pytest + `respx`, bash, NanoClaw v2 (`ncl`, `scripts/q.ts`), OneCLI CLI 2.11, systemd user units.

**Spec:** `docs/superpowers/specs/2026-10-24-nanoclaw-inbox-processing-design.md`

## Global Constraints

- The runner never deletes mail, never moves anything to Deleted Items, and never writes `rules.yaml`. Trash rules become label + `em/held` (Gmail) or mark-read + "held" count (Outlook).
- Rule matching: case-insensitive glob (`*`, `?`) on `sender` (From header) or `subject`; `header` rules match the header named by `header_name` and apply to Gmail only; a `sender_pattern` on a subject rule is an additional AND condition on the sender; first match in file order wins.
- Labels/folders: Gmail `em/important em/newsletters em/notifications em/promotions em/receipts em/spam em/held em/review`; Outlook folders `em-important em-newsletters em-notifications em-promotions em-receipts` (the label's `em/` → `em-`); no `em-spam`, no `em-review` folder.
- Defaults: `--max 500`, `--budget-seconds 600`, backlog 7 days on an account's first run, error re-report suppression 6 hours.
- Runner stdout contract: last two lines `DIGEST: data/last-digest.txt` then `VERDICT: REPORT` or `VERDICT: QUIET`; exit 0 on a completed run, 1 on a crash (still printing a `REPORT` verdict).
- Credentials: none in the repo, the mount, prompts or container env. Host files `~/.config/email-manager/relay.env` and `outlook-token.json`, mode 0600. OneCLI secret name `outlook-graph-relay`, host `graph.microsoft.com`, path `/*`, header `Authorization`, format `Bearer {value}`. Dr Claw's OneCLI agent id `4ae0ed0c-a511-4ef5-be2a-06d0c5b489fe`; NanoClaw group `ag-1792842294321-ro8wyt`; destination `gethin`.
- Container paths: mount `/home/gethi/email-manager` → `/workspace/extra/email-manager` (container path `email-manager`, stored with `"readonly": false`). Task `inbox-processing`, recurrence `0 7,12,17,22 * * *`.
- Python packages for the runner come from `requirements-runner.txt`; tests use a dev venv `.venv-dev` on Windows (`.venv*/` is git-ignored). Test command: `.venv-dev\Scripts\python -m pytest tests -q`.
- Windows↔WSL: run Ubuntu steps as `wsl.exe -d Ubuntu -u gethi -- bash /mnt/c/Users/gethi/AppData/Local/Temp/<script>.sh`; never inline quoted bash. Run spikes or probes under Dr Claw's identity without the agent (docker run with its container env), because Dr Claw refuses diagnostics that ask it to stay silent.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; branch `nanoclaw-inbox`.

## Review Focus

1. A message whose From header has a display name (`"Acme Billing" <no-reply@acme.com>`): sender rules must match against the address, and the digest should show the display name. Test in Task 1 (match) and Task 6 (digest).
2. A Gmail message already archived but matching a rule (not in INBOX): it must not be re-labelled or counted; the `in:inbox` query plus the `em/*` skip handle it. Test in Task 4.
3. An Outlook message the owner read themselves within the window: skipped as "already processed" by design; the summary must not count it. Test in Task 5 (filter is server-side `isRead eq false`) and Task 6 (no double count).
4. The same message returned twice across pages (provider pagination overlap): processed once. Test in Task 6.
5. `state.json` missing or half-written (crash mid-save): load must not raise; a `.tmp` sibling is ignored; first run becomes a backlog run. Test in Task 3.

---

### Task 1: Project scaffold and the rules engine

**Confidence:** 95%

**Files:**
- Create: `runner/__init__.py`, `runner/models.py`, `runner/rules.py`, `requirements-runner.txt`, `requirements-dev.txt`, `tests/runner/__init__.py`, `tests/runner/conftest.py`
- Test: `tests/runner/test_rules.py`
- Modify: `.gitignore` (add `.venv*/`, `data/`)

**Interfaces:**
- Produces `runner/models.py`:
  ```python
  @dataclass(frozen=True)
  class Message:
      id: str
      account: str            # "gmail" | "outlook"
      sender: str             # raw From header, e.g. '"Acme" <a@b.com>'
      subject: str
      received: datetime      # tz-aware UTC
      headers: dict[str, str] | None = None   # lower-cased names; Gmail only
      def sender_address(self) -> str: ...     # 'a@b.com' (lower-case), or sender lowered if no <>
      def sender_display(self) -> str: ...     # 'Acme', or the address when no display name
  ```
- Produces `runner/rules.py`:
  ```python
  class RulesError(ValueError): ...
  @dataclass(frozen=True)
  class Rule:
      pattern: str; match_field: str; label: str; action: str
      header_name: str | None = None; sender_pattern: str | None = None
  ACTIONS = ("keep", "archive", "trash", "calendar_event")
  FIELDS = ("sender", "subject", "header")
  def glob_match(pattern: str, value: str) -> bool        # fnmatch.fnmatchcase on both lower-cased
  def load_rules(path: Path) -> list[Rule]                 # raises RulesError on missing/unknown fields
  def match(message: Message, rules: list[Rule]) -> Rule | None
  ```
  Matching: `sender` → `glob_match(pattern, message.sender_address())` or against the whole lowered From string (either suffices); `subject` → against subject, and if `sender_pattern` is set it must also glob-match the sender; `header` → `message.headers` must be present and contain `header_name.lower()`, and the pattern matches its value (`"*"` = presence). Unknown keys such as `last_matched`, `retention_days`, `calendar_title` are ignored.

- [ ] **Step 1: Dev environment and scaffold**

Create `requirements-runner.txt` (`httpx>=0.27`, `pyyaml>=6.0`, `python-dateutil>=2.9`) and `requirements-dev.txt` (`-r requirements-runner.txt`, `pytest>=8`, `respx>=0.21`). On Windows: `python -m venv .venv-dev && .venv-dev\Scripts\pip install -q -r requirements-dev.txt`. Add `.venv*/` and `data/` to `.gitignore`. Create empty `runner/__init__.py`, `tests/runner/__init__.py`, and `tests/runner/conftest.py` containing a `msg()` factory fixture that builds a `Message` with defaults (`account="gmail"`, `received=datetime(2026,10,24,9,0,tzinfo=timezone.utc)`) and keyword overrides.

- [ ] **Step 2: Write the failing tests in `tests/runner/test_rules.py`**

```python
def test_sender_rule_matches_address_inside_display_name(msg):
    rules = [Rule("*@acme.com", "sender", "em/notifications", "keep")]
    assert match(msg(sender='"Acme Billing" <no-reply@acme.com>'), rules) is rules[0]

def test_glob_is_case_insensitive_and_supports_question_mark():
    assert glob_match("news?letter@EXAMPLE.com", "NEWSLETTER@example.com") is False  # '?' is one char: 'news'+'?'+'letter' needs 11+ chars
    assert glob_match("*@Example.COM", "x@example.com") is True
    assert glob_match("order ?onfirmed*", "Order Confirmed #42") is True

def test_first_match_wins_in_file_order(msg):
    rules = [Rule("*@acme.com", "sender", "em/promotions", "trash"),
             Rule("*@acme.com", "sender", "em/important", "keep")]
    assert match(msg(sender="a@acme.com"), rules).label == "em/promotions"

def test_subject_rule_with_sender_pattern_requires_both(msg):
    r = Rule("*invoice*", "subject", "em/receipts", "keep", sender_pattern="*@shop.example")
    assert match(msg(subject="Your invoice", sender="billing@shop.example"), [r]) is r
    assert match(msg(subject="Your invoice", sender="billing@other.example"), [r]) is None

def test_header_rule_needs_headers_and_header_name(msg):
    r = Rule("*", "header", "em/newsletters", "archive", header_name="List-Unsubscribe")
    assert match(msg(headers={"list-unsubscribe": "<mailto:x>"}), [r]) is r
    assert match(msg(headers=None), [r]) is None
    assert match(msg(headers={"precedence": "bulk"}), [r]) is None

def test_load_rules_rejects_unknown_action_and_missing_field(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text("rules:\n- pattern: '*'\n  match_field: sender\n  label: em/x\n  action: delete\n")
    with pytest.raises(RulesError): load_rules(bad)
    bad.write_text("rules:\n- pattern: '*'\n  label: em/x\n  action: keep\n")
    with pytest.raises(RulesError): load_rules(bad)

def test_load_rules_ignores_bookkeeping_keys(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text("settings: {privacy_mode: headers_first}\nrules:\n- pattern: '*@a.b'\n  match_field: sender\n  label: em/receipts\n  action: keep\n  last_matched: '2026-01-01'\n  retention_days: 7\n")
    rules = load_rules(p)
    assert len(rules) == 1 and rules[0].action == "keep"

def test_repo_rules_file_loads_if_present():
    p = Path(__file__).resolve().parents[2] / "rules.yaml"
    if not p.exists(): pytest.skip("no rules.yaml in checkout")
    rules = load_rules(p)
    assert len(rules) > 100 and all(r.action in ACTIONS for r in rules)
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv-dev\Scripts\python -m pytest tests/runner/test_rules.py -q`
Expected: collection error / `ImportError` on `runner.rules`.

- [ ] **Step 4: Implement `runner/models.py` and `runner/rules.py` to the interfaces above**

`sender_address`: use `email.utils.parseaddr`. `load_rules`: `yaml.safe_load`, require a `rules` list, require `pattern`, `match_field in FIELDS`, `label`, `action in ACTIONS`, and `header_name` when `match_field == "header"`; build frozen `Rule`s.

- [ ] **Step 5: Run to verify pass**

Run: `.venv-dev\Scripts\python -m pytest tests/runner/test_rules.py -q`
Expected: 8 passed (or 7 passed, 1 skipped without a rules file).

- [ ] **Step 6: Commit**

```bash
git add runner requirements-runner.txt requirements-dev.txt tests/runner .gitignore
git commit -m "feat(runner): scaffold, message model and glob rules engine"
```

---

### Task 2: Autonomous policy

**Confidence:** 96%

**Files:**
- Create: `runner/policy.py`
- Test: `tests/runner/test_policy.py`

**Interfaces:**
- Consumes `Rule` from Task 1.
- Produces:
  ```python
  Op = tuple[str, str]   # ("add_label", "em/x") | ("remove_from_inbox", "") | ("mark_read", "") | ("move_to_folder", "em-x")
  BUCKETS = ("keep", "archive", "held", "calendar_review", "review")
  @dataclass(frozen=True)
  class Decision:
      ops: tuple[Op, ...]
      bucket: str          # one of BUCKETS
      label: str | None    # the rule's label, None when unmatched
      important: bool      # label == "em/important"
  def folder_for(label: str) -> str          # "em/receipts" -> "em-receipts"
  def decide(rule: Rule | None, provider: str) -> Decision   # provider "gmail" | "outlook"
  ```

- [ ] **Step 1: Write the failing tests in `tests/runner/test_policy.py`** — one parametrised test over the full table from spec Section 2:

| rule action | provider | expected ops | bucket |
|---|---|---|---|
| keep | gmail | (add_label em/L) | keep |
| keep | outlook | (mark_read) | keep |
| archive | gmail | (add_label em/L), (remove_from_inbox) | archive |
| archive | outlook | (move_to_folder em-L) | archive |
| trash | gmail | (add_label em/L), (add_label em/held) | held |
| trash | outlook | (mark_read) | held |
| calendar_event | gmail | (add_label em/L) | calendar_review |
| calendar_event | outlook | (mark_read) | calendar_review |
| None | gmail | (add_label em/review) | review |
| None | outlook | (mark_read) | review |

plus `test_important_flag_only_for_em_important`, `test_folder_for_maps_slash_to_dash`, and `test_decide_never_emits_delete_or_trash_ops` (asserts no op name contains "delete" or "trash" for every cell).

- [ ] **Step 2: Run to verify failure** — Run: `.venv-dev\Scripts\python -m pytest tests/runner/test_policy.py -q`. Expected: `ImportError` on `runner.policy`.

- [ ] **Step 3: Implement `runner/policy.py`** as a pure table; no I/O.

- [ ] **Step 4: Run to verify pass** — Expected: all passed.

- [ ] **Step 5: Commit** — `git commit -m "feat(runner): autonomous action policy table"`.

---

### Task 3: State

**Confidence:** 95%

**Files:**
- Create: `runner/state.py`
- Test: `tests/runner/test_state.py`

**Interfaces:**
- Produces:
  ```python
  class State:
      path: Path
      data: dict                                   # the JSON document
      @classmethod
      def load(cls, path: Path) -> "State"         # missing/unparsable file → fresh document, never raises
      def save(self) -> None                       # write path.with_suffix(".json.tmp") then os.replace
      def accounts(self) -> list[str]              # autonomous.accounts, default ["gmail", "outlook"]
      def backlog_days(self) -> int                # autonomous.backlog_days, default 7
      def last_run(self, account: str) -> datetime | None
      def window_start(self, account: str, now: datetime) -> datetime   # last_run or now - backlog_days
      def advance(self, account: str, ts: datetime) -> None             # only if ts > last_run
      def should_report(self, condition: str, now: datetime, hours: int = 6) -> bool
      def mark_reported(self, condition: str, now: datetime) -> None
  ```
  Fresh document: `{"accounts": {"gmail": {"last_run": None, "initial_cleanup": {"status": "not_started", "emails_processed": 0}}, "outlook": {...}}, "autonomous": {"enabled": True, "accounts": ["gmail","outlook"], "last_run": None, "backlog_days": 7, "digest": {"channel": "slack", "target": "gethin"}, "last_reported": {}}}`. Timestamps stored as ISO-8601 UTC with `Z`.

- [ ] **Step 1: Write the failing tests**

`test_load_missing_file_gives_fresh_document_with_both_accounts`, `test_load_unparsable_file_gives_fresh_document_and_keeps_backup` (the bad file is renamed to `state.json.corrupt-<ts>`), `test_load_ignores_tmp_sibling`, `test_window_start_uses_backlog_when_no_last_run` (now − 7 days), `test_window_start_uses_last_run`, `test_advance_never_moves_backwards`, `test_save_is_atomic_no_tmp_left_behind`, `test_should_report_suppresses_within_six_hours_then_allows`, `test_existing_documented_shape_is_preserved` (load the example from `autonomous-processing.md`'s state block, save, reload, same keys).

- [ ] **Step 2: Run to verify failure** — `ImportError` on `runner.state`.
- [ ] **Step 3: Implement `runner/state.py`.**
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(runner): atomic state with backlog window and report suppression"`.

---

### Task 4: Provider interface and the Gmail client

**Confidence:** 92%. Gmail REST shapes are well documented; `respx` fixtures pin them.

**Files:**
- Create: `runner/providers/__init__.py`, `runner/providers/base.py`, `runner/providers/gmail.py`
- Test: `tests/runner/test_gmail.py`

**Interfaces:**
- Produces `runner/providers/base.py`:
  ```python
  class AuthError(Exception):            # account unusable this run
      def __init__(self, message: str, connect_url: str | None = None): ...
  class TransientError(Exception): ...   # per-message failure after retries
  class Provider(Protocol):
      name: str
      def ensure_labels(self) -> None: ...
      def list_since(self, start: datetime, limit: int) -> list[Message]: ...  # oldest first, ≤ limit, deduplicated by id
      def apply(self, message_id: str, op: Op) -> None: ...
  def request_with_retry(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response
      # raises AuthError on 401/403 (parsing a JSON body with "connect_url"/"secret_url" when present),
      # retries 429/503 up to 3 times honouring Retry-After (seconds, default 2), then TransientError
  ```
- Produces `runner/providers/gmail.py`: `class GmailProvider(Provider)` with `BASE = "https://gmail.googleapis.com/gmail/v1/users/me"`, constructor `(client: httpx.Client | None = None, header_names: list[str] = ())` (header names a header rule needs; passed from the rules at startup). `list_since` uses `GET /messages?q=after:<unix> in:inbox&maxResults=100&pageToken=…`, then per id `GET /messages/{id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date&metadataHeaders=<each header_name>`, skips any message whose `labelIds` contains a label whose name starts with `em/` (resolve ids via the label map from `ensure_labels`), returns `Message`s with `received` from `internalDate` (ms epoch). `ensure_labels` lists labels, creates missing `em/*` names from the Global Constraints list, caches `name→id`. `apply`: `add_label` → `POST /messages/{id}/modify {"addLabelIds":[id]}`; `remove_from_inbox` → `{"removeLabelIds":["INBOX"]}`; other ops raise `ValueError`.

- [ ] **Step 1: Write the failing tests with `respx`**

`test_ensure_labels_creates_only_missing` (labels list has 6 of 8; expect exactly 2 POST /labels), `test_list_since_pages_and_dedupes_and_orders_oldest_first` (two pages, one id repeated, `internalDate` out of order), `test_list_since_skips_messages_already_carrying_em_label`, `test_list_since_query_has_after_and_in_inbox` (assert the `q` param), `test_metadata_request_includes_rule_header_names`, `test_apply_add_label_and_remove_from_inbox_bodies`, `test_401_app_not_connected_raises_auth_error_with_connect_url`, `test_429_retries_then_transient_error` (3× 429 → `TransientError`; 2× 429 then 200 → success), `test_message_received_is_utc_from_internal_date`.

- [ ] **Step 2: Run to verify failure** — `ImportError`.
- [ ] **Step 3: Implement `base.py` then `gmail.py`.**
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(runner): provider interface and Gmail REST client"`.

---

### Task 5: Microsoft Graph client

**Confidence:** 92%

**Files:**
- Create: `runner/providers/graph.py`
- Test: `tests/runner/test_graph.py`

**Interfaces:**
- Consumes `Provider`, `request_with_retry`, errors from Task 4.
- Produces `class GraphProvider(Provider)` with `BASE = "https://graph.microsoft.com/v1.0/me"`. `ensure_labels` lists `GET /mailFolders?$top=100`, creates missing folders from the Global Constraints list via `POST /mailFolders {"displayName": …}`, caches `displayName→id`. `list_since`: `GET /mailFolders/inbox/messages?$filter=receivedDateTime ge <iso Z> and isRead eq false&$orderby=receivedDateTime asc&$top=50&$select=id,receivedDateTime,from,subject,isRead`, follows `@odata.nextLink` until `limit`, dedupes by id, builds `sender` as `"<name>" <address>` from `from.emailAddress`. `apply`: `mark_read` → `PATCH /messages/{id} {"isRead": true}`; `move_to_folder` → `POST /messages/{id}/move {"destinationId": folderId}`; other ops raise `ValueError`.

- [ ] **Step 1: Write the failing tests** — `test_ensure_folders_creates_only_missing`, `test_list_since_filter_orderby_and_paging` (assert `$filter` text, follow one nextLink), `test_sender_built_from_from_email_address`, `test_apply_mark_read_and_move_bodies`, `test_401_raises_auth_error`, `test_429_honours_retry_after`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement `graph.py`.**
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(runner): Microsoft Graph client"`.

---

### Task 6: Orchestration, digest, verdict and CLI

**Confidence:** 93%

**Files:**
- Create: `runner/run.py`, `runner/digest.py`, `runner/__main__.py`
- Test: `tests/runner/test_run.py`, `tests/runner/test_digest.py`, `tests/runner/fakes.py`

**Interfaces:**
- Consumes everything above.
- Produces:
  ```python
  @dataclass
  class RunConfig:
      data_dir: Path; rules_path: Path; state_path: Path
      max_messages: int = 500; budget_seconds: float = 600.0
      dry_run: bool = False; accounts: list[str] | None = None
  @dataclass
  class AccountStats: fetched: int = 0; processed: int = 0; errors: int = 0; capped: bool = False
  @dataclass
  class Summary:
      started: str; finished: str; dry_run: bool
      accounts: dict[str, AccountStats]
      important: list[dict]          # {"account","from","subject"}  (from = sender_display)
      held: int; review: int; calendar_review: int
      errors: list[str]
      auth: dict[str, str]           # "ok" | "not_connected" | "unauthorized"
      verdict: str                   # "REPORT" | "QUIET"
      def to_json(self) -> str
  def run(cfg: RunConfig, providers: dict[str, Provider], state: State, rules: list[Rule], now: Callable[[], datetime] = utcnow) -> Summary
  # digest.py
  def render(summary: Summary, local_tz: str = "Europe/London") -> str
  def decide_verdict(summary: Summary, state: State, now: datetime) -> str   # also calls state.mark_reported for auth conditions it reports
  # __main__.py
  def main(argv: list[str]) -> int
  ```
  `main` parses `--max`, `--budget-seconds`, `--dry-run`, `--accounts gmail,outlook`, `--data-dir` (default `<repo>/data`), `--rules` (default `<repo>/rules.yaml`), `--state` (default `<repo>/state.json`); builds providers (`GmailProvider(header_names=…)`, `GraphProvider()`); calls `run`; writes `data/runs/<started>.json`, `data/last-digest.txt`, appends to `data/last-run-summary.md`; saves state; prints `DIGEST: data/last-digest.txt` and `VERDICT: <v>`; returns 0. Any exception: writes an error digest `⚠️ Email run HH:MM: <type>: <message>. <N> messages processed before the error.`, prints the two lines with `VERDICT: REPORT`, returns 1; state is saved only if `run` returned.

  Run semantics (spec Section 2): per account in `cfg.accounts or state.accounts()`: `ensure_labels()` (an `AuthError` here marks `auth[account]` and skips the account), `list_since(window_start, max_messages)`, then for each message in order: stop if budget exceeded (`capped=True`); `rule = match(...)`; `decision = decide(rule, account)`; unless `dry_run`, apply each op, catching `TransientError`/`httpx.HTTPError` → `errors.append(f"{account}: {op[0]} {id}: {exc}")`, `stats.errors += 1`, and do not advance past it; on success `stats.processed += 1`, bucket counters, `important.append` when `decision.important`, and `state.advance(account, message.received)`. Messages are deduplicated by id within a run. If the account completed without a cap and without errors, `state.advance(account, started)`.

- [ ] **Step 1: Write `tests/runner/fakes.py`** — `FakeProvider(name, messages, fail_ops: set[str] = (), auth_error: AuthError | None = None, delay: float = 0)` recording `applied: list[tuple[str, Op]]`.

- [ ] **Step 2: Write the failing tests in `test_run.py`**

`test_first_run_uses_backlog_window` (fake asserts `start == now − 7d`), `test_cap_sets_capped_and_last_run_stops_at_last_processed`, `test_failed_op_does_not_advance_last_run_and_is_retried_shape` (second run sees the same window start), `test_complete_run_advances_to_started`, `test_dry_run_applies_nothing_but_counts`, `test_auth_error_skips_account_and_sets_auth`, `test_duplicate_ids_processed_once`, `test_budget_cutoff_marks_capped` (delay fake, budget 0.01), `test_important_entries_use_display_name`, `test_summary_json_roundtrip`.

Tests in `test_digest.py`: `test_render_matches_spec_format_with_all_sections`, `test_render_omits_empty_sections`, `test_render_error_form`, `test_verdict_quiet_when_nothing_to_say`, `test_verdict_report_on_each_trigger` (parametrised over important/held/review/calendar_review/errors/auth), `test_auth_condition_suppressed_within_six_hours`.

Tests for `main` (in `test_run.py`, using monkeypatched providers): `test_main_prints_digest_and_verdict_lines_last`, `test_main_crash_writes_error_digest_and_returns_1`, `test_main_writes_run_json_and_appends_summary_md`.

- [ ] **Step 3: Run to verify failure.**
- [ ] **Step 4: Implement `run.py`, `digest.py`, `__main__.py`.** Digest exact format (spec Section 4); times rendered in `Europe/London`.
- [ ] **Step 5: Run the whole suite** — `.venv-dev\Scripts\python -m pytest tests -q`. Expected: all passed.
- [ ] **Step 6: Commit** — `git commit -m "feat(runner): orchestration, digest, verdict and CLI"`.

---

### Task 7: Outlook token relay

**Confidence:** 93%. Microsoft's endpoints and OneCLI's commands were both exercised live today; only the glue is new.

**Files:**
- Create: `relay/__init__.py`, `relay/config.py`, `relay/tokens.py`, `relay/ms.py`, `relay/onecli.py`, `relay/__main__.py`, `systemd/outlook-token-relay.service`, `systemd/outlook-token-relay.timer`
- Test: `tests/relay/__init__.py`, `tests/relay/test_tokens.py`, `tests/relay/test_ms.py`, `tests/relay/test_onecli.py`, `tests/relay/test_cli.py`

**Interfaces:**
```python
# config.py
@dataclass(frozen=True)
class RelayConfig: client_id: str; client_secret: str; tenant: str; config_dir: Path
def load_config(config_dir: Path = Path("~/.config/email-manager").expanduser()) -> RelayConfig   # reads relay.env KEY=VALUE lines
# tokens.py
class TokenStore:
    def __init__(self, path: Path): ...
    def load(self) -> dict | None
    def save(self, doc: dict) -> None        # tmp + os.replace, mode 0600
# ms.py
AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPES = "offline_access User.Read Mail.ReadWrite"
REDIRECT_URI = "http://localhost:3333/auth/callback"
def refresh(cfg: RelayConfig, refresh_token: str, client: httpx.Client | None = None) -> dict   # {"access_token","refresh_token","expires_in"}; raises RelayError(error, description)
def authorize(cfg: RelayConfig, open_browser: Callable[[str], None] = print_url, client: httpx.Client | None = None) -> dict   # PKCE + local server on 127.0.0.1:3333, 15-minute wait
# onecli.py
SECRET_NAME = "outlook-graph-relay"
DR_CLAW_AGENT_ID = "4ae0ed0c-a511-4ef5-be2a-06d0c5b489fe"
def upsert_secret(value: str, secret_id: str | None, run: Callable = subprocess.run) -> str
    # None → `onecli secrets create --name outlook-graph-relay --type generic --value <v> --host-pattern graph.microsoft.com --path-pattern '/*' --header-name Authorization --value-format 'Bearer {value}'`, parse JSON for "id", then `onecli agents grants attach-secret --id <agent> --secret-id <id>`
    # str  → `onecli secrets update --id <id> --value <v>`; if that fails with a not-found error, fall back to create+attach
# __main__.py: `python -m relay authorize | refresh | status`; refresh exits 1 on any failure with the reason on stderr
```
Token document: `{"refresh_token": str, "scope": str, "saved_at": iso, "secret_id": str | None, "source": "authorize" | "refresh"}`. `refresh` command: load → `ms.refresh` → `store.save` with the rotated token **before** touching OneCLI → `upsert_secret` → save again with `secret_id`.

- [ ] **Step 1: Write the failing tests**

`test_tokens`: save creates 0600 and no tmp; load None when missing. `test_ms`: `refresh` posts `grant_type=refresh_token` with client id/secret/scope and returns the rotated token; non-200 raises `RelayError` with Microsoft's `error` code; `authorize` builds a URL containing `code_challenge_method=S256` and the redirect URI, and exchanges the code with `code_verifier` (drive the local server with an in-test `httpx` GET to `http://127.0.0.1:<port>/auth/callback?code=…&state=…`; use a port override parameter defaulting to 3333). `test_onecli`: `upsert_secret(None)` runs create then attach with the exact argument lists above; `upsert_secret("abc")` runs update only; update "not found" falls back to create+attach. `test_cli`: `refresh` saves the rotated token even if OneCLI fails afterwards, and exits 1; success path saves `secret_id`.

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement the five modules.**
- [ ] **Step 4: Run to verify pass** — `.venv-dev\Scripts\python -m pytest tests/relay -q`.
- [ ] **Step 5: Write the systemd units** under `systemd/` in the repo (installed by Task 9):

```ini
# outlook-token-relay.service
[Unit]
Description=Refresh the Outlook token into OneCLI for email-manager
[Service]
Type=oneshot
WorkingDirectory=/home/gethi/email-manager
ExecStart=/home/gethi/email-manager/.venv-host/bin/python -m relay refresh
Environment=PATH=/home/gethi/.local/bin:/usr/local/bin:/usr/bin:/bin
```
```ini
# outlook-token-relay.timer
[Unit]
Description=Refresh the Outlook token every 30 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Persistent=true
[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Commit** — `git commit -m "feat(relay): Outlook token relay with OneCLI secret upsert and systemd units"`.

---

### Task 8: Wrapper script, README and autonomous-processing note

**Confidence:** 95%. The wrapper is the battery one with three substitutions; its test harness already exists.

**Files:**
- Create: `scripts/run.sh`
- Modify: `README.md` (new section after "Setup"), `autonomous-processing.md` (note at top)

**Interfaces:**
- Produces `scripts/run.sh [args…]`: identical to weatherToBattery's `scripts/run-nightly.sh` (copy it from `C:\Users\gethi\sources\weathertobattery\scripts\run-nightly.sh`) with: `requirements-runner.txt` instead of `requirements.txt`; `"$VENV/bin/python" -m runner "$@"` instead of the nightly script; log file `data/runner.log`; and it prints the script's **last two** lines (`tail -n 2`) so both `DIGEST:` and `VERDICT:` reach the caller. Executable bit committed.

- [ ] **Step 1: Write the wrapper** as described; `git update-index --chmod=+x scripts/run.sh`.
- [ ] **Step 2: Verify on the Ubuntu host with the existing harness pattern** (a temp copy with a stub `runner` package that prints `DIGEST: data/last-digest.txt` and `VERDICT: QUIET`): from `/`, stdout is exactly those two lines and exit 0; a bogus `requirements-runner.txt` gives exit 1 with pip's error in `data/runner.log` and no `.venv` left; a second run with a good file recovers and stamps.
- [ ] **Step 3: Docs** — README "Running under NanoClaw" (what runs where, the four daily times, the verdict contract, the relay and its two host files, how to re-authorise); a 4-line note at the top of `autonomous-processing.md` saying the scheduler, MCP tools and WhatsApp digest it describes are superseded on this install by `runner/` and the Slack digest, with the policy tables still authoritative.
- [ ] **Step 4: Commit** — `git commit -m "feat: run.sh wrapper for NanoClaw and docs"`.

---

### Task 9: Deploy to Ubuntu, relay live, mount into Dr Claw, dry-run sweep

**Confidence:** 92%. Every command was used for the battery job today; the relay's first live refresh is the only new live step and its inputs were verified.

**Files:**
- Ubuntu: `/home/gethi/email-manager` (clone), `.venv-host`, `~/.config/systemd/user/outlook-token-relay.{service,timer}`, `~/nanoclaw/groups/dm-with-gethin/instructions.prepend.md` (append), mount allowlist, Dr Claw container config.

**Interfaces:**
- Consumes the pushed branch `nanoclaw-inbox`; `relay.env` and `outlook-token.json` already present on the host.
- Produces: a running relay timer, the OneCLI secret `outlook-graph-relay` granted to Dr Claw, the mount live in Dr Claw's container, and a reviewed dry-run summary under `data/runs/`.

- [ ] **Step 1: Push and clone** — `git push -u origin nanoclaw-inbox`; in Ubuntu `git clone … ~/email-manager && git checkout nanoclaw-inbox`; `python3 -m venv .venv-host && .venv-host/bin/pip install -q -r requirements-runner.txt`. Expected: `~/email-manager/rules.yaml` present, no `state.json`.
- [ ] **Step 2: Relay live** — `.venv-host/bin/python -m relay refresh`; expected exit 0, `onecli secrets list` shows `outlook-graph-relay`, `onecli agents grants list --id 4ae0ed0c-…` shows it, `outlook-token.json` now has `secret_id`. Then the agent-free probe (docker run with Dr Claw's container env, as in today's spikes) to `https://graph.microsoft.com/v1.0/me` returns 200. Install and enable the units: copy from `systemd/`, `systemctl --user daemon-reload && systemctl --user enable --now outlook-token-relay.timer`; `systemctl --user list-timers` shows it.
- [ ] **Step 3: Mount and instructions** — allowlist JSON now lists **both** roots (weathertobattery and email-manager, both `allowReadWrite: true`); `ncl groups config add-mount --id <DC> --host /home/gethi/email-manager --container email-manager`; set `additional_mounts` via `scripts/q.ts` to the two-entry array with `"readonly": false` on both; append the "Inbox processing" section (spec Section 4) to `instructions.prepend.md`; `ncl groups restart --id <DC>`. Expected: a one-off mount check (as for the battery job) shows `/workspace/extra/email-manager/rules.yaml` from inside the container.
- [ ] **Step 4: Dry-run sweep from Dr Claw** — one-off task with the spec's prompt but the command `bash /workspace/extra/email-manager/scripts/run.sh --dry-run --max 2000`; it reports its digest to `gethin`. Expected: a Slack digest marked dry run; `data/runs/<ts>.json` with `dry_run: true`, both accounts `auth: ok`, no `errors`, and `fetched` counts for seven days; `state.json` created with `last_run` still null (dry run does not advance). Record the elapsed time from `runner.log` and the fetched counts in the completion message; if a 429 appears, record it.

---

### Task 10: Backlog sweep, schedule, observe, induced failure

**Confidence:** 90%. The only unknown is the real sweep's volume, bounded by the dry run just measured.

**Files:**
- NanoClaw state only: one-off sweep task, recurring task `inbox-processing`.

**Interfaces:**
- Consumes Task 9's deployed state.
- Produces: the live schedule and a recorded first fire.

- [ ] **Step 1: Real sweep** — same one-off task with `--max 2000` and no `--dry-run`. Expected: Slack digest; `state.json` `last_run` set for both accounts; `data/runs/` has the summary with `processed` ≈ dry-run `fetched`; spot-check in Gmail that a known-archive sender now has its label and is out of the inbox, and in Outlook that an `em-` folder gained messages; nothing in either trash.
- [ ] **Step 2: Schedule** — `ncl tasks create --group <DC> --name inbox-processing --recurrence "0 7,12,17,22 * * *" --prompt "<spec Section 4 prompt>"`. Expected: `ncl tasks list --all` shows it with the next fire at the next of 07:00/12:00/17:00/22:00 local.
- [ ] **Step 3: Observe the next fire** — after it: `completed_runs: 1`; either a Slack digest or no message, consistent with the verdict line in `data/runner.log`.
- [ ] **Step 4: Induced Outlook failure** — `mv ~/.config/email-manager/outlook-token.json{,.bak}`; `systemctl --user start outlook-token-relay.service` → fails; wait for the secret's access token to expire (≤ 90 min) or set the secret to a bogus value with `onecli secrets update`; the next fire's digest contains the "Outlook needs re-authorising" line once; restore the file, run the relay once, confirm the fire after that is clean and the warning was not repeated within six hours.

---

## Review Focus tests placement

- #1 → Task 1 `test_sender_rule_matches_address_inside_display_name`; Task 6 `test_important_entries_use_display_name`.
- #2 → Task 4 `test_list_since_query_has_after_and_in_inbox` and `test_list_since_skips_messages_already_carrying_em_label`.
- #3 → Task 5 `test_list_since_filter_orderby_and_paging` (asserts `isRead eq false`); Task 6 counts only returned messages.
- #4 → Task 4 `test_list_since_pages_and_dedupes_and_orders_oldest_first`; Task 6 `test_duplicate_ids_processed_once`.
- #5 → Task 3 `test_load_unparsable_file_gives_fresh_document_and_keeps_backup`, `test_load_ignores_tmp_sibling`.
