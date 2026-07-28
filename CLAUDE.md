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
├── deploy/       Dockerfiles, Kubernetes manifests / Helm chart
└── CLAUDE.md
```

- **Frontend:** Angular (the `.gitignore` is already set up for it). Two main
  surfaces:
  - *Student view:* mobile-first; scan QR → login → answer the active question.
  - *Teacher view:* create quizzes/questions, run a session (open/close
    rounds), display the QR code and live result histograms for projection.
- **Backend:** a single service exposing a REST API plus a WebSocket (or SSE)
  channel that pushes session-state changes (question opened/closed, round
  changed) to connected students. Prefer a small, boring stack — e.g. Python
  (FastAPI) or Node (NestJS) — decide once and record the choice here.
- **Database:** PostgreSQL. Core entities:
  - `User` (KTH-id, display name, role: teacher/student)
  - `Quiz` → `Question` (text, ordered choices, correct choice(s), optional
    image/markdown)
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

## Deployment: SciLifeLab Kubernetes

Target: a SciLifeLab Kubernetes cluster (e.g. **SciLifeLab Serve**,
https://serve.scilifelab.se, or a project-allocated namespace). Consequences
for how we build:

- Everything ships as **Docker images**; keep Dockerfiles in `deploy/` or next
  to each component. Multi-stage builds; the Angular app is served as static
  files (from the backend or an nginx container).
- Provide plain Kubernetes manifests (Deployment, Service, Ingress) or a small
  Helm chart in `deploy/k8s/`. Assume an ingress controller with TLS
  termination is provided by the cluster.
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
cd backend && <install> && <run dev server>    # on :8000, mock-login enabled

# full stack
docker compose up                              # app + postgres, for e2e testing
```

## Open decisions (update this list as they are made)

- [ ] Backend language/framework (FastAPI vs NestJS — pick one).
- [ ] Real-time transport: WebSocket vs SSE (SSE may be simpler and is enough,
      since students only *receive* state; answers go over REST).
- [ ] Exact hosting target: SciLifeLab Serve app vs raw namespace on a
      SciLifeLab cluster — affects whether we need our own Ingress/TLS setup.
- [ ] OIDC registration details with KTH IT (redirect URIs, allowed scopes).
- [ ] Whether sessions need an anonymous "no-login fallback" mode for guests
      or login outages.
