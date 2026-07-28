# quizbinf

A live, in-class quiz app for a bioinformatics course at KTH, built around
**peer instruction**: each question is asked twice — once answered
individually, then again after students discuss it with their neighbours — so
the teacher can see whether the discussion moved the class toward the correct
answer.

- Students join by scanning a **QR code** projected in class and log in with
  their **KTH-id**.
- The teacher drives the flow live: open the *pre* round → halt it →
  discussion → open the *post* round → halt → show the two distributions
  side by side. Answers are accepted **only** while a round is open.
- While a round is open the teacher sees just a count of answers received;
  the distribution appears only after the round is halted, so a projected
  screen never shows the class how it voted before the discussion.
- Results shown in class are aggregate only; individual answers are never
  exposed to other students.

## Stack

| Part | Choice |
| --- | --- |
| Frontend | Angular 19 (standalone components, signals) |
| Backend | Python / FastAPI, SQLAlchemy, Alembic |
| Live updates | Server-Sent Events (`sse-starlette`) |
| Database | PostgreSQL (SQLite for local development) |
| Hosting | SciLifeLab Serve (single Docker image) |

## Quick start

```bash
# backend on :8000 (SQLite, mock login)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # put your own KTH-id in TEACHER_USERNAMES
uvicorn app.main:app --reload

# frontend on :4200
cd frontend && npm install && npm start
```

Open http://localhost:4200. Log in as the username listed in
`TEACHER_USERNAMES` for the teacher view; log in as any other username (use a
private window) to act as a student.

To run the production image locally, `cd deploy && docker compose up --build`.

## Tests

```bash
cd backend && . .venv/bin/activate && pytest
cd frontend && npm test
```

## Container image

GitHub Actions builds the image and publishes it to
`ghcr.io/<owner>/quizbinf` on every push to `main` and on `v*` tags. Deploy an
immutable tag (`sha-<commit>` or `v0.1.0`) on SciLifeLab Serve rather than
`latest`. The GHCR package must be set to **public** for Serve to pull it.

## Status

Working end to end against mock login. **KTH OIDC login is still a stub** —
see the "Not built yet" section of [CLAUDE.md](CLAUDE.md), which is also the
place to look for architecture notes and conventions.

## License

Apache-2.0 — see [LICENSE](LICENSE).
