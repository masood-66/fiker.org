# FIKER Moderated Forum Implementation Plan

> **For agentic workers:** Implement this plan natively in the bound FIKER project, then run an independent read-only connection review before delivery.

**Goal:** Replace the static youth-forum prototype with a real, persistent, account-required, staff-moderated community forum.

**Architecture:** Keep the existing SEO-friendly static HTML website and add a same-origin Flask API/server for dynamic forum data, Manus OAuth login, session validation and moderation endpoints. Use the managed MySQL database for durable users, categories, threads, replies and reports; never store forum state in process memory or the ephemeral filesystem. The browser loads the existing forum route and uses a focused `forum.js` client to call `/api/*` endpoints.

**Tech Stack:** Python 3.11, Flask, PyMySQL, PyJWT, Manus OAuth, managed MySQL, existing generated HTML/CSS/JS, Docker/Gunicorn for deployment.

**Spec:** User requested a “real working forum” and selected the recommended model: public reading, account-required posting/replies, staff moderation, child-safe defaults and an admin dashboard.

## Global Constraints

- Public visitors can read only approved content.
- Account login uses Manus OAuth; do not create a separate password system.
- New threads and replies enter `pending` moderation state and are not public until approved.
- Users can create topics, reply, edit/delete their own pending or approved content, and report content; users cannot moderate their own reports.
- Moderators can approve, hide, lock, pin and delete threads/replies and review reports.
- Child-safety UI must discourage names, exact addresses, contact details and identifying information; reports are not emergency response.
- All dynamic API responses are JSON, use explicit validation, parameterized SQL and `private, no-store` caching.
- Static website pages and assets remain available; API routes must not fall back to HTML.
- Managed Server and Database capabilities are one-way enabled for this project and are required by the feature.

## Review Focus

- OAuth state/nonce mismatch, forged/expired JWTs and cross-site Preview cookies must fail closed.
- Pending content must never appear in public thread/list queries.
- SQL input, HTML content and report reasons must be parameterized/escaped; rate limits must prevent obvious spam.
- Admin endpoints must require a database role, not merely a logged-in Manus account.
- Restarting or publishing must preserve database content and not re-seed over user edits.

## Project Structure

- `server.py` — Flask application, static fallback, OAuth/session routes, JSON API and health endpoint.
- `forum_db.py` — MySQL connection, additive schema migration/seed, query helpers and moderation transitions.
- `auth.py` — Manus OAuth URL/callback exchange, Preview JWT validation, app-session cookie helpers and role resolution.
- `requirements.txt` / `Dockerfile` — pinned runtime dependencies and production entrypoint.
- `scripts/forum.js` — login state, topic/reply forms, filtering, thread view, reports, moderation UI and accessible status messages.
- `styles/forum.css` — forum-specific layout, forms, moderation chips and responsive states.
- `build_site.py` — generate the forum shell and admin shell without fake posts; preserve the existing static site generator.
- `youth/forum/index.html` — public forum shell generated from the source.
- `admin/forum/index.html` — protected staff dashboard shell generated from the source.
- `public/manus-routes.json` — add the staff route while keeping API paths out of the page manifest.

---

### Task 1: Full-stack foundation and durable schema

**Files:** Create `requirements.txt`, `Dockerfile`, `forum_db.py`, `auth.py`; modify `server.py`.

- [ ] Add pinned Flask, Gunicorn, PyMySQL and PyJWT dependencies.
- [ ] Implement `init_schema()` with a version table and additive migrations for `forum_users`, `forum_categories`, `forum_threads`, `forum_posts` and `forum_reports`; seed only the three existing categories and three approved public starter topics if absent.
- [ ] Implement database helpers with `DATABASE_URL`, TLS-capable MySQL connections, context-managed cursors, parameterized queries and no destructive reset.
- [ ] Implement Manus OAuth login/callback/logout and `webdev_app_session` cookies with `SameSite=None; Secure` on public Preview, short-lived state/nonce binding and fixed post-login redirect.
- [ ] Validate the platform JWT with `MANUS_JWT_SECRET`/HS256 and `MANUS_PROJECT_ID` when Preview auto-login is present; resolve every identity into a database user and role.
- [ ] Add unauthenticated `GET /api/health` returning JSON 200 and start the Flask app on `0.0.0.0:${PORT:-3000}`.
- [ ] Preserve static file and extensionless route behavior for the existing generated website while reserving `/api/*`, `/auth/*`, `/admin/forum/` and `/youth/forum/` for the application.

### Task 2: Public forum and member API

**Files:** Modify `server.py`, `forum_db.py`; create/update `scripts/forum.js`, `styles/forum.css`; modify `build_site.py`.

- [ ] Add `GET /api/auth/me`, `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`.
- [ ] Add `GET /api/forum/categories`, `GET /api/forum/threads?q=&category=&page=`, `GET /api/forum/threads/<id>`, `POST /api/forum/threads`, `POST /api/forum/threads/<id>/replies`, `PATCH /api/forum/posts/<id>`, `DELETE /api/forum/posts/<id>`, and `POST /api/forum/posts/<id>/reports`.
- [ ] Require authentication for create/reply/edit/delete/report; validate title/body/category lengths, return 400/401/403/404/409/429 consistently, and rate-limit new threads/replies/reports per user/IP.
- [ ] Return approved-only public data with safe author display names, timestamps, reply counts and moderation-aware status for the current author.
- [ ] Replace prototype copy and placeholder topic cards with a real forum shell: login button/state, category filter/search, New topic form, topic list, thread/reply panel, report dialog, loading/empty/error states and child-safety notice.
- [ ] Wire the UI to accessible keyboard/focus states and `prefers-reduced-motion`; render user-entered text as text, never unsanitized HTML.

### Task 3: Staff moderation dashboard

**Files:** Modify `server.py`, `forum_db.py`, `build_site.py`; create/update `admin/forum/index.html`, `scripts/forum.js`, `styles/forum.css`.

- [ ] Add role-gated `GET /api/admin/forum/summary`, `GET /api/admin/forum/queue`, `POST /api/admin/forum/threads/<id>/moderate`, `POST /api/admin/forum/posts/<id>/moderate`, `GET /api/admin/forum/reports`, and `POST /api/admin/forum/reports/<id>/resolve`.
- [ ] Support approve, hide, lock, pin and delete transitions with audit metadata (`moderated_by`, `moderated_at`, `moderation_note`).
- [ ] Render a staff dashboard with pending queue, reports, filters, action confirmation, current moderator identity and clear unauthorized state.
- [ ] Bootstrap admin role only from a protected `FORUM_ADMIN_EMAILS`/`FORUM_ADMIN_OPEN_IDS` environment value; never make the first visitor admin automatically. If no bootstrap value is configured, show an explicit setup message rather than bypassing authorization.

### Task 4: Deployment, routes and integration validation

**Files:** Modify `public/manus-routes.json`; configure Webdev `deploy` and `routes` domains; update `app.config.ts` only if needed for valid project branding.

- [ ] Declare the committed Dockerfile and `/api/health` deploy contract; route `/api/*`, `/auth/*` and `/admin/forum/*` to server before static routes, keep `/assets/*` static, and preserve the static page catch-all.
- [ ] Run database migration/seed once against the managed database using a version guard; do not re-seed or overwrite member content on restart.
- [ ] Verify health, login initiation, anonymous public reads, authenticated write paths, pending visibility, moderation transitions, reports, rate limiting, direct page routes, static assets and mobile layout.
- [ ] Run source checks, Python compile/tests and an independent read-only connection review; fix confirmed findings before checkpointing.

## Verification Evidence

- `python3 -m py_compile server.py auth.py forum_db.py`
- `python3 -m unittest` or project test command covering schema idempotence, auth failure, public pending exclusion, post validation, moderation transitions and role checks.
- `curl` checks for `/api/health`, public thread list, missing API route behavior and static asset responses.
- Runtime checks against the managed database proving a created pending topic survives process restart and becomes visible only after moderator approval.
- Browser screenshot only if needed to inspect a concrete forum layout defect; source/runtime validation remains primary.
