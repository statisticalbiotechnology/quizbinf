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
- **Pre/post pairing.** Every answer is stored with the round it belongs to
  (`pre` or `post`) so the two distributions can be compared per question.

## Architecture (planned)

Nothing is implemented yet; the repository currently contains only this file,
a license, and a `.gitignore`. The intended shape:

```
quizbinf/
├── frontend/     Angular SPA (student view + teacher/presenter view)
├── backend/      REST + WebSocket API, OIDC login, persistence
├── deploy/       Dockerfile(s), docker-compose for local runs, Serve notes
└── CLAUDE.md
```

- **Frontend:** Angular (the `.gitignore` is already set up for it). Two main
  surfaces:
  - *Student view:* mobile-first; scan QR → login → answer the active question.
  - *Teacher view:* create quizzes/questions, run a session (open/close
    rounds), display the QR code and live result histograms for projection.
- **Backend:** **Python / FastAPI** (decided — Python fits the bioinformatics
  community that will maintain this, and FastAPI keeps the service small).
  One service exposing:
  - a REST API (login, quiz/question CRUD, answer submission, teacher
    controls, aggregate results);
  - an **SSE** endpoint (decided — students only *receive* state changes;
    answers go over REST) that pushes session-state changes (question
    opened/closed, round changed) to connected clients. Use `sse-starlette`;
    plan for client auto-reconnect + full state resync on connect.
  - Persistence via SQLAlchemy; migrations via Alembic.
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
- The app must work behind a reverse proxy at a fixed public hostname; the
  WebSocket endpoint must survive proxying (plan for reconnect + state resync
  on the client).
- PostgreSQL: assume a managed/cluster-provided instance if available,
  otherwise a simple StatefulSet with a PVC. Answers are the only precious
  data — keep the schema migration story simple (one migration tool, run as an
  init step on deploy).

## Development conventions

- **Branching:** develop on feature branches; `main` is the deployable state.
- **Commits:** clear, descriptive messages; small logical commits.
- **License:** Apache-2.0 (see `LICENSE`).
- **Testing:** backend logic that pairs pre/post rounds and enforces
  round-open/closed rules is the heart of the app — it must have unit tests.
  Frontend: at minimum, tests for the answer-submission and session-follow
  logic.
- **Privacy:** individual answers are personal data (GDPR). Never expose
  per-student answers to other students; teacher views show aggregates.
  Provide an export (CSV) of aggregates, and keep any per-student export
  teacher-only and minimal.
- **Time/ordering:** the server is the single source of truth for whether a
  round is open; the client must not trust its own clock.

## Local development (once components exist)

Document the exact commands here as soon as they are real. Intended shape:

```bash
# frontend
cd frontend && npm install && npm start        # ng serve on :4200

# backend
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload
                                               # on :8000, mock-login enabled

# full stack
docker compose up                              # app + postgres, for e2e testing
```

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
