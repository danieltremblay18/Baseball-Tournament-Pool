# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **Flask + PostgreSQL** web application that manages a 13U baseball tournament
(Baseball Québec, Art. 42.11), deployed on **Render.com** with a **Neon** Postgres
database. It is a **faithful port** of an earlier Google Apps Script system that ran
inside a Google Sheet (that original lives in a *separate, unrelated* repo,
`TournoiBaseball.git`, as `TournoiBaseball_Script.gs` + `CLAUDE.md`). This repo is
standalone — it does **not** depend on that one at runtime.

The tournament has 2 classes (A, B) × 3 pools of 4 teams = 24 teams, round-robin
(6 games/pool = 36 games total), then semifinals. The hard part — and the reason
this app exists — is the **tiebreaker procedure (Art. 42.11)**: recursive,
three-tiered, with fractional innings, extra-innings exclusion (Note 4), forfeit
exclusion (Note 5), and a manual Priority-4 override.

## Layout

```
wsgi.py                     # entry point (gunicorn on Render, `flask run` local); loads .env
render.yaml, Procfile       # Render deploy config (repo root IS the app root)
requirements.txt
data/Horaire-tournoi-2026.xlsx   # the 2026 schedule, imported by `flask seed-schedule`
tests/test_engine.py        # port of the original test_tiebreaker.js — proves engine fidelity
tournoi/
  __init__.py               # app factory, extensions init, blueprint + CLI registration, ensure_initial_admin
  config.py                 # env-driven config; driver-agnostic Postgres URL; SQLite fallback
  extensions.py             # db (SQLAlchemy), login_manager, csrf singletons
  models.py                 # Game, SecondOverride, ForcedRank, User
  engine.py                 # PURE tiebreaker engine — 1:1 port of the .gs pure functions
  standings.py              # DB <-> engine bridge: get_game_results, compute_standings_model, build_admin_model, build_ledger
  public.py                 # public read-only page (= old doGet) + /api/standings JSON
  public_template.py        # the public HTML/CSS/JS, EXTRACTED VERBATIM from the .gs (do not hand-edit)
  auth.py                   # login/logout, user mgmt, forced password change
  admin.py                  # results entry, force-second/force-rank, schedule import, ledger, simulate, clear, export
  demo.py                   # simulate_results() — canned test scores (= old simulateMatchResults)
  seed.py                   # import the schedule from the .xlsx (stdlib only: zipfile + ElementTree)
  templates/                # Jinja templates for the admin/auth area (NOT the public page)
```

## Working with the code

There is no build step. Development commands (from this directory):

```bash
pip install -r requirements.txt
pytest -q                              # the engine regression suite — run this after ANY engine change

# Without DATABASE_URL, the app uses a local SQLite file (tournoi_local.db):
flask --app wsgi init-db               # create tables (also auto-created at startup)
flask --app wsgi seed-schedule         # import the 36 pool games from data/…xlsx
flask --app wsgi create-user <name> --admin
flask --app wsgi seed-demo             # inject canned test results
flask --app wsgi run --debug           # http://127.0.0.1:5000/
```

**`pytest` is the primary verification** and does not need a database — it exercises
`tournoi/engine.py` directly on hand-built game dicts. It is the Python port of
`tests/test_tiebreaker.js` from the original repo and locks in the exact same
scenarios (P2 flip, PDF Note-2 regression, Step-A head-to-head, Priority-4 manual +
"Forcer rang" override, `is_row_complete`, `calculate_innings`, Note 4, Note 5,
`resolve_second_representative`). **If you change the engine, this must stay green.**

## Architecture & invariants

**The engine (`engine.py`) is a line-for-line port of the original `.gs` pure
functions and must stay that way.** Game objects are Python dicts whose keys reuse
the original camelCase names (`scoreLocal`, `offVisiteur`, `raRatio`, `regDefLocal`,
`suppNeedsTie`, …) *on purpose* — it keeps the 1:1 correspondence obvious and lets
the ported tests read almost identically to the JS. Do not "pythonify" these keys
without a matching reason; the fidelity is the point. `order_teams` returns an
`OrderedResult` (a `list` subclass carrying `needs_manual_check`), mirroring the JS
`ordered.__needsManualCheck = true` pattern. The deep business rules (why fractions,
Note 4/5, the recursive three-tier tiebreak, Priority-4-only precedence of the
override) are documented in the original `TournoiBaseball_Script.gs` `CLAUDE.md` and
summarized in `engine.py` docstrings — read those before touching the math.

**The public page and the admin standings share the same engine — they cannot
diverge.** `public.py` builds its data from `compute_standings_model(classe)` and
`admin.py` from `build_admin_model(classe)`; both call the *same* pure functions in
`standings.py`/`engine.py`. This is the same guarantee the original had between its
`doGet` web app and the `Classements` sheets. The public model is the *stripped*
view (real ratios, no tiebreak tables); the admin model adds the bris-d'égalité
tables (regulation basis, Note 4), the decisive-criterion column, and the forcing
inputs.

**No calculated state is stored.** Unlike the Sheets version (which wrote P–T
columns and rebuilt `Classements` tabs), every standings view here is recomputed
from the DB on each request. There is therefore no live-recalc trigger, no
`LockService`, no multi-station lock to maintain — Postgres serializes the writes,
and each page read is authoritative. `Game` stores only the schedule + the raw
result entry; winners, innings, ratios, seeds are all derived on the fly.

**Data model ↔ Sheets mapping.** `Game` ≈ one row of "Résultats A/B" (schedule
columns A–G + manual entry H–O; the calculated P–T are never persisted).
`SecondOverride` ≈ a ticked "Forcer 2e" checkbox (Note 5). `ForcedRank` ≈ a "Forcer
rang" value (Priority 4), keyed by `scope` (`A1`/`A2`/`A3` for a pool's Step A, `C`
for Étape C, `B` for Étape B) + team. `User` replaces Google account sharing with
individual accounts. `get_game_results(classe)` is the analog of `getGameResults`:
it reads played `Game` rows and produces the engine game dicts (forfait score
normalization, `calculate_innings`, the Note-4 `reg*` fields).

**`public_template.py` is generated, not authored.** Its two big strings
(`PUBLIC_HTML_TEMPLATE`, `RULES_HTML`) were extracted verbatim from the original
`.gs` template literals (with a `/*__TITLE__*/`, `/*__DATA__*/`, `/*__RULES__*/`
injection scheme preserved, plus an added `<title>`/viewport). Data is injected the
same way `renderPublicHtml_` did — `window.DATA = <json>` with `<` escaped to
`<` to prevent a team name from closing `</script>`. If the public page needs a
real redesign, edit it here, but keep the injection markers.

**Config is driver-agnostic (`config.py`).** A Neon URL (`postgresql://…?sslmode=require`)
is rewritten to `postgresql+psycopg` if psycopg 3 is importable (Render, via
`requirements.txt`), else `postgresql+psycopg2` (dev machines that only have v2),
else plain `postgresql`. `postgres://` is normalized to `postgresql://`. With no
`DATABASE_URL` at all, it falls back to SQLite so you can develop offline. `wsgi.py`
loads `.env` with `override=True` so a local `.env` wins over a pre-existing
`DATABASE_URL` in the shell environment.

**Auth: initial admin + forced password change.** On the very first startup with an
empty `app_user` table, `ensure_initial_admin` creates an admin (`INITIAL_ADMIN_USERNAME`,
default `admin`) with a temporary password (`INITIAL_ADMIN_PASSWORD` if set, else a
random one printed in the startup logs), flagged `must_change_password`. A
`before_app_request` hook in `auth.py` redirects any authenticated user with that
flag to `/auth/change-password` before letting them into `/admin` (the public page
stays reachable). Admin-created and admin-reset passwords are also temporary.

## Deployment (Render + Neon)

`render.yaml` at the repo root defines the web service (gunicorn, `wsgi:app`).
Secrets are **not** committed: set `DATABASE_URL` (Neon), `SECRET_KEY`
(`generateValue`), optionally `INITIAL_ADMIN_PASSWORD`/`INITIAL_ADMIN_USERNAME`,
`DISPLAY_TZ`, `TOURNAMENT_TITLE` in the Render dashboard. Tables are auto-created at
startup (`db.create_all()`, idempotent, non-fatal if the DB is briefly unreachable).
After first deploy, run `flask --app wsgi seed-schedule` in the Render shell. See
`README.md` for the full runbook.

**Note on this repo's history:** it was split off from the umbrella project folder;
the old Google Sheets app (`gsheet/`) deliberately lives in a different repo
(`TournoiBaseball.git`) and is **not** part of this one. `.env` is git-ignored —
never commit the Neon connection string.

## Git workflow

This repo IS meant to be pushed (unlike the gsheet repo). Commit changes to a
branch and open a PR, or push to `main` when the user asks. Do not commit `.env`.
