# TRADEOS Build Progress

_Last updated: 2026-11-05_

## Repository state at start (2026-11-05)
- Repository contained only `SPEC.md` (one commit). No existing code to preserve; no conflicts.
- Runtime: Windows 11, Python 3.13.5, Node 24.18, npm 11. **No Docker, no PostgreSQL, no Redis** installed.
- Running downloaded PostgreSQL binaries was blocked by the local permission policy, so local
  dev/tests use SQLite (ADR 0002). PostgreSQL is the production target (Docker Compose + CI).
- A concurrent edit (not by the build agent) added `AUTO_CREATE_SCHEMA` and hosted-Postgres URL
  normalisation (Neon/Vercel) to `config.py`, `db.py`, `main.py`. Kept as-is.

## Phase status
| Phase | Status | Evidence |
|---|---|---|
| 0 Setup | in progress | venv, requirements, .gitignore; docs/README/CI pending |
| 1 Foundation | done (backend) | auth, orgs, RBAC, tenant ctx, audit — `tests/test_auth.py`, `tests/test_tenant_isolation.py` |
| 2 Master data | done (backend) | products/units/retailers/suppliers/warehouses/price lists/CSV — `tests/test_master_data.py` |
| 3 Inventory & orders | done (backend) | ledger, pricing, validation, reservations, fulfilment, returns — `tests/test_orders.py` |
| 4 AI order inbox | done (backend) | demo parser + Claude adapter + matching + review/convert + eval — `tests/test_inbox.py`, `tests/test_eval_regression.py` |
| 5 Schemes | done (backend) | engine + versioning/approval + preview + extracted drafts + claims — `tests/test_scheme_engine.py`, `tests/test_schemes.py` |
| 6 Finance & reconciliation | done (backend) | PIs, credit notes, payments, rules R1–R7, cases, exceptions — `tests/test_finance.py` |
| 7 Analytics & polish | not started | |
| 8 Hardening | not started | |
| 9 Manufacturer portal | not started | |
| Frontend (Next.js) | not started | |

## Tests
`cd services/api && ../../.venv/Scripts/python -m pytest` → 82 passed (SQLite).

## Next actions (in order)
1. Alembic initial migration (replace AUTO_CREATE_SCHEMA stopgap for real deployments).
2. Synthetic seed generator (`python -m app.seed`) + analytics/audit/integrations/data-sharing routers.
3. Next.js frontend in `apps/web`.
4. Docs (README, CLAUDE.md, ARCHITECTURE, DATA_MODEL, API, SECURITY, AI_EVALUATION, INTEGRATIONS, USER_GUIDE, ADRs), Docker Compose, GitHub Actions CI, Playwright E2E.
