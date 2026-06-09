I have everything. The recon is complete and verified against actual code. Here is the deliverable.

---

# Phase-1 "Daily Brief" — Integration Surface Map & Wiring Plan

**Repo:** `/Users/willlambert/Documents/odysseus` · branch `bertos` (off `dev`) · READ-ONLY recon, nothing modified.

## TL;DR — the single most important finding

**The daily brief already exists as a built-in action.** `action_daily_brief` (`src/builtin_actions.py:986-1112`), registered as `"daily_brief"` in `BUILTIN_ACTIONS` (`src/builtin_actions.py:2217`) with description (`:2239`): *"Build a morning digest: today's calendar, unread email count + top senders, active todos."* It already gathers calendar + email + todos and returns formatted text. **You do not need to build the gather logic — it's done.** The Phase-1 work is narrower than the brief assumes: **register a cron task that runs this action and route its output to ntfy.** There is one real gap: action-type task output does not currently reach ntfy (see Gap A).

The other major correction: **the `free_only=True` guardrail does not exist anywhere in the codebase** (grep for `free_only`/`free_models`/`require_free` returns nothing). It is net-new Phase-1 work. But — see Guardrail section — for `daily_brief` specifically it is **moot**, because the action uses no LLM at all.

---

## 1. Scheduler: how tasks are defined, registered, invoked

**DB table:** `scheduled_tasks` → `ScheduledTask` model, `core/database.py:543-588`. Key columns: `task_type` ("llm"|"action"|"research"), `action` (builtin action name), `schedule` ("once"|"daily"|"weekly"|"monthly"|"cron"), `scheduled_time` ("HH:MM", stored UTC), `cron_expression`, `next_run` (indexed), `output_target` (default `"session"`), `owner`, `model`/`endpoint_url`. Runs are recorded in `task_runs` → `TaskRun` (`core/database.py:623-643`).

**Programmatic add — two paths:**
- **HTTP route (canonical):** `POST /api/tasks` → `create_task` (`routes/task_routes.py:453-552`). Body is `TaskCreate` pydantic (`routes/task_routes.py:136-149`); it validates, computes `next_run` via `compute_next_run`, constructs `ScheduledTask(...)` (`:522-546`), commits. For a cron task it requires `schedule="cron"` + a valid `cron_expression` (validated `:471-476`).
- **Direct ORM seeding (no HTTP/auth):** `TaskScheduler.ensure_defaults(owner)` (`src/task_scheduler.py:1947+`) seeds the `HOUSEKEEPING_DEFAULTS` dict (`:205-216`) idempotently per `action`. This is the precedent for adding a built-in seeded task — a new `"daily_brief"` entry here would auto-create the task for every owner. (Note `ship_paused: True` on the email housekeeping defaults — they seed paused.)

**Loop / dispatch:** `start()` (`:349`) launches `_loop()` (`:567`), which polls `_check_due_tasks()` (`:595`) — selects `ScheduledTask` rows where `status="active" AND next_run <= now`, dispatches each to `_execute_task` (`:620`) → `_execute_task_locked` (`:666`). Strict serial execution: `_run_semaphore = Semaphore(1)` (`:256`), one model-backed task at a time.

**How a task invokes the agent/tool** (`_execute_task_locked`, `:705-733`):
- `task_type == "action"` → `_execute_action` (`:1010`) → looks up `BUILTIN_ACTIONS.get(task.action)` and `await`s it. **No LLM, no model slot** (unless the action is in `_MODEL_BACKED_ACTIONS`, `:966-976` — `daily_brief` is **not** in that set, confirmed).
- `task_type == "llm"` → `_execute_llm_task` (`:1280`) → `_run_agent_loop` (`:1565`) → `stream_agent_loop` (full tool access).
- `task_type == "research"` → `_execute_research_task` (`:1671`) — this is where your **reference `resolve_endpoint` call lives** (`:1687-1694`): `resolve_endpoint("research", endpoint_url, model, None, owner=task.owner)`. That's the pattern for resolving an endpoint/model inside a scheduled LLM task.

**The closest existing precedent to a "daily brief" is `_execute_checkin`** (`:1097-1278`) — the assistant "check-in" path. It already gathers calendar (direct `CalendarEvent` DB query, `:1123-1156`), notes/tasks (`do_manage_notes`, `:1162`), RSS (Miniflux, `:1188-1213`), and MCP email (`CHECKIN_MCP_PATTERNS`, `:1044-1062`), then hands a data dump to the agent loop. It's triggered when a task is named "...check-in" and linked to the default-assistant crew (`:1333`). This is heavier (LLM-summarized) than `action_daily_brief` (deterministic text).

---

## 2. Notifications / ntfy

**Canonical dispatcher:** `dispatch_reminder(title, note_body, note_id, owner, ...)` — `routes/note_routes.py:138-549`. It reads `reminder_channel` from settings and routes to browser | email | ntfy | webhook. Returns `{channel, synthesis, email_sent, ntfy_sent, webhook_sent, browser_sent, ...}`.

**The actual ntfy push** — `routes/note_routes.py:464-490`:
```python
if channel == "ntfy":
    intg = next((i for i in load_integrations()
                 if i.get("preset") == "ntfy" and i.get("enabled", True) and i.get("base_url")), None)
    base = intg["base_url"].rstrip("/")
    topic = settings.get("reminder_ntfy_topic") or "reminders"
    hdrs = {"Title": title or "Reminder", "Priority": "high", "Tags": "bell"}
    # + Authorization: Bearer <api_key> if set
    resp = await client.post(f"{base}/{topic}", content=ntfy_body, headers=hdrs)
    ntfy_sent = resp.is_success
```
ntfy is configured as an **Integration** (preset `"ntfy"`, `src/integrations.py:93-103`), not a hardcoded URL. Base URL from `NTFY_BASE_URL` (`.env.example:108-112`, default `http://localhost:8091`); topic from `reminder_ntfy_topic` setting (default `"Reminders"`, `src/settings.py:146`).

**Channels (per README:24, 374):** browser (in-app `Notification(...)` via the scheduler's notification queue) · email (SMTP) · ntfy (port 8091) · webhook (generic, incl. Discord). `reminder_channel` default is `"browser"` (`src/settings.py:144`).

**THE CALL PATTERN TO COPY** — `action_check_email_urgency` (`src/builtin_actions.py:1903-1925`) calls `dispatch_reminder` **directly** (no HTTP):
```python
from routes.note_routes import dispatch_reminder
dispatch_result = await dispatch_reminder(title=title, note_body=body, note_id="urgent-email", owner=owner or "")
# then: delivered = bool(dispatch_result.get("ntfy_sent"))  # per channel
```
The inline comment (`:1900-1901`) states **why** direct-call is mandatory: *"the endpoint version 401's the background scheduler because it has no session cookie."* Any push from a scheduled task MUST call `dispatch_reminder` in-process, not hit the HTTP route.

---

## 3. Data sources (existing functions — no new code to fetch)

All three are already wired inside `action_daily_brief` (`src/builtin_actions.py:986-1112`), so reuse is "call the action." For reference / if you build a custom variant:

- **Calendar (today):** direct DB query — `db.query(CalendarEvent).join(CalendarCal).filter(CalendarEvent.dtstart < tomorrow, CalendarEvent.dtend > today, status != "cancelled")` scoped by `owner_filter(...)` (`:1010-1017`). `CalendarEvent` table = `core/database.py:1509`; source column `"local"|"caldav"` (`:1168`). CalDAV sync into this table lives in `src/caldav_sync.py`. The check-in path uses windowed buckets via `_digest_windows` (`task_scheduler.py:225-236`, `:1126-1154`).
- **Unread/urgent email:** `action_daily_brief` opens IMAP directly via `_imap_connect(None)` + `conn.search(None, "UNSEEN")` (`:1033-1038`) for count + top-5 headers. For *urgency triage* (LLM-scored, fires its own reminder): `action_check_email_urgency` (`src/builtin_actions.py:1443`). Route-level listing: `GET /api/emails` → `list_emails` (`routes/email_routes.py:967`, `filter="unread"`); IMAP helper `_imap_connect` (`routes/email_helpers.py:765`). Note: `_imap_connect(None)` in daily_brief uses the **default account only** — multi-account is not covered by the built-in action.
- **Open tasks / notes:** in-action it queries the `Note` table directly for non-archived checklists + pinned notes (`:1019-1076`). Generic tool: `do_manage_notes(json.dumps({"action":"list"}), owner=...)` (`src/tool_implementations.py:1802`), as used by the check-in path (`task_scheduler.py:1162`). `Note` table = `core/database.py:1462`.

---

## 4. Guardrail tie-in (FREE-only, unattended) — **explicit dependency note**

The brief's requirement: unattended → MUST run on a free local model (`free_only=True`).

**Status: that flag/guardrail DOES NOT EXIST.** Grep across `src/ routes/ core/` for `free_only|free_models|require_free|only_free|is_free` returns **nothing**. The nearest existing signal is `ModelEndpoint.endpoint_kind` (`core/database.py:348`, values `auto|local|api|proxy`) — `local` = self-hosted = free. There is no cost/price column and no "free model" allowlist anywhere.

**Critical nuance that de-risks this:** `action_daily_brief` calls **NO LLM**. It's pure DB/IMAP formatting. It is **not** in `_MODEL_BACKED_ACTIONS` (`task_scheduler.py:966-976`), so `_task_needs_model_slot` returns `False` (`:978-993`) and it never touches `resolve_endpoint` at all. **If the brief uses the existing `daily_brief` action, `free_only` is moot — there is no model to constrain.** The unattended-cost risk is **zero** for that path.

The guardrail only becomes load-bearing if you choose the **LLM-summarized** variant (the `_execute_checkin` style, or a new LLM task). In that case the model is resolved via `resolve_endpoint("utility"/"research", owner=...)` (`endpoint_resolver.py:221`) → falls back to `utility_model` → `default_model` (`:260-272`). **None of these resolvers filter by free/local.** Forcing free-only would require new code: either (a) a `free_only` param threaded into `resolve_endpoint` that filters `ModelEndpoint` by `endpoint_kind == "local"`, or (b) pinning the task's `model`/`endpoint_url` to a known-local endpoint at creation. **Flag this as a hard prerequisite before any unattended LLM task ships.** (Matches HANDOFF non-negotiable: "unattended work forced to free local models.")

**Recommendation:** Phase-1 daily brief should use the **deterministic `daily_brief` action**, not an LLM. It satisfies "free by default" trivially and is already verified-shaped code.

---

## 5. THE WIRING PLAN (concrete)

**Approach A (recommended — minimal, free-by-construction):** cron task → `daily_brief` action → ntfy.

1. **Register the task.** Add a `HOUSEKEEPING_DEFAULTS` entry in `src/task_scheduler.py:205` (seeded path) OR `POST /api/tasks`:
   - `task_type="action"`, `action="daily_brief"`, `schedule="cron"`, `cron_expression="0 7 * * *"` (07:00; cron is UTC unless a crew timezone is linked — see `_resolve_task_timezone`, `:187-198`), `output_target="ntfy"` (new value — see Gap A), `owner=<user>`.
   - Seeding precedent: mirror the `summarize_emails` entry (`:210`), likely with `ship_paused: True` so it doesn't fire before the user opts in.

2. **Gather** — already done inside `action_daily_brief` (`builtin_actions.py:986`). No change.

3. **Summarize with a FREE model** — **skip.** The action is deterministic; no model needed. (If a polished prose summary is later wanted, add an LLM pass guarded by the free-only resolver from §4 — not Phase-1.)

4. **Push via ntfy** — close **Gap A** below.

### Gap A (the one real change needed): action output → ntfy

Today an `action`-type task's result reaches ntfy via **no path**:
- `_deliver_task_result` (`task_scheduler.py:1412-1437`) handles only `mcp__*`, `email*`, and `session` — there is **no `ntfy` / `notification` branch** (verified `:1421-1437`).
- The `output_target == "notification"` value (offered in the UI, `task_routes.py:963`) only feeds the **in-app browser** queue via `add_notification(body=...)` (`task_scheduler.py:828-835`) — **and that gate excludes actions**: `should_notify` requires `task_type in {"llm","research"}` (`:824-827`). So a `daily_brief` action fires **nothing**.

**Two clean options to close it (pick one):**
- **A1 (most consistent — recommended):** Have `action_daily_brief` itself call `dispatch_reminder(...)` at the end, exactly like `action_check_email_urgency` does (`builtin_actions.py:1903-1925`). It already builds `plain_body`; add `await dispatch_reminder(title="Daily brief — <date>", note_body=plain_body, note_id="daily-brief", owner=owner)` before `return`. This reuses the full channel router (respects `reminder_channel`), needs zero scheduler changes, and works under the no-session-cookie constraint. Confirmed today it does NOT do this (`:986-1112`, no `dispatch_reminder` call).
- **A2:** Add an `output_target == "ntfy"` branch to `_deliver_task_result` (`task_scheduler.py:1428`-ish) that calls `dispatch_reminder`. More general (any task), but touches the shared delivery method.

**Dependency:** ntfy must be configured as an enabled Integration (preset `"ntfy"`, base URL set) AND `reminder_channel="ntfy"` (or A1 can force the channel via `settings_override={"reminder_channel":"ntfy"}`, supported at `note_routes.py:144,160`). Otherwise `dispatch_reminder` falls back to browser/returns `ntfy_sent=False`.

---

## 6. What "done + verified" looks like

A **real ntfy notification actually fires** — not an exit code, an observed push (HANDOFF non-negotiable: "verify by running + reading real output"):

1. **Config:** ntfy Integration enabled with reachable `base_url` (`NTFY_BASE_URL`, default `http://localhost:8091`); `reminder_ntfy_topic` set; `reminder_channel="ntfy"` (or A1 override).
2. **Force-run** the task: `POST /api/tasks/{id}/run` → `run_task_now` (`task_scheduler.py:1920`), or set `cron_expression` to the next minute and let `_loop` pick it up.
3. **Verify the push landed:** subscribe to the topic — `curl -s "$NTFY_BASE_URL/<topic>/json?poll=1"` (the documented poll endpoint, `integrations.py:101`) and confirm a message with the brief body + `Title` header appears. A successful send means `dispatch_reminder` returned `ntfy_sent=True` (`note_routes.py:483,547`).
4. **Verify the run record:** the `TaskRun` row (`task_runs`) is `status="success"` with `result` = the brief text (`task_scheduler.py:715-717` for actions).
5. **Verify content correctness:** brief contains today's real calendar events, real unread count + senders, real todos — cross-check against the calendar UI / IMAP, since `action_daily_brief` uses the **default email account only** (`_imap_connect(None)`) — flag if the user has multiple accounts.
6. **Free-cost check (if LLM variant is ever used):** confirm the resolved model is local; today the resolver gives no such guarantee — see §4.

---

## Risks / uncertainties (flagged)

- **Gap A is real and verified** — without A1/A2, a `daily_brief` action task delivers nothing to ntfy (and nothing at all, since the browser-notify gate excludes actions). This is the one piece of new wiring.
- **`free_only` is vaporware** — does not exist; only relevant if you go LLM. The deterministic action sidesteps it entirely. Do not claim a free-only guardrail is "reused" — it must be built if/when an unattended LLM task ships.
- **Timezone:** cron `next_run` is computed in **UTC** unless the task is linked to a `CrewMember` with a `timezone` (`compute_next_run` + `_resolve_task_timezone`, `task_scheduler.py:67-198`). "07:00 local" needs either a crew-linked tz or a UTC-offset cron — easy to get wrong.
- **Multi-account email:** `action_daily_brief`'s `_imap_connect(None)` is single-(default-)account; multi-account users get an undercount. Not a blocker, but note it.
- **Owner scoping:** `action_daily_brief` gates the `owner=None` (shared) branch on single-user deploys only (`:1003-1007`, `_allow_null`) — correct multi-tenant behavior, no leak.
- **Base-branch question** (from HANDOFF `:15`) is orthogonal to this map; not resolved here.

**Key anchors:** `src/builtin_actions.py:986` (action_daily_brief), `:2217`/`:2239` (registry), `:1903-1925` (dispatch_reminder call pattern) · `routes/note_routes.py:138` / `:464-490` (ntfy send) · `src/task_scheduler.py:1412-1437` (delivery gap), `:824-835` (notify gate), `:1687-1694` (resolve_endpoint reference), `:205-216`/`:1947` (seeding) · `core/database.py:543` (ScheduledTask), `:333` (ModelEndpoint, endpoint_kind) · `routes/task_routes.py:453` (create_task) · `src/endpoint_resolver.py:221` (resolve_endpoint — no free filter) · `src/settings.py:144-146` (reminder defaults).