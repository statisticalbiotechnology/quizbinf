# CLAUDE.md

This file gives Claude Code (and human contributors) the context needed to work
in this repository.

## What this project is

**quizbinf** is a live, in-class quiz application for a bioinformatics course at
KTH, built around the *peer instruction* teaching method (Mazur-style):

1. The teacher shows a question in class; students answer **individually**
   (the *pre* round).
2. Students discuss the question with their peers.
3. The **same question** is asked again and students answer a second time
   (the *post* round).
4. The teacher can compare pre/post answer distributions to see whether the
   discussion shifted the class toward the correct answer.

Key product requirements:

- **QR-code entry.** During class the teacher projects a QR code. Scanning it
  takes students straight to the currently active quiz/question on their
  phones. The QR code encodes a short session URL (e.g. `/s/<session-code>`),
  so one code works for a whole lecture session.
- **KTH login.** Students authenticate with their KTH-id so answers can be
  tied to individuals (for participation credit) while results shown in class
  stay anonymous/aggregated.
- **Teacher control.** The teacher drives the flow live: open question →
  close pre round → discussion → open post round → close → reveal histogram.
  Clients should follow along in (near) real time.
- **The submission window is the attendance guard.** Answers are accepted
  *only* while a round is open; between rounds every submission is rejected
  (409). The teacher opens a short window, halts it, runs the discussion,
  opens the second window, and halts again before showing statistics. See
  `tests/test_submission_window.py`, which encodes this sequence. Note the
  limit: this stops answering *outside* the window, not a student who is sent
  the session code and answers from elsewhere during it (see below).
- **Never project a distribution while its round is open.** The teacher's
  screen is the projected one; showing the vote split before the discussion
  defeats the point of asking twice. While a round is open the teacher sees
  only an answer *count* (`GET /api/sessions/{code}/live`, teacher-only,
  count without breakdown); bars for a phase appear once that round is
  closed.
- **Pre/post pairing.** Every answer is stored with the round it belongs to
  (`pre` or `post`) so the two distributions can be compared per question.

## Architecture

Both components are scaffolded and working end to end (mock login; the KTH
OIDC flow is the one remaining stub).

```
quizbinf/
├── frontend/     Angular 19 SPA (student view + teacher/presenter view)
│   └── src/app/  api.service.ts (REST + SSE), auth.service.ts, guards.ts,
│                 login/, student/, teacher/
├── backend/      FastAPI: REST + SSE, session cookie auth, persistence
│   ├── app/      config, db, models, schemas, service (round rules),
│   │             events (SSE broadcaster), routers/
│   ├── alembic/  migrations
│   └── tests/    round-lifecycle and answer-rule unit tests
├── deploy/       Dockerfile (multi-stage), docker-compose.yml
└── CLAUDE.md
```

- **Frontend:** Angular 19, standalone components with signals. Two surfaces:
  - *Student view* (`/s/:code`): mobile-first; scan QR → login → answer the
    active question. Follows the session over SSE and re-syncs via
    `GET /state`.
  - *Teacher view* (`/teacher`, `/teacher/session/:code`): create
    quizzes/questions, run a session (open/close rounds), display the QR code
    and pre/post histograms for projection.
- **Backend:** **Python / FastAPI** (decided — Python fits the bioinformatics
  community that will maintain this, and FastAPI keeps the service small).
  One service exposing:
  - a REST API (login, quiz/question CRUD, answer submission, teacher
    controls, aggregate results);
  - an **SSE** endpoint (decided — students only *receive* state changes;
    answers go over REST) at `GET /api/sessions/{code}/events`, using
    `sse-starlette`. Every event is a *full state snapshot*, so a client that
    missed events recovers by applying the next one; clients also call
    `GET /api/sessions/{code}/state` on connect. Keep-alive comments every
    15 s stop proxies from dropping idle streams.
  - Persistence via SQLAlchemy; migrations via Alembic.
- **Where the rules live:** `app/service.py` owns the round lifecycle — only
  one round open per session, a phase can be run only once, `post` requires a
  closed `pre`, answers rejected once closed, one answer per user per round
  (last write wins). Routers translate `RuleViolation` into HTTP 409. Put new
  domain rules there, not in the routers.
- **SSE broadcaster** (`app/events.py`) is in-memory and therefore assumes a
  **single replica**. Scaling out requires Postgres LISTEN/NOTIFY or Redis
  pub/sub instead.
- **Questions are multiple choice with exactly one correct choice.** Keep the
  model and UI to single-select radio buttons; no free text, no multi-select.
- **Database:** PostgreSQL. Core entities:
  - `User` (KTH-id, display name, role: teacher/student)
  - `Quiz` → `Question` (text, ordered choices, exactly one correct choice,
    optional image/markdown)
  - `Session` (a lecture run of a quiz; owns the short code in the QR URL)
  - `Round` (question × phase, phase ∈ {pre, post}; open/closed timestamps)
  - `Answer` (user × round × choice, timestamped; one answer per user per
    round, last write wins while the round is open)

## Authentication: KTH-id

- KTH provides login via **OpenID Connect** (login.kth.se) and via **SAML
  through SWAMID**. Prefer OIDC — it is far simpler to integrate and to run
  on Kubernetes. Client registration is requested from KTH IT.
- The backend handles the OIDC flow (authorization code + PKCE) and issues its
  own session cookie/JWT to the SPA; never put OIDC client secrets in the
  frontend.
- The KTH username (e.g. `lukask`) is the stable user key. Store the minimum:
  username, name, and affiliation claim if available.
- Teachers are designated by an allowlist in configuration (or a role flag in
  the DB), not by anything derived from the IdP.
- For local development there must be a **mock login mode** (env-flag
  controlled, hard-disabled in production builds) so the app can be developed
  without reaching KTH's IdP.

## Deployment: SciLifeLab Serve

Target (decided): **SciLifeLab Serve**, https://serve.scilifelab.se. Serve
runs apps from Docker images and provides the ingress/TLS and public hostname,
so we do not manage our own Ingress objects. Consequences for how we build:

- Everything ships as **Docker images**; keep Dockerfiles in `deploy/` or next
  to each component. Multi-stage builds; the Angular app is served as static
  files (from the backend or an nginx container).
- **Images are built by GitHub Actions and published to GHCR**
  (`publish-image.yml` → `ghcr.io/<owner>/quizbinf`), which is where Serve
  pulls from. The workflow runs on pushes to `main` and on
  `v*` tags; it needs no configured secret because it authenticates with the
  built-in `GITHUB_TOKEN` (`packages: write`). Tags produced: `latest` (from
  `main`), the branch name, `sha-<commit>`, and semver tags for releases.
  **The GHCR package must be made public** (Packages → quizbinf → Package
  settings → Change visibility) or Serve cannot pull it; GHCR packages are
  private by default. Prefer deploying an immutable `sha-` or version tag
  over `latest` so a redeploy is reproducible.
- Serve deploys a single app image behind its own ingress with TLS, so prefer
  **one image** that serves both the API and the built Angular app as static
  files from FastAPI. No Kubernetes manifests of our own are needed for Serve;
  keep a `docker-compose.yml` for local full-stack runs instead.
- SSE must survive Serve's reverse proxy: disable proxy buffering assumptions,
  send periodic keep-alive comments, and make the client reconnect + resync.
- All configuration via **environment variables** (12-factor): database URL,
  OIDC issuer/client-id/secret, session secret, public base URL (needed to
  build the QR-code URLs and OIDC redirect URI). Secrets come from Kubernetes
  `Secret`s — never commit them.
- The app must work behind a reverse proxy at a fixed public hostname.
  `PUBLIC_BASE_URL` must be set to that hostname — the QR-code URL is built
  from it, so getting it wrong means students scan a code pointing nowhere.
- PostgreSQL: assume a managed/cluster-provided instance if available,
  otherwise a simple StatefulSet with a PVC. Answers are the only precious
  data — keep the schema migration story simple (one migration tool, run as an
  init step on deploy).

## CI

Two workflows are written but **staged in `deploy/github-workflows/`, not yet
active** — see that directory's README for the one-command activation. They
could not be committed under `.github/workflows/` because the token used to
create them lacked the `workflow` scope; pushing them from a normal account
works fine.

- `ci.yml` runs backend pytest and frontend unit tests + production build on
  every push and PR.
- `publish-image.yml` builds `deploy/Dockerfile` (context = repo root) and
  pushes to GHCR on `main` and on `v*` tags. Cut a release image with
  `git tag v0.2.0 && git push origin v0.2.0`.

## Development conventions

- **Branching:** develop on feature branches; `main` is the deployable state.
- **Commits:** clear, descriptive messages; small logical commits.
- **License:** Apache-2.0 (see `LICENSE`).
- **Testing:** backend logic that pairs pre/post rounds and enforces
  round-open/closed rules is the heart of the app — it must have unit tests
  (`backend/tests/test_rounds.py`, `test_answers.py`). Frontend: at minimum,
  tests for the answer-submission and session-follow logic
  (`frontend/src/app/api.service.spec.ts`). Keep both green.
- **Privacy:** individual answers are personal data (GDPR). Never expose
  per-student answers to other students; teacher views show aggregates.
  Provide an export (CSV) of aggregates, and keep any per-student export
  teacher-only and minimal. Two invariants already enforced and worth keeping:
  the student `state` payload omits `is_correct` so the answer cannot be read
  out of the network tab, and a student is told only *their own* current
  choice (`my_choice_id`).
- **Time/ordering:** the server is the single source of truth for whether a
  round is open; the client must not trust its own clock.

## Local development

```bash
# backend — on :8000, SQLite, mock login enabled
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # set TEACHER_USERNAMES to your own KTH-id
uvicorn app.main:app --reload

# frontend — ng serve on :4200, proxies nothing; it calls :8000 directly
cd frontend && npm install && npm start

# full stack in containers (app image + postgres), for testing the real image
cd deploy && docker compose up --build         # app on :8000
```

Then open http://localhost:4200, log in with the username you put in
`TEACHER_USERNAMES` to get the teacher view, and log in as any other username
(in a private window) to play a student.

### Tests

```bash
cd backend && . .venv/bin/activate && pytest          # round + answer rules
cd frontend && npm test                               # needs a Chrome/Chromium
# headless (CI/containers):
CHROME_BIN=/path/to/chrome npx ng test --watch=false --karma-config=karma.conf.js
```

### Migrations

`app/main.py` calls `create_all` at startup, which is enough for dev and
tests, but **Alembic owns the schema in deployment** (the container runs
`alembic upgrade head` before starting uvicorn). After changing `models.py`:

```bash
cd backend && . .venv/bin/activate
alembic revision --autogenerate -m "what changed"
alembic upgrade head
```

### API shape

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `POST /api/auth/mock-login` | dev only | log in without the IdP |
| `GET /api/auth/login` | all | KTH OIDC flow (**not implemented yet**) |
| `POST /api/quizzes`, `POST /api/quizzes/{id}/questions` | teacher | author content |
| `POST /api/sessions?quiz_id=` | teacher | start a lecture session |
| `GET /api/sessions/{code}/join-url` | teacher | the URL the QR code encodes |
| `POST /api/sessions/{code}/rounds` | teacher | open a `pre`/`post` round |
| `POST /api/sessions/{code}/rounds/{id}/close` | teacher | close the open round |
| `GET /api/sessions/{code}/live` | teacher | answer count for the open round (no breakdown) |
| `GET /api/sessions/{code}/questions/{id}/comparison` | teacher | pre vs post counts |
| `GET /api/sessions/{code}/state` | student | full state snapshot (resync) |
| `GET /api/sessions/{code}/events` | student | SSE state stream |
| `POST /api/sessions/{code}/answers` | student | submit/change an answer |

## Decisions made

- [x] Backend: **Python / FastAPI** (SQLAlchemy + Alembic, `sse-starlette`).
- [x] Real-time transport: **SSE** (students only receive state; answers go
      over REST).
- [x] Hosting: **SciLifeLab Serve** — Serve provides ingress/TLS, we provide
      Docker images.
- [x] Question format: **multiple choice, exactly one correct choice**.
- [x] **No anonymous login in production.** KTH-id is required to answer.
      The only login bypass is the mock-login mode for local development,
      which must be hard-disabled in production builds.

## Open decisions (update this list as they are made)

- [ ] OIDC registration details with KTH IT (redirect URIs, allowed scopes) —
      to be requested; until then, develop entirely against mock login.

## Not built yet (next steps)

- [ ] **KTH OIDC login.** `GET /api/auth/login` returns 501. Implement the
      authorization-code + PKCE flow against login.kth.se, then call the same
      `get_or_create_user` + `set_session_cookie` path mock login already uses.
      Redirect URI will be `<PUBLIC_BASE_URL>/api/auth/callback`.
- [ ] **CSV export** of aggregate results (and a minimal teacher-only
      per-student participation export).
- [ ] **Presenter polish:** a full-screen projection mode (large QR, large
      histogram, no chrome).
- [ ] **Stronger attendance guard.** The open/closed window stops answering
      between rounds, but a student who is texted the 6-character session
      code can still answer from outside the lecture hall during a window.
      Options if this turns out to matter: a per-round code shown only on the
      projected slide and required with the answer, or a short auto-closing
      window. Not built — decide whether it is worth the friction.
- [ ] **Question editing/reordering and quiz deletion** — only create and
      delete-question exist today.
- [ ] Consider showing students the correct answer after the post round
      closes (currently never revealed to them).
