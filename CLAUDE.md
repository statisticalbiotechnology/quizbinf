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
  - *Teacher view*: `/teacher` to author, and four views of a running session,
    each with its own URL so one can be projected from a second window —
    `/teacher/session/:code/{join,control,report,people}`. Join shows the QR
    and how many have joined; Control opens and halts rounds; Report shows the
    pre/post distributions; **People is per-student and must not be
    projected**.
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
- **Question text is Markdown**, rendered *and sanitised on the server*
  (`app/markdown.py`, markdown-it-py + nh3) and exposed as `text_html`
  alongside the source. Clients bind it with `[innerHTML]`, so Angular
  sanitises again — a teacher is trusted, but the output is shown to every
  student in the room. Raw HTML in the source is escaped, not passed through.
  The authoring preview renders through the same endpoint
  (`POST /api/markdown/preview`) so it cannot disagree with what students see.
- **Figures are uploaded to the app**, not linked from elsewhere: a question
  must not break because an external host is down mid-lecture. Files land on
  the mounted volume (`<data>/images/`), are teacher-only to upload, capped at
  4 MB, checked against their magic bytes, and given unguessable names. **SVG
  is refused** — it can carry script and would be served from our own origin.
- **Questions are multiple choice with exactly one correct choice.** Keep the
  model and UI to single-select radio buttons; no free text, no multi-select.
  Nothing is marked correct by default when authoring — a pre-selected first
  choice silently made it the right answer whenever the teacher did not
  notice, so the form refuses to save until one is chosen deliberately.
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
- **The session window is an *idle* timeout, and it slides.** A cookie lasts a
  week unused (`SESSION_MAX_AGE`), and any request that uses it re-issues it
  once it is older than a day (`SESSION_RENEW_AFTER`), so a session cannot
  lapse mid-lecture. The renewal lives in a middleware in `app/main.py`, not
  in the `current_user` dependency: FastAPI merges a dependency's response
  headers only when the endpoint returns data to serialise, so a cookie set
  there is silently dropped by anything returning a `Response` directly —
  `qr.svg`, the SPA fallback. `tests/test_session_cookie.py` pins that case.
- **The session secret must be identical in every process.** It is generated
  once and stored at `<data>/session_secret`, created with `O_EXCL` so that
  concurrent cold starts adopt one value instead of each writing its own, and
  cached per process. Both properties matter: a check-then-write race produced
  six different secrets from one cold start, and as a plain property it was
  re-evaluated on *every request*, so a cookie handed out by one request was
  rejected by the next. `GET /api/health` reports `instance` and a truncated
  hash of the secret — if either changes between two calls to the same URL,
  processes disagree and logins fail at random. Setting `SESSION_SECRET`
  explicitly sidesteps all of it.
- **Why a cookie was rejected is worth distinguishing.** `SignatureExpired`
  subclasses `BadSignature`, so catching only the latter reports every routine
  expiry as a forged cookie — which cost real debugging time. Expiry says
  *Session expired*; *Invalid session* means specifically that the server
  could not verify an in-date cookie, i.e. the session secret is not what
  signed it.

## Roster-checked identification (a stop-gap)

Every real login route needs an administrator at KTH to grant something, and
none had by the time the course needed to run. `ROSTER_LOGIN=true` plus
`ROSTER_TEACHER_PASSWORD=…` turns on a stand-in: a student types their KTH
address and is let in if it appears in a synced roster.

**This is identification, not authentication.** Anyone who knows a
classmate's address can answer as them. It is a deliberate, documented gap —
the submission window remains what stops answering from outside the lecture.
Retire it the moment a real IdP is available; do not build anything on top
of it that assumes the identity is proven.

- **Teachers need the shared password**, because the teacher views hold every
  student's participation record. Teachers come from `TEACHER_USERNAMES`, not
  from the roster (they are teachers in Canvas, so never appear in a *student*
  roster). Guessing is rate-limited per client in `app/throttle.py`, which is
  in-memory and so assumes the single replica the SSE broadcaster already does.
- Enabled without a teacher password it is **refused outright**, not run in a
  degraded mode: a blank password would let any student sign in as a teacher.
- **The login page must not list the class.** A dropdown of enrolled students
  would publish the roster to anyone who opens the page. The field is instead
  a type-ahead over `GET /api/auth/roster-suggest`, which is deliberately
  grudging: nothing until three characters, prefix rather than substring, at
  most eight matches, rate-limited per client, and scoped to
  `CANVAS_COURSE_ID`. This still leaks the roster to anyone patient enough to
  try many prefixes — a smaller hole than handing the class over on page load,
  but a hole, and one more reason to retire this mode. A refusal never says
  who *is* enrolled.
- **One device, one student identity** (`DEVICE_BINDING_HOURS`, default 12).
  An opaque cookie binds a browser to the first identity it claims, so a
  student cannot sign in as each of their friends in turn on one phone and
  answer for all of them. It identifies a browser, never a person — no
  fingerprinting. The binding outlives *logout* deliberately (otherwise
  signing out would bypass it) and expires on its own so a shared or replaced
  device recovers without anyone intervening. Teachers are exempt, since a
  teacher's laptop may legitimately be used to demonstrate the student view.
- Login and roster sync share one normalisation
  (`canvas.username_from_login_id`); if they disagreed on case or whitespace,
  every match would silently fail.

## Canvas: the course roster

Reading the roster is the one piece of Canvas integration that is
**self-service**: the teacher generates a personal access token at
`https://canvas.kth.se/profile/settings` (Approved Integrations → New Access
Token) and sets `CANVAS_TOKEN`. No administrator is involved. The token acts
as that teacher, so it is a secret: it lives in the config file on the volume,
never reaches a client, and is never logged.

- `app/canvas.py` reads `GET /api/v1/courses/{id}/users`. **It must follow the
  `Link: rel="next"` chain** — Canvas caps `per_page` at 100 and reports no
  total, so a course of 137 students silently returns 100 without it. That is
  pinned by a test.
- **TAs are synced alongside students** (`ROSTER_ENROLMENT_TYPES`), so the
  teacher has someone who can sign in exactly as a student does and rehearse a
  lecture. They are indistinguishable from students afterwards, including in
  the attendance export — filter them out there by username if it matters.
  Teachers are deliberately excluded: they hold every student's participation
  record and sign in with the shared password.
- **Course listing includes unpublished courses.** Asking Canvas for
  `state[]=available` returns published courses only, which hides a course
  still being prepared — exactly when its roster is wanted. The three
  non-deleted states are requested explicitly, and `workflow_state` is carried
  through so the dropdown can label anything not currently running.
- A sync is a **mirror, not an append**: students who dropped disappear.
  Removing a roster row removes nothing else — answers live in their own table,
  so a student who drops still appears in the participation record for the
  sessions they attended.
- **`kthid` (Canvas `sis_user_id`, a `u1…` value) is the identifier to match
  on**, not the username. It survives a username change, and it is the same
  identifier KTH's own IdP exposes — so a student who authenticates through
  Canvas today and through KTH later stays one person instead of becoming two.
  `login_id` (`shiraza@kth.se`) supplies the display username.
- **Email is deliberately not stored.** Canvas returns it; the app never sends
  mail, so keeping it would be personal data held for no reason.
- The roster is personal data: teacher-only endpoints, and the dashboard shows
  counts rather than names.

The roster also removes a dependency from the *login* work still to come:
Canvas OAuth2 returns only a Canvas user id, and the roster already maps that
id to a KTH identity — so the developer key needs no extra API scopes, and a
valid Canvas login from someone not enrolled can be refused.

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
- **Serve provides no env-var field and no managed database.** The app
  therefore configures itself (see `deploy/SERVE.md`): settings are also read
  from `/home/data/quizbinf.env` on the mounted volume; `DATABASE_URL`
  defaults to SQLite on that volume; `SESSION_SECRET` is generated once and
  persisted there; and `PUBLIC_BASE_URL` is derived from the request's
  forwarded headers so the QR code resolves without configuration. Keep this
  property — anything new that *must* be configured has to work through the
  volume file, not an env var alone.
- The app must work behind a reverse proxy at a fixed public hostname.
  `PUBLIC_BASE_URL` must be set to that hostname — the QR-code URL is built
  from it, so getting it wrong means students scan a code pointing nowhere.
- PostgreSQL: assume a managed/cluster-provided instance if available,
  otherwise a simple StatefulSet with a PVC. Answers are the only precious
  data — keep the schema migration story simple (one migration tool, run as an
  init step on deploy).

## CI

Both workflows are active in `.github/workflows/`.

- `ci.yml` runs backend pytest and frontend unit tests + production build on
  every push and PR.
- `publish-image.yml` builds `deploy/Dockerfile` (context = repo root) and
  pushes to GHCR on `main` and on `v*` tags. Cut a release image with
  `git tag v0.2.0 && git push origin v0.2.0`.

Note for automated contributors: a token without the `workflow` scope cannot
create or update files under `.github/workflows/`. Land such changes from an
account that has it, or stage the file elsewhere and move it in a follow-up.

### Testing a built image without Serve

`deploy/docker-compose.ghcr.yml` runs the published GHCR image against
Postgres locally — useful during Serve maintenance windows and for
reproducing a specific deployed tag. It exercises the image and the Alembic
startup migration but **not** SSE through a reverse proxy; for that, expose
the container via a temporary tunnel and set `PUBLIC_BASE_URL` to the tunnel
URL so the QR code resolves. See the README.

## Development conventions

- **Branching:** develop on feature branches; `main` is the deployable state.
- **Commits:** clear, descriptive messages; small logical commits.
- **License:** Apache-2.0 (see `LICENSE`).
- **Testing:** backend logic that pairs pre/post rounds and enforces
  round-open/closed rules is the heart of the app — it must have unit tests
  (`backend/tests/test_rounds.py`, `test_answers.py`). Frontend: at minimum,
  tests for the answer-submission and session-follow logic
  (`frontend/src/app/api.service.spec.ts`). Keep both green.
- **A green build is not evidence the app renders.** A broken QR code shipped
  twice with unit tests passing and `ng build` succeeding. `npm run e2e`
  (Playwright, `frontend/e2e/smoke.spec.mjs`) drives a real browser through
  one lecture against the built frontend served by the backend, and asserts
  the things only a browser can see: that the projected QR image actually
  loads (`naturalWidth > 0`), and that a student's page updates over SSE
  without a reload. Run it before claiming a UI change works.
- **Privacy:** individual answers are personal data (GDPR). Never expose
  per-student answers to other students; teacher views show aggregates.
  Provide an export (CSV) of aggregates, and keep any per-student export
  teacher-only and minimal. The **Participants view is the only place in the
  app that shows individuals** — it is teacher-only, restricted to the
  session's own owner, hides names until explicitly revealed so opening it in
  front of a class exposes nobody, and is labelled *do not project*. Keep
  those properties if you touch it. Two further invariants worth keeping:
  the student `state` payload omits `is_correct` so the answer cannot be read
  out of the network tab, and a student is told only *their own* current
  choice (`my_choice_id`).
- **Time/ordering:** the server is the single source of truth for whether a
  round is open; the client must not trust its own clock.
- **Answers are the only irreplaceable data.** Nothing expires them: sessions,
  rounds and answers are rows that live as long as the volume does. Two paths
  can still destroy them, and both are deliberate — the Control view's *reset*
  (documented above), and deleting a question. The latter used to be silent
  and much worse than it looked: nothing cascades from a question to the
  rounds that asked it, so the answers survived while `Round.question` became
  None, and the participation report for *every* session using that question
  raised instead of rendering. Deleting a question that has been asked is now
  refused (409), and the report skips a stranded round rather than failing.
  See `tests/test_answers_survive.py`. Since the database is a single SQLite
  file with no backup, exporting `participation.csv` after a lecture is the
  only real safeguard.
- **Editing a question follows the same principle.** Text, choice wording,
  choice order and which choice is correct can all be changed at any time,
  including after the question has been asked — fixing the wrong answer being
  marked correct is exactly what editing is for. The one refusal is removing a
  choice students have already picked, because an answer points at a choice
  id. Choices therefore carry their id through an edit, so the server can tell
  a rewording from a replacement. The authoring form is one component
  (`teacher/question-editor.component.ts`) shared by create and edit, so an
  edit form cannot quietly become a worse tool than the one that wrote the
  question. See `tests/test_edit_question.py`.
- **The term report is attendance, not marking.** `GET /api/reports/
  participation.csv` spans every session its owner has run and answers one
  question per student per session: did they answer *both* bouts of every
  question that was asked twice. Correctness is deliberately absent — it is
  the participation-credit record. A question that never got its second bout
  counts against nobody, and a session where no question ran both bouts is
  blank rather than an absence. A failed cell carries the fraction
  (`no (1/2)`) so the all-or-nothing rule cannot silently hide a student who
  answered most of them. Personal data, so teacher-only and labelled
  do-not-project like the Participants view.

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

# browser smoke test: builds the frontend, serves it from the backend the way
# the production image does, and drives one lecture end to end
cd frontend && npm run e2e
CHROME_BIN=/path/to/chrome npm run e2e                # if Chromium is not on PATH
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
| `GET /api/auth/methods` | all | which login forms this deployment offers |
| `POST /api/auth/roster-login` | all | **stop-gap:** identify against the roster; teachers need the shared password |
| `GET /api/auth/roster-suggest?q=` | all | type-ahead over the roster: ≥3 chars, prefix, capped, rate-limited |
| `GET /api/auth/login` | all | KTH OIDC flow (**not implemented yet**) |
| `POST /api/quizzes`, `POST /api/quizzes/{id}/questions` | teacher | author content |
| `PUT /api/quizzes/{id}/questions/{qid}` | teacher | edit a question; send back the id of every choice kept |
| `DELETE /api/quizzes/{id}/questions/{qid}` | teacher | delete a question — **409 once it has been asked** |
| `POST /api/sessions?quiz_id=` | teacher | start a lecture session |
| `GET /api/sessions/{code}/join-url` | teacher | the URL the QR code encodes |
| `POST /api/sessions/{code}/rounds` | teacher | open a `pre`/`post` round |
| `POST /api/sessions/{code}/rounds/{id}/close` | teacher | close the open round |
| `GET /api/sessions/{code}/live` | teacher | answer count for the open round (no breakdown) |
| `GET /api/sessions/{code}/participants` | teacher | how many joined (counts only, no names) |
| `GET /api/sessions/{code}/participation` | teacher | **per-student** correctness (personal data) |
| `GET /api/sessions/{code}/participation.csv` | teacher | the same as CSV |
| `GET /api/reports/participation[.csv]?from=&to=` | teacher | **end of term:** attendance across every session, yes/no per session, no correctness |
| `GET /api/roster/status` | teacher | is Canvas configured, and what has been synced |
| `GET /api/roster/courses` | teacher | Canvas courses the token's owner teaches |
| `POST /api/roster/sync?course_id=` | teacher | mirror a course's students into the local roster |
| `GET /api/roster?course_id=` | teacher | the stored roster (**personal data**) |
| `GET /api/sessions/{code}/questions/{id}/comparison` | teacher | pre vs post counts |
| `DELETE /api/sessions/{code}/questions/{id}/rounds` | teacher | reset a question — **discards its answers** so it can be run again |
| `POST /api/images` | teacher | upload a figure; returns Markdown to paste |
| `POST /api/markdown/preview` | teacher | render Markdown for the authoring preview |
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
- [ ] **Presenter polish:** a full-screen projection mode (large QR, large
      histogram, no chrome).
- [ ] **Stronger attendance guard.** The open/closed window stops answering
      between rounds, but a student who is texted the 6-character session
      code can still answer from outside the lecture hall during a window.
      Options if this turns out to matter: a per-round code shown only on the
      projected slide and required with the answer, or a short auto-closing
      window. Not built — decide whether it is worth the friction.
- [ ] **Question reordering and quiz deletion.** Questions can now be created,
      edited and deleted; reordering them within a quiz, and deleting a whole
      quiz, are still missing.
- [ ] Consider showing students the correct answer after the post round
      closes (currently never revealed to them).
