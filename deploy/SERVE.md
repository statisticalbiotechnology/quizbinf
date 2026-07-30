# Deploying on SciLifeLab Serve

Serve's app form has four relevant fields and **no way to set environment
variables**, so the image is built to configure itself from the image defaults
plus a file on the mounted volume.

## App form

| Field | Value |
| --- | --- |
| Docker image | `ghcr.io/statisticalbiotechnology/quizbinf:sha-<commit>` |
| Port | `8000` (Serve allows 3000–9999) |
| Mount path | `/home/data` — **never `/app`**, which would shadow the application code |
| Title | quizbinf — in-class quiz for bioinformatics teaching |
| Description | see below |

The 1 GB default volume is ample: a whole course of answers is well under a
megabyte. Serve provides no managed database, so the app stores everything in
SQLite on that volume.

## Configuration file

There is no env-var field, so the app also reads settings from
`/home/data/quizbinf.env` — upload it into the project storage (Files, or a
notebook attached to the same project). Real environment variables still win
where a platform provides them.

```ini
# /home/data/quizbinf.env
MOCK_LOGIN=true
ENVIRONMENT=development
TEACHER_USERNAMES=some-non-obvious-name
```

Everything else configures itself:

| Setting | Behaviour when unset |
| --- | --- |
| `DATABASE_URL` | SQLite at `<mount>/quizbinf.db`, i.e. on the persistent volume |
| `SESSION_SECRET` | random secret generated once and stored at `<mount>/session_secret`, so logins survive restarts |
| `PUBLIC_BASE_URL` | derived from the request's forwarded headers, so the QR code points at the real Serve hostname |
| `DATA_DIR` | `/home/data` |

If the volume is missing or read-only the app still starts: the database falls
back to the container filesystem (**ephemeral — answers are lost on redeploy**)
and the session secret becomes per-process (logins drop on restart).

## Security warning for the current state

`MOCK_LOGIN=true` means **there is no authentication**. Anyone who opens the
public URL can log in as any username they type, including whatever is in
`TEACHER_USERNAMES`, which grants full teacher control. Serve apps are publicly
listed, so:

- use a non-obvious `TEACHER_USERNAMES` value while the app is public;
- treat the deployment as disposable and do not collect real student data;
- remove `MOCK_LOGIN` once KTH OIDC login is implemented, at which point
  `ENVIRONMENT=production` becomes the correct setting.

## Serve policy notes

- Serve requires the **code and data behind a hosted app to be publicly
  available**, so the GitHub repository needs to be public. The GHCR package
  must be public too, or Serve cannot pull the image.
- Serve states it does **not support apps with sensitive data**. Answers tied
  to a KTH-id are personal data under GDPR; keep the exports aggregate-only
  and avoid storing anything beyond username and answer.

## Suggested description

> Live in-class quiz tool for a KTH bioinformatics course. Students scan a QR
> code to answer a multiple-choice question individually, discuss it with their
> neighbours, then answer the same question again — letting the teacher compare
> the two response distributions (peer instruction). Open source, Apache-2.0:
> https://github.com/statisticalbiotechnology/quizbinf
> This is a test deployment under active development.
