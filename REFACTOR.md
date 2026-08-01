# REFACTOR.md — Nest.APStudy refactor charter

Local-only planning document (gitignored). Branch: `codex/refactor-foundation`.

**This document is the contract.** The refactoring agent reads this file first, picks exactly
one theme from §8, executes only that theme, proves it with §5, commits, and stops. It does not
improvise beyond the selected theme.

---

## 1. How to use this document

1. Read §2 (baseline), §3 (non-goals), §4 (guardrails), §5 (verification), §6 (test hazards),
   §7 (false positives). All seven sections are mandatory reading before the first edit.
2. Pick **one** theme from §8. Announce which one. Do not start a second theme in the same session.
3. Work the theme's checklist in order. Run the §5 loop after each logical step, not at the end.
4. Commit per §9. Append the outcome to §10.
5. End the session. A fresh session starts the next theme with a clean context window.

Rule of thumb: if a change is not named in the selected theme, it does not belong in this commit.
Write it down in §10 as a follow-up instead.

---

## 2. Verified baseline

Every number below was measured on this branch, not estimated. Re-measure before claiming
improvement; do not trust these figures after the tree changes.

### Size

| Surface | Tracked LOC | Files | Notes |
|---|---|---|---|
| Python | 48,715 | 135 | Includes ~11,900 lines of tests |
| Browser JS (`.js/.mjs/.cjs`) | 55,070 | 267 | Excludes committed `dist/` bundles |
| CSS | 35,565 | 32 | Handwritten; Tailwind output is a separate 31 KB artifact |
| Jinja templates | 7,560 | 43 | No base template exists |

### Largest files (the refactor surface)

Backend: `blueprints/chat_api.py` 3129, `blueprints/admin.py` 2407, `blueprints/calendar_api.py`
2122, `blueprints/settings.py` 1903, `services/atlas_client.py` 1781, `blueprints/auth.py` 1606,
`blueprints/dashboard.py` 1489, `blueprints/notes_api.py` 1241, `blueprints/file_share.py` 1178,
`services/discord_audit.py` 1093, `services/focus_mode.py` 1040.

Frontend: `static/js/chat/runtime.js` 2994, `static/js/notes/editor.js` 2647,
`static/js/onboarding/index.js` 1128, `static/js/admin-analytics.js` 1015,
`static/js/focus/view.js` 952, `static/js/focus/index.js` 888, `static/js/settings/index.js` 831,
`static/js/notes/list.js` 820, `static/js/courses/index.js` 774, `static/js/core/global.js` 669.

### Test suite (measured on this branch)

| Suite | Command | Result | Runtime |
|---|---|---|---|
| JS | `npm run test:js` | 406 pass, 0 fail | ~1.2 s |
| Python | `.venv/bin/python -m unittest` | 565 pass, 0 fail | ~6.3 s |
| Combined | `npm test` | 971 pass | ~10 s |
| Browser | `npm run test:browser` | ~60 Playwright cases | needs a manually started server |

The suite is fast enough to run after every single edit. There is no excuse for batching changes
before verifying.

### Where the debt actually is

The repo has a code-quality scanner (`.desloppify/`) with per-language history. The asymmetry in
its records is the single most useful strategic fact available:

| Language | Scans | Objective score | Strict score | Open findings |
|---|---|---|---|---|
| JavaScript | 112 | 95.9 | 78.3 | ~111 |
| Python | **1** | 85.5 | **21.4** | **441** (308 in product code) |

The frontend has been through 112 cleanup cycles and shows it: `calendar/`, `courses/`, `files/`,
`settings/`, `dashboard/`, `tasks/`, and `notes/editor/` are all already decomposed into
per-concern directories. The backend has been scanned **once, ever**, and has never had a cleanup
pass. Its 24 flagged large files and 11 monster functions are all original.

**Therefore: this refactor is primarily a backend refactor.** The two frontend monoliths
(`chat/runtime.js`, `notes/editor.js`) are the exceptions worth attacking, and they are already
half-finished. Everything else on the frontend is maintenance, not restructuring.

Note when reading `.desloppify/plan.json`: its named clusters (`files-split`, `calendar-split`,
`courses-split`, `settings-split`) are **stale**. They reference `static/js/chat.js`,
`static/js/calendar.js`, `static/js/files.js` etc. — files that no longer exist because the splits
already happened. It also lists `notes/editor.js` at 1336 LOC; it is now 2647. `config.json` has
`needs_rescan: true`. Treat the cluster list as history, not as a work queue.

### Architecture in one paragraph

Flask monolith, application factory in `app.py:create_app()` (L32–319), 21 blueprints registered in
`blueprints/__init__.py:30–51`. **Persistence is SQLite, not Appwrite** — `appwrite_helpers.*_safe`
(L157–218) delegates to `services/database.py`, which reimplements Appwrite's query API over
SQLite. `models.py` is a Flask-Login user DTO, not an ORM; there is no SQLAlchemy. Real Appwrite is
used for exactly three things: OAuth/session identity (`blueprints/auth.py`), file storage
(avatars, file share, notes media, chat attachments), and admin user deletion. Blueprints are fat
controllers mixing routing, validation, permission checks, serialization, and data access. Store
layers exist for notes and calendar (`services/note_store.py`, `services/calendar_store.py`) but
chat, tasks, files, settings, and admin write CRUD inline.

---

## 3. Non-goals — explicitly off limits

Do not do any of the following, even if it looks like an obvious improvement. Each one is either
load-bearing, deliberately chosen, or out of scope. If you believe one of these is wrong, write the
argument in §10 and stop; do not act on it.

### Architecture and dependencies

- **Do not replace the data layer.** `services/database.py` + `appwrite_helpers.*_safe` is a
  deliberate SQLite-behind-an-Appwrite-shaped-API design. Do not introduce SQLAlchemy, an ORM, a
  query builder, Alembic, or a repository abstraction over it. Do not "finish" the unused
  `AppwriteRepository` class, and do not delete it either — migration scripts reference it.
- **Do not migrate persistence back to remote Appwrite.** The `*_safe` functions calling SQLite is
  correct and intentional, not a bug or an incomplete migration.
- **Do not add a web framework, build tool, bundler, or runtime dependency.** No FastAPI, no Django
  patterns, no Pydantic, no Flask-RESTX, no webpack, no TypeScript migration, no npm packages.
  Tailwind + Vite + `node --test` + `unittest` is the toolchain.
- **Do not convert browser JS to a framework.** React exists only inside the two Vite bundles
  (notes editor, tasks). Classic-script pages stay classic scripts. Do not introduce a client-side
  router, a state management library, or a component framework on the other pages.
- **Do not switch test frameworks.** `unittest` and `node --test` stay. pytest is installed but
  unused by `npm test`; leave it that way. Do not rewrite tests into pytest style.

### Behavior

- **No behavior changes, no API contract changes.** Route paths, HTTP methods, status codes, JSON
  key names, response shapes, cookie names, and header names all stay byte-identical. This refactor
  is provably behavior-preserving or it is wrong.
- **Do not change the database schema.** No new `migrations/*.sql`. No column renames. No index
  changes.
- **Do not "simplify" security controls.** The CSRF design is deliberately asymmetric: global
  default off (`WTF_CSRF_CHECK_DEFAULT = False`, `app.py:68`), enforced for authenticated mutations
  in `protect_authenticated_mutations` (`app.py:100–108`), with `admin` exempted there because
  `admin.py:335–338` runs its own stricter always-on check. That is not redundancy to collapse.
  Likewise leave the CSP constants in `auth.py:63–100` and the SSRF guards alone.
- **Do not touch the entitlements/tier logic** (`services/entitlements.py` and every
  `exc.payload(), 403` site). It is billing-adjacent.
- **Do not delete features under the banner of simplification.** Specifically protected: Focus
  Mode, the Echo dashboard (`/derek/echo`), Discord audit and bridge, the invite/referral system,
  the Atlas course scraper, notes collaboration, and the split-flap clock. Low test coverage is not
  evidence that something is unused.

### Files and directories

- **Do not refactor gitignored local-only trees.** `temporary_rsvp/`, `codex-attention-notifier/`,
  `.agents/`, `docs/`, `.desloppify/`, `Fall_2026/`, `Spring_2026/`, `data/`. The scanner reports
  findings in these (22 + 18 + 24 respectively); all are out of scope. Note that 24 of the 47
  flagged "large JS files" are in `.agents/skills/impeccable/scripts/` — ignore every one.
- **Do not regenerate or hand-edit committed bundles.** `static/js/notes/dist/` and
  `static/js/tasks/dist/` are committed artifacts. Only `npm run build` may change them, and only
  when a theme explicitly says so.
- **Do not reformat files you are not otherwise changing.** No repo-wide formatter run. No import
  reordering as a drive-by. A diff full of whitespace is unreviewable and will be rejected.
- **Do not add a linter or type checker in the same commit as a refactor.** `eslint.config.cjs` has
  `rules: {}` and is not wired to any npm script; there is no ruff/black/mypy. Introducing one is a
  separate, deliberate change (see §8, Theme H) — not a prerequisite.

### Process

- **Do not push.** Commit locally only. The developer pushes.
- **Do not touch `main`.** All work stays on `codex/refactor-foundation`.
- **Do not install the GitHub CLI or use `gh`.** Plain `git` only, per `COMMIT_PUSH.md`.
- **Do not commit secrets.** `.env`, `client_secret.json`, `secrets/` are gitignored; keep it so.
- **Do not edit `AGENTS.md`, `COMMIT_PUSH.md`, or `VPS_CONTEXT.md`** to make a rule inconvenient to
  you go away.

---

## 4. Guardrails

**Sandbox / approvals.** Workspace-write is appropriate: the agent needs to edit files in this repo
and run the test suite. Keep terminal execution on explicit approval. Network access is not needed
and should stay off — every test mocks Appwrite and HTTP, so a run that wants the network is a
signal something is wrong.

**Never run without asking, regardless of policy:** anything touching `instance/` (live SQLite),
`git push`, `git reset --hard`, `git checkout main`, `npm install`, `pip install`, the deploy
scripts in `deploy/`, `scripts/backup_nest_db.py`, or the `systemctl`/`git pull` paths in
`admin.py:1406–1467`. This repo deploys to a real VPS serving real users.

**Scope ceiling per commit.** If a single logical step touches more than ~8 files or ~400 net lines,
it is too big — split it. If a theme's diff exceeds ~1,500 net lines, stop and reassess rather than
pressing on.

**Stop conditions.** Halt and report rather than improvising if: a test fails and the cause is not
obvious within two attempts; a change requires editing a test's *assertions* (see §6); the same test
fails twice after two different fixes; or the work is pulling you outside the selected theme.

---

## 5. The verification loop

Run after every logical step. Both suites, every time — they take ten seconds combined.

```bash
npm run test:js                        # 406 expected, ~1.2s
.venv/bin/python -m unittest           # 565 expected, ~6.3s
```

Before handing off, additionally:

```bash
npm test                               # both of the above
npm run build                          # css + notes bundle + tasks bundle
git diff --stat                        # review the shape of the change
```

### Gotchas that will waste your time

- **`unittest` prints its summary to stderr**, and several tests emit `[ERROR]`/`[WARN]` log lines
  on stdout as part of normal passing behavior (the backup-script tests are the loudest). Log noise
  is not failure. Confirm the real result with:
  `.venv/bin/python -m unittest 2>&1 | rg 'Ran [0-9]+ tests|^OK|^FAILED'`
- **`test:py` hardcodes `.venv/bin/python`.** If the venv is missing, it fails instantly with a
  path error that has nothing to do with your change.
- **Discovery pattern is `test*.py` under `tests/`.** A new test file named anything else will be
  silently skipped and you will believe you have coverage you do not have.
- **`npm run build` rewrites committed bundles** in `static/js/notes/dist/` and
  `static/js/tasks/dist/`. Expect dist churn in `git status` after a build; do not commit it unless
  the theme intends to.
- **Playwright is not in `npm test`** and `playwright.config.mjs` has no `webServer`. It needs Flask
  already running on `127.0.0.1:8000`. Without a server every spec fails on connection refused — it
  does not skip. Do not add it to the inner loop; run it manually when a theme touches UI behavior.
- **Two Atlas tests self-skip** when the `Fall_2026/`/`Spring_2026/` JSON corpus is incomplete.
  Skips there are normal.
- **`npm test` needs no network, no Appwrite credentials, and no built CSS.** If a test starts
  wanting any of those, you have broken an isolation boundary.

### Coverage honesty

Where the suite genuinely protects behavior: chat, admin, auth/OAuth, dashboard, notes sharing and
media, file share, calendar feed ingestion, Discord audit, notifications, focus-mode *service*,
global search, tasks helpers, security boundaries.

Where it does not — refactor these only with hand-written tests added first: `services/giphy.py`,
`services/ics_builder.py` (280 LOC, zero direct tests), `services/course_catalog.py`,
`services/file_cleanup.py`, `services/task_schedule.py` (211 LOC), `services/notes_collaboration.py`
(849 LOC, zero direct tests), `blueprints/atlas_api.py`, `blueprints/calendar_sources_api.py`,
`blueprints/focus.py` routes (the service is tested, the routes are not), and
`collaboration/server.mjs` (a separate Node process with no tests at all).

---

## 6. Test-coupling hazards — read before moving any code

This repo's tests are coupled to code *structure*, not just behavior. Two mechanisms will bite, and
both can fail **silently** — passing tests that no longer test anything. This is the highest-risk
property of the entire refactor.

### Hazard 1 — Python tests patch symbols on the blueprint module object

There are 1,025 `patch`/`patch.object` calls across `tests/*.py`. The dominant form targets the
blueprint module itself:

| Patch target | Occurrences |
|---|---|
| `patch.object(admin, ...)` | 174 |
| `patch.object(chat_api, ...)` | 145 |
| `patch.object(auth, ...)` | 92 |
| `patch.object(course_tracking, ...)` | 81 |
| `patch.object(dashboard_bp, ...)` | 45 |
| `patch.object(courses, ...)` | 35 |
| `patch.object(settings_bp, ...)` | 26 |
| `patch.object(notes_api, ...)` | 25 |

Consequence: **a name must remain bound in the blueprint module's namespace, and the route must
call it unqualified.**

Safe — the patch still intercepts, because the blueprint namespace still holds the name and the call
site resolves through it:

```python
# blueprints/admin.py
from services.admin_user_sections import load_courses_section
...
rows = load_courses_section(user_id)          # patch.object(admin, "load_courses_section") works
```

Unsafe — the patch silently no-ops. It rebinds an attribute nobody reads, the real implementation
runs, and the test may still pass while asserting nothing:

```python
# blueprints/admin.py
from services import admin_user_sections
...
rows = admin_user_sections.load_courses_section(user_id)   # patch on `admin` never fires
```

Rules:
- Extract with `from services.x import name`, then call `name(...)` unqualified.
- Never convert an existing unqualified call into a module-qualified one.
- After extracting anything a test patches, confirm the test still *exercises* the patch — make it
  fail on purpose once (raise inside the extracted function, or assert a wrong value) and watch it
  go red. A test that cannot fail is not protecting you.

### Hazard 2 — 11 JS test files assert on source text, not behavior

`tests/js/` contains source-contract tests that `readFileSync` a module and regex its contents.
`browser-modules.test.mjs` alone is 1,338 lines of this, covering most of `static/js/`. Others
include `focus-mode.test.mjs` (19 reads), `ui-primitives-contract.test.mjs`,
`notes-editor-build.test.mjs`, `task-asset-contract.test.mjs`.

These fail in both directions:
- **False failure** on a harmless rename, file move, or reformat — nothing broke, the string moved.
- **False pass** on genuine semantic breakage — the string is still present, the logic is wrong.

So: when a JS contract test fails after a move, the correct fix is usually to update the test's
*path or pattern* to follow the code. That is legitimate. Rewriting the *assertion* so it no longer
checks the property is not — that is deleting a test while appearing to fix it.

Additional structural constraints worth knowing before touching the frontend:

- `tests/test_template_asset_order.py` and `tests/js/theme-diagnostics-loading.test.mjs` encode the
  stylesheet and script load order (`theme-init.js` → `themes.css` → `global.css` → `layout.css` →
  `index.css` → `tailwind.css` → feature CSS). Reordering `<head>` breaks these by design.
- `z-index-contract.test.mjs` forbids raw four- and five-digit z-index literals in first-party
  sources; use the `--z-*` tokens in `global.css`.
- `material-icon-contract.test.mjs` enforces the `AGENTS.md` rule that every Material Symbols
  ligature must exist in `static/fonts/material-symbols-outlined-v361-icons.txt`.
- `layout-motion-contract.test.mjs`, `mobile-viewport-contract.test.mjs`, and
  `icon-font-contract.test.mjs` all reference specific CSS file paths; splitting or renaming a CSS
  file requires updating them.

---

## 7. Known false positives — do not "fix" these

The scanner's findings include noise that looks alarming and is not. Acting on these would make the
codebase worse.

**28 × Bandit B608 "possible SQL injection via string-based query construction."** All false
positives. Verified in `services/database.py`: every value is bound through `?` placeholders
(`_parse_queries` L351–414, `list_rows` L417–447); table names are validated against `IDENTIFIER_RE`
and `sqlite_master` before interpolation (`table_columns` L298–304); column names are validated
against `PRAGMA table_info` (`_validate_column` L307–312). The f-strings interpolate only
schema-validated identifiers, which is the correct way to write this in SQLite. The same pattern
holds in `note_store.py`, `calendar_store.py`, `notifications.py`, `notes_collaboration.py`, and
`focus_mode.py`. **Do not rewrite these queries and do not add an ORM to "fix" them.**

**2 × "hardcoded secret" on `fetch` credentials options** — flagged as `strategy` issues by the
scanner itself. Same-origin credentials strings, not secrets.

**1 × "hardcoded secret in variable `AUTH_ERROR_OAUTH_CREDENTIALS`"** — an error-message constant.

**7 × B603/B404 subprocess warnings in `blueprints/admin.py` and `services/apswiftly_control.py`** —
deliberate VPS administration (`systemctl`, `git pull`) behind `admin_required`. The one legitimate
finding in this group is *"subprocess call without timeout (can hang forever)"* in `admin.py`; adding
a timeout is a real fix and is listed in §8 Theme A.

**`services/database.py` importing `note_store`** (for `backfill_preview_texts` during `init_db`)
reads as a circular import and is a deliberate, working migration hook. Leave it.

**Low coverage ≠ dead code.** See §3. `services/notes_collaboration.py` has 849 lines and zero
direct tests; it is live and load-bearing.

---

## 8. The plan

Three tiers by risk and blast radius. Each theme is one session and one or more commits. Themes
within a tier are independent unless a dependency is stated. **Do not start Tier 2 before Tier 1 is
committed and green** — Tier 1 establishes the shared helpers that Tier 2 extractions depend on.

### Tier 1 — Quick wins

Low risk, mechanical, individually verifiable. These build confidence in the loop and remove noise
that would otherwise obscure the bigger diffs.

**Theme A — Correctness and hygiene sweep.**
Real defects the scanner found, none of them structural.
- Add a `timeout=` to the untimed `subprocess` call in `blueprints/admin.py` (can currently hang the
  worker forever).
- Fix the ~56 `dict_keys` findings: keys written twice with no read between, and keys read but never
  written. Concentrated in `admin.py` system-status (`cpu_percent`, `cpu_logical`, `cpu_physical`,
  `mem_percent`, `mem_used_gb`, `mem_total_gb`, `storage_percent`, `storage_used_gb` all overwritten
  around L189–229), `settings.py:1008–1012` (`avatar_file_id`), `calendar_api.py:626–628`
  (`reminder_minutes`), and a `"jobs"` key read at `admin.py:1513` that is never written. Each is a
  two-line fix; each is a real latent bug.
- Fix the `"$id"` vs `"id"` key-typo findings on `_row_to_dict`.
- Delete four provably dead private functions, each zero-reference: `_extract_course_name()`
  (`services/feed_fetcher.py`), `_ics_escape()` (`services/ics_builder.py`), `_ordered_tiles()`
  (`blueprints/dashboard.py`), `_delete_file_share_storage_file()` (`blueprints/settings.py`).
  Grep to confirm zero references before each deletion.
- Fix the ~11 catch-blocks that only log and the ~9 `except: pass` handlers that swallow errors
  silently. Preserve control flow exactly — add context to the log or narrow the exception type; do
  not change what is caught or start re-raising.

*Acceptance:* `npm test` green at 971. Every deletion justified by a grep showing zero references.

**Theme B — Backend helper consolidation.**
The single highest-leverage low-risk change, and a prerequisite for Tier 2.
- `_row_id(row)` is copy-pasted in seven blueprints: `admin.py:111`, `chat_api.py:133`,
  `calendar_api.py:926`, `dashboard.py:191`, `file_share.py:78`, `notes_api.py`, `tasks_api.py:44`.
  Consolidate into one helper.
- Five duplicate now-timestamp helpers: `file_share._utcnow` (L60), `tasks_api._utcnow/_utcnow_iso`
  (L48–52), `notes_api._utcnow_iso` (L51), `chat_api._now` (L129), `database.utcnow_iso` (L53).
- `blueprints/chat_api.py:635–662` duplicates four functions that already exist in
  `services/user_profile.py:8–37` (`normalize_banner_color`, `profile_handle`, `is_emory_school`,
  `is_early_member`). Delete the copies, import the originals.
- 13 constants defined identically in multiple modules.

**Import per Hazard 1**: `from services.row_utils import row_id` and call `row_id(...)` unqualified.
Do not module-qualify.

*Acceptance:* `npm test` green. `rg 'def _row_id'` returns one definition. No route behavior touched.

**Theme C — Frontend `escapeHtml` unification.**
18 independent `escapeHtml` implementations exist: `chat/presentation.js:4`, `chat/attachments.js:14`,
`chat/media-picker.js:24`, `calendar/utils.js:331`, `calendar/events/event-form.js:11`,
`courses/utils.js:156`, `dashboard/utils.js:93`, `files/utils.js:403`, `notes/list/utils.js:2`,
`notes/sharing.js:12`, `notes/editor/review-panel.js:1`, `notes/editor/collaboration.js:21`,
`settings/utils.js:300`, `admin-analytics.js:165`, `landing.js:250`, `core/notification-tray.js:11`,
`derek/echo-utils.js:3`, and the canonical DOM-based one in `core/ui-primitives.js:14–17` — which is
not currently exported.

This is an XSS-relevant surface: eighteen hand-rolled escapers are eighteen chances for one to be
subtly wrong. Export the `ui-primitives.js` implementation, adopt it everywhere the module system
allows, and **diff the escaping behavior of each replaced implementation before removing it** — if
any one escapes a different character set, that difference is either a bug being fixed or a
requirement being broken, and you must determine which.

Constraint: the module systems are mixed (60 ES modules, 81 classic scripts). Classic scripts cannot
`import`; they must reach it via the `window` global that `ui-primitives.js` already establishes, and
`ui-primitives.js` must keep loading before `global.js` per `AGENTS.md`. Do not convert a classic
script to a module as part of this theme.

*Acceptance:* `npm test` green. One implementation. Contract tests updated to follow paths, never
weakened.

**Theme D — Frontend HTTP consolidation.**
~76 raw `fetch(` call sites across 41 files. CSRF is already handled globally and correctly by the
`window.fetch` patch in `core/global.js:8–72` — **do not touch that patch.** The inconsistency is in
error handling: `APStudyHttp.fetchJson` (`global.js:422–448`) is the canonical wrapper but only 9
files use it, while `notes/list/utils.js:71`, `files/utils.js`, `notes/sharing.js:46`,
`dashboard/utils.js:104`, `settings/utils.js:209`, `tasks/task-utils.js:93` each reimplement it, and
`onboarding/index.js:138–148` uses bare `fetch` with no wrapper and no pending-mutation tracking.

Migrate the duplicate wrappers to `APStudyHttp`. Start with `onboarding/index.js` — it is the only
one missing `APStudyPendingMutations` tracking, so it is the only one with a user-visible bug.

*Acceptance:* `npm test` green. Error-shape behavior unchanged at every migrated call site.

### Tier 2 — Medium architectural shifts

Real restructuring. One theme per session, each with its own commit. Hazard 1 applies to every
backend extraction; re-read §6 before starting.

**Theme E — Thin the fat blueprints.** The core of this refactor. Take **one blueprint per session**,
in this order (ascending risk):

1. **`blueprints/settings.py` (1903).** `save_onboarding` (L670–958, **289 lines**) is a step
   machine — each step validates, calls `update_row_safe`, and fires side effects such as
   `create_university_channel` at L918. Extract per-step handlers to `services/onboarding.py`. This
   also removes the `settings` → `chat_api` lazy import. Next: `update_feed_url` (L1294–1424, 131),
   `update_profile` (L1068–1185, 118), `upload_avatar` (L1190–1290, 101), `add_course` (L1778–1875,
   98), `update_interface_preferences` (L1485–1579, 95).
2. **`blueprints/dashboard.py` (1489).** `update_dashboard_layout` (L1045–1213, 169) and
   `_load_calendar_summary` (L519–644, 126). Move the L75–960 tile loaders to
   `services/dashboard_summary.py`; leave routes as delegators. Well covered by
   `tests/test_dashboard.py` (26 cases) — a good early confidence-builder.
3. **`blueprints/calendar_api.py` (2122).** L70–925 is already almost entirely private helpers —
   serialization, span metadata, colors, reminders, feed sources, share links. Move to
   `services/calendar_events.py`. `get_events` (L1282–1401, 120) is the biggest function. Side
   benefit: relocating `_load_serialized_calendar_events` fixes the `services/global_search.py:246`
   → blueprint import inversion and one of the seven cross-module private imports.
4. **`blueprints/file_share.py` (1178).** `upload_file` (L604–768, **165**) and the L60–563 folder
   helpers → `services/file_share_store.py`, mirroring the existing `note_store.py` pattern.
5. **`blueprints/admin.py` (2407).** `_load_section` (L759–929, **171 lines**) is a switch over
   `ALLOWED_SECTIONS` loading every domain — the switch structure makes it mechanically splittable
   into per-domain loaders. Also extract the VPS operations (L82–96, L1406–1467) to
   `services/host_admin.py`. **Highest patch-coupling in the repo at 174 `patch.object(admin, ...)`
   calls** — do this one last, after the pattern is proven four times over.

Deliberately excluded from this theme: `blueprints/auth.py`. Its `_complete_appwrite_login`
(L874–1101, **228 lines**) is the largest function in the backend, but it is the live OAuth path
guarded by 92 patches, and a mistake there locks every user out. Move it to Tier 3 and only after
the pattern is boring.

*Acceptance per blueprint:* `npm test` green at 971. Every extracted symbol still resolvable and
patchable from the blueprint namespace (§6, Hazard 1) — verified by deliberately breaking one patch
per extraction and confirming red. Route table unchanged: diff `app.url_map` before and after.

**Theme F — Split `static/js/chat/runtime.js` (2994).**
Critical structural fact: the file has **exactly one export**, `startChatRuntime` (L4), and all
~100 functions are nested inside that single closure sharing `state` (L20–59) and `els` (L110+) by
lexical scope. You cannot lift a function out without explicitly threading its dependencies. Plan
the parameter/context object *first*; do not start cutting.

Already extracted and working: `presentation.js`, `cache.js`, `attachments.js`, `media-picker.js`,
`message-media.js`. Extract next, loosest-coupled first:
1. `chat/realtime.js` — SSE, `handleRealtimePayload` (L2274), fallback polling (L1248–1333)
2. `chat/presence.js` — presence maps, typing, `renderPresenceDrivenUi` (L1826)
3. `chat/messages-dom.js` — the `render*` family and `syncMessagesToDom` (L1156)
4. `chat/rooms.js` — channel/DM lists, `selectRoom` (L2076), unread state
5. `chat/composer.js` — `sendActiveMessage` (L2502), retry, attachment integration

`bindEvents` (L2790–2963, 174 lines) should be split alongside whichever concern each handler
belongs to, not extracted as a unit. The existing `fetchJson` bridge at L366–383 is the natural
anchor for a `chat/api.js`.

*Acceptance:* `npm test` green; `tests/js/chat-ui-realtime.test.mjs` (529 lines) and
`frontend-module-boundaries.test.mjs` updated for new paths only. Manually exercise send, receive,
presence, and unread in a browser — the source-contract tests cannot prove this works (§6, Hazard 2).

**Theme G — Finish `static/js/notes/editor.js` (2647).**
The pattern is already proven: `static/js/notes/editor/` holds 2,140 lines across 13 modules
(`print.js`, `markdown-repair.js`, `block-catalog.js`, `image-runtime.js`, `review-panel.js`,
`images.js`, `collaboration.js`, `utils.js`, keyboard shortcuts, `block-operations.js`,
`heading-collapse.js`). Collaboration, print, images, and review are cleanly modular already. What
remains in the entry file is imperative toolbar DOM plus React mount glue:
- `editor/toolbar-dom.js` — `bindWritingToolbar` (L1835–2065, **231 lines**), `updateToolbarState`
  (L1607–1693, 87), menu positioning
- `editor/react-shell.js` — `NoteEditor` (L2317–2430, 114), `NotesSideMenu` (L2179–2315, 137),
  `NotesSlashMenu`, `initEditorPage` (L2432–2573, 142)
- `editor/save.js` — `saveNote` (L1053), debounce (L2086), fingerprinting (L393–417),
  `APStudyPendingMutations` integration
- `editor/page-setup.js` — zoom/margins popover (L202–640)
- `editor/paste.js` — `handleNotesPaste` (L2104–2148)

This one changes a **Vite bundle**, so `npm run build:notes` must run and
`tests/js/notes-editor-build.test.mjs` plus `tests/browser/notes-editor-payload.spec.mjs` must pass.
Committed `dist/` churn is expected here and is correct.

*Acceptance:* `npm test` green, `npm run build` clean, Playwright notes specs pass against a local
server. Verify collaborative editing and print by hand.

**Theme H — Wire up static analysis.** Standalone; do not bundle with a refactor commit.
`eslint.config.cjs` has `rules: {}` and no npm script. Add a minimal rule set (`no-unused-vars`,
`no-undef`, `no-implicit-globals`) plus a `lint` script, and consider ruff for Python. Expect a large
first-run backlog: land the config with a baseline, then fix in separate commits. This theme is
worth doing early *if* you want a machine checking the later themes — but it is not a prerequisite.

### Tier 3 — Long-term

Larger than a single session. Plan explicitly before touching; each needs its own charter.

**Theme I — Centralize configuration.** ~112 direct `os.environ` reads across 23 backend files
(`app.py` 15, `chat_api.py` 13, `scheduler.py` 13, `discord_audit.py` 9, `appwrite_client.py` 8,
`database.py` 6). Note `services/app_config.py` is *database-backed feature flags*, not env config —
do not conflate them. Introduce a config dataclass populated once in `create_app`, and land it first
as a **read-only facade** that existing call sites can migrate to incrementally. Do not attempt a
big-bang cutover; env handling touches deployment and getting it wrong takes production down.

**Theme J — Break the blueprint↔blueprint cycles.** `admin` → `settings`, `chat_api`;
`calendar_api` ↔ `settings`, `tasks_api`; `dashboard` → `settings`, `calendar_api`, `tasks_api`;
`focus`/`derek` → `dashboard`. Plus lazy function-level blueprint imports inside services
(`scheduler.py:473`, `discord_gateway.py:108–138`, `global_search.py:246`) that exist purely to dodge
circularity. Largely falls out of Tier 2 Theme E if extraction is done consistently — reassess scope
after Theme E lands rather than planning it now.

**Theme K — Extract `blueprints/chat_api.py` (3129) domain logic.** The largest file in the repo.
Natural seams: L129–717 presence helpers, L731–1599 Discord ingest/sync (`_upsert_discord_message`,
`ingest_discord_gateway_message`, `_reconcile_discord_deletes`), L934–1255 markdown rendering,
L1600–2180 summary/bootstrap, L2181+ routes. Biggest functions: `send_channel_message`
(L2625–2783, 159), `dm_thread_messages` (L2916–3019, 104). Extracting the Discord block also removes
`scheduler.py` and `discord_gateway.py`'s dependency on a blueprint. Guarded by 145
`patch.object(chat_api, ...)` calls and module-level SSE listener state at L79–80 — respect both.

**Theme L — `auth.py` login path.** `_complete_appwrite_login` (L874–1101, 228) →
`services/auth_session.py`; `_fetch_provider_identity` (L499–591, 93) →
`services/oauth_providers.py`. Deferred from Tier 2 deliberately: this is the live OAuth path, 92
patches deep, and the blast radius of an error is every user locked out.

**Theme M — CSS and template consolidation.** Genuinely large: 35,565 lines of CSS with ~347 custom
properties and ~325 distinct hex colors, and **zero `{% extends %}` in 43 templates** — all 27 full
pages duplicate their own `<head>`. Sequence, smallest risk first:
1. Introduce `base.html` with blocks and migrate page-by-page. The load-order contract in
   `tests/test_template_asset_order.py` already encodes the required cascade, which makes this
   verifiable rather than guesswork.
2. Delete `static/css/sidebar.css` — not linked by any template or `@import`; the live sidebar styles
   are in `layout.css` (~L940–1480). Verify by grep before deleting.
3. Remove the inert `@tailwind base/components/utilities` directives at `global.css:1–3`. Only
   `tailwind-input.css` is compiled, so those three lines are invalid at-rules the browser ignores.
4. Fix wrong token fallbacks in `task.css` — `var(--color-primary, #3b82f6)` falls back to Tailwind
   blue instead of the APStudy `#0060aa`.
5. Extract a table macro from `admin_detail.html` (1093 lines, ~15 near-identical
   `admin-card`/`admin-table` sections).
6. Split `global.css` (2929 lines: base resets, calendar dialogs, command palette, tier badges,
   search field, skeletons all in one file). Last, because it touches every page.

Important scoping fact: Tailwind is linked on 25 of 27 full pages (missing on `focus.html` and
`derek_echo.html`) but only ~332 of ~2,571 `class` attributes use utilities — roughly **13%**.
Tailwind is real but narrow. Do not attempt to delete feature CSS in favor of utilities; the
coverage is not there and pages will break.

**Theme N — Cover the untested modules.** Add tests for the §5 list before anyone refactors them.
`services/notes_collaboration.py` (849 LOC) and `services/ics_builder.py` (280 LOC) are the two
largest completely unprotected modules. `collaboration/server.mjs` has no tests at all and runs as a
separate Node process. This theme *enables* other refactors and can run in parallel with them.

---

## 9. Commit protocol

Follow `COMMIT_PUSH.md`. Summary of what binds here:

- One logical change per commit; split unrelated work even within a theme.
- Stage explicit paths. No `git add -A`.
- Subject line: imperative, sentence case, describes the outcome — not file names. Body: one or two
  sentences on what and why.
- HEREDOC for the message.
- **Commit only. Do not push.** Report hashes and branch; the developer pushes.

```bash
git add blueprints/settings.py services/onboarding.py tests/test_settings_preferences.py
git commit -m "$(cat <<'EOF'
Extract onboarding step handlers into a dedicated service.

Move the 289-line save_onboarding step machine into services/onboarding.py so each
step's validation and side effects are independently testable, and drop the lazy
settings-to-chat_api import. No route or payload changes.
EOF
)"
```

Commit at every green point, not once per theme. A theme that produces five reviewable commits is
better than one that produces a single 1,500-line commit.

---

## 10. Progress log

Append one row per session. Keep it honest — a theme abandoned halfway is more useful recorded than
hidden.

| Date | Theme | Commits | Tests | Notes / follow-ups |
|---|---|---|---|---|
| 2026-07-30 | — | — | 406 JS / 565 PY green | Branch created, charter written. Baseline captured in §2. |
| 2026-07-30 | A | 81747af | 406 JS / 565 PY green; build green | Added restart timeout handling, corrected dict-key and row-ID hygiene, removed four zero-reference helpers, and made swallowed failures diagnosable. |
| 2026-07-30 | B | c96d14e | 406 JS / 565 PY green; build green | Consolidated row IDs, UTC timestamps, chat profile helpers, and 13 identical constants while preserving direct patchable module bindings. |
| 2026-07-30 | C | 378bbad, fdf3985, 735c8a2, 6d2940b, 46f5682, 3b78b47, 5785b9f, 0188d92, f800dbd | 406 JS / 565 PY green; 4 browser; build green | Unified the listed frontend escapers plus the undocumented chat message-media copy behind one strengthened DOM-based primitive and preserved classic-script load order. |
| 2026-07-30 | D | abea655, 692ee27, dc36296 | 406 JS / 565 PY green; build green | Consolidated the listed frontend JSON wrappers on APStudyHttp, added onboarding pending-mutation tracking, and preserved onboarding and notes-sharing error contracts. |
| 2026-07-30 | E (dashboard) | 6b39ee7, 4ba9077, 1a59ee0 | 406 JS / 565 PY green; build green | Moved dashboard tile loaders, calendar summary aggregation, and layout persistence into `services/dashboard_summary.py`; preserved blueprint patch bindings and the 251-route map. |
| 2026-07-31 | F | 82b2878, 1f3131d, 36196be, ff9bbc9, d8a5ea7 | 406 JS / 567 PY green; build green; browser unavailable | Split chat realtime, presence, message DOM, rooms/unread, and composer/delivery concerns behind one shared runtime context. Interactive send/receive/presence/unread verification remains pending because this session exposed no browser backend. |
| 2026-07-31 | N (notes_collaboration) | 8cbbc36, fc4b3c5 | 406 JS / 583 PY green; npm test green; build green | Added 16 isolated unittest cases for invitations, notifications, suggestions, comments, binary documents, versions, ownership transfers, access events, validation, and cleanup. Phase 2 (`ics_builder.py`) and phase 3 (`collaboration/server.mjs`) remain deferred; this worktree has no local `.venv`, so Python verification used the linked existing virtualenv. |
| 2026-07-31 | N (ics_builder) | — | Targeted tests not added; verification halted before the suite gates | Phase 2 stopped after inspection exposed an existing UID collision: `_build_event_uid(None, user_id)` uses `id(None)` instead of an event identifier, so missing-UID events for one user are not globally unique. No production behavior was changed; Phase 3 remains deferred. |
| 2026-07-31 | N (ics_builder) | f4ab665 | 406 JS / 595 PY green; npm test green; build green | Resumed Phase 2 after explicit authorization to fix the recorded UID collision. Added 12 isolated unittest cases for UID fallback, iCalendar escaping/defaults, validation, Appwrite failures, and Atlas recurrence/file failure paths; now uses cached `$id`/`id` and rejects missing identifiers. Phase 3 remains deferred; no push. |
| 2026-07-31 | N (collaboration/server.mjs) | — | Targeted node:test halted after 2 passing cases and a reproducible failure | Phase 3 stopped after the read-only integration test exposed that `beforeHandleMessage` rejects the initial Yjs sync whenever `connection.readOnly` is true, so viewer connections cannot complete their first sync. No production behavior was changed and no commit was created; the remaining Phase 3 coverage is deferred. |
| 2026-07-31 | N (collaboration/server.mjs) | 415dcb2 | 412 JS / 595 PY green; npm test green; build green | Resumed Phase 3 after explicit authorization to fix the recorded defect. Added 6 isolated node:test process-boundary cases for health, auth/origins, Yjs load/store, read-only synchronization, size limits, and internal-secret serialization; removed the redundant pre-hook rejection so Hocuspocus can sync viewers while still rejecting their mutations. Theme N complete; no push. |
| 2026-07-31 | I (phase 1) | 081e84e | 412 JS / 600 PY green; npm test green; build green | Added the frozen, read-only environment configuration facade and populated it once in `create_app()`; migrated only app-factory bootstrap reads, preserving defaults and production-secret errors. Inventory captured 107 direct product reads; import-time and request-time reads, backup scripts, and infrastructure remain deferred to later batches; no push. |
| 2026-07-31 | J (focus/Derek) | 7ab98b8 | 412 JS / 600 PY green; build green; exact `npm test` blocked by missing local `.venv` | Broke `focus`/`derek` → `dashboard` by moving shared authenticated page context into `services/dashboard_context.py` and retaining blueprint-local patchable adapters. Remaining admin/settings/calendar/task/dashboard edges and lazy service imports are deferred; no push. |
| 2026-07-31 | I (phase 2) | b0c84ce | 412 JS / 604 PY green; npm test green; build green | Routed the eight Appwrite import-time environment values through the frozen facade and preserved import-time client setup, missing-credential behavior, bucket defaults, and explicit empty values. Request-time endpoint/project reads, storage capability checks, and later migration batches remain deferred; no push. |
| 2026-07-31 | J (dashboard calendar share) | 1da8231 | 412 JS / 604 PY green; build green; exact `npm test` blocked by missing local `.venv` | Replaced dashboard’s lazy `blueprints.calendar_api` public-share import with service-backed local adapters, preserving the route, template payload, 404 response, and patch interception. Remaining dashboard-summary, admin, and lazy service-import phases are deferred; no push. |
| 2026-07-31 | J (remaining cycle phases) | fe42099, 246b913, acd21b6 | 412 JS / 604 PY green; build green; exact `npm test` blocked by missing local `.venv` | Completed the remaining bounded cycle breaks: dashboard summary calendar/task edges, admin settings/chat edges, and scheduler, Discord Gateway, and global-search lazy service imports. Preserved blueprint-level patchability and stopped without pushing or starting another cycle. |
| 2026-08-01 | I (phase 3) | 28289b0 | 412 JS / 605 PY green; npm test green; build green | Migrated both request-time shell context branches from direct Appwrite endpoint/project reads to the frozen facade, preserving frontend defaults and explicit empty values. Storage capability, database/instance, chat/Discord, scheduler/operations, and residual configuration batches remain deferred; no push. |
| 2026-08-01 | I (phase 4) | 5594aa7, c4e33d5 | 412 JS / 608 PY green; npm test green; build green | Completed the bounded database/instance/calendar path and storage-capability batches through the read-only facade, preserving path precedence, raw production matching, bucket defaults, and missing-or-empty capability behavior. Entitlement/tier, chat/Discord, scheduler/operations, backup, deployment, instance/, and infrastructure reads remain deferred; no push. |
| 2026-08-01 | I (completion) | 1e127a0–d03b178 | 412 JS / 634 PY green; npm test green; build green | Completed Theme I in nine bounded commits after four independent Luna Max audits. Routed all eligible product environment reads through the frozen facade while preserving import/startup/request/job/CLI timing, defaults, normalization, and errors. The AST boundary test documents the intentional exceptions: protected entitlement logic, the protected backup script, OAuth environment mutation, and full-environment inheritance for untouched VPS subprocesses. No deployment, `instance/`, VPS configuration, production infrastructure, or `services/app_config.py` changes; no push. |
| 2026-08-01 | K (phase 1: presence query/status core) | 7b0d6d29 | 412 JS / 634 PY green; 14 targeted; npm test green; build green | Extracted six runtime presence freshness/query/status helpers into `services/chat_presence_runtime.py` behind per-call blueprint adapters. Preserved all `blueprints.chat_api` symbols, nested patch interception, the 25-route map, SSE listener state, Discord/database behavior, and external contracts. Deliberate callback bypass produced the required red test before restoration. Later presence, Discord/rendering, summary/bootstrap, delivery routes, and any SSE reassessment remain deferred; no push. |
| 2026-08-01 | L (phase 1: characterization) | a1b21750 | 13 new tests; final safe targeted: 28 passed; 412 JS passed; build passed; mutation probe red then green; literal `npm test` failed because this worktree lacks `.venv` and broader Python execution reaches forbidden `instance/` or unmocked service paths | Characterized provider-identity and authentication-session boundaries without changing production behavior. Harness isolation remains deferred; no push. |

### Deferred items and open questions

- `.desloppify/plan.json` needs a rescan; its clusters name files that no longer exist and
  `config.json` already has `needs_rescan: true`. Rescan before trusting any score as a measure of
  this refactor's progress.
- The scanner reports "131 resolved findings never committed — verified score depressed." Its commit
  log is empty (`commit_log: []`, `uncommitted_issues: 127`). Score history is unreliable until
  that is reconciled; do not use the strict score as a success metric this cycle.
- Desloppify `zone_overrides` mislabels three files as classic scripts that are really ES modules:
  `static/js/core/command-palette.js`, `static/js/notes/editor.js`, `static/js/tasks/task.js`. It
  also lists `static/js/login.js`, which does not exist.
- `static/js/core/appwrite.js` appears orphaned — it expects a global `Appwrite` SDK that no template
  loads, and `tests/test_template_asset_order.py:27–33` actively asserts the SDK is *not* loaded.
  Determine whether to wire or retire it; do not delete on assumption.
- SortableJS is loaded two ways: CDN in `dashboard.html:28` and `notes.html:23`, npm import in
  `tasks/task-components.js:3`. Pick one.
- `blueprints/invites_api.py` has no `url_prefix`; its routes hardcode `/settings/api/invites`
  (L74). Inconsistent with the other blueprints but working — leave unless a theme covers it.
- `services/calendar_store.py` keeps its own `calendar_connection` and DDL (L232–258) from when
  calendar lived in a separate SQLite file. `app.py:51` now points `CALENDAR_SQLITE_PATH` at the main
  DB. Legacy worth unwinding eventually; not urgent.
- No CI exists. `.github/workflows/` is present locally but empty, and there are no test or lint git
  hooks. `npm test` is entirely dependent on someone remembering to run it.
