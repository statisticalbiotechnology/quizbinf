# Rehearsing a lecture against the deployment

`backend/loadtest/lecture.py` drives a whole lecture at a running instance:
a class loads the page, signs in, holds an SSE stream open per student the way
a phone does, a bout opens, everyone answers at once — and the teacher
refreshes the projected view in the middle of the burst.

The instance most worth pointing it at is the deployed one. A laptop has more
cores than the Serve container and a local disk instead of a mounted volume, so
a clean local run says little about the machine the class will actually hit.
This is how to run it against `https://quizbinf.serve.scilifelab.se` without
harming the class whose answers live in it.

## The two problems with testing in place, and what handles each

**There is no scriptable login.** The deployment's only login is KTH's
OIDC — `GET /api/auth/methods` returns `{"mock_login":false,
"roster_login":false,"oidc":true}`. There are no two hundred fake KTH
accounts, and driving KTH's IdP with scripted logins is neither possible nor
ours to do.

→ `LOADTEST_KEY` opens a narrow door: `POST /api/auth/loadtest-login` signs in
throwaway students. It is shut unless the key is set, it mints **students
only** (never a teacher — those views hold every student's participation
record), and every username it can produce starts with `loadtest-`, so nothing
it creates can collide with or be mistaken for a real student.

**Fake data would corrupt the attendance record.** Both attendance reports walk
every session their owner has run, and a session that ran rounds is a lecture
to them. A rehearsal left among them would enter the Canvas gradebook
denominator and mark the whole real class absent from a lecture that never
happened.

→ A session started with `loadtest=true` is flagged, and
`service.sessions_in_range` — the one place both reports go through — skips
flagged sessions. `DELETE /api/sessions/{code}` then removes the session, its
rounds, its answers and the throwaway students; it refuses any session that is
*not* a rehearsal, because a mistyped six-character code must never be able to
delete a real lecture's answers.

The teacher still signs in as themselves. `--teacher-cookie` lends the run a
session the teacher already has, rather than growing a second way to become
one.

## Running it

1. **Set a key** in `/home/data/quizbinf.env` on the volume and restart:

   ```
   LOADTEST_KEY=<48 random characters>
   ```

   Generate it with `python -c "import secrets; print(secrets.token_urlsafe(36))"`.
   Anything shorter than 24 characters is treated as unconfigured — a guessable
   key on a door that creates accounts is worse than no door.

2. **Get a teacher cookie.** Log in to the deployment in a browser as yourself,
   then copy the `quizbinf_session` cookie value (DevTools → Application →
   Cookies). It is your session: treat it as your password.

3. **Run it.** Both secrets go in the environment, never on the command line —
   a command line is visible to `ps` and lands in your shell history.

   ```bash
   cd backend
   export QUIZBINF_LOADTEST_KEY='…'
   export QUIZBINF_TEACHER_COOKIE='…'
   python -m loadtest.lecture \
       --base-url https://quizbinf.serve.scilifelab.se \
       --students 200 --questions 4
   ```

   It prints the session code before it does anything, purges at the end, and
   reports what it removed.

4. **Afterwards**, remove `LOADTEST_KEY` from the config file and restart. The
   door should not stay open between rehearsals.

### If it dies halfway

The purge does not run. The session code is the first line it printed:

```bash
curl -X COOKIE… -X DELETE https://quizbinf.serve.scilifelab.se/api/sessions/<code>
```

or open the app as the teacher and delete it. Until then the session is still
flagged, so no report counts it — the rows are untidy, not harmful.

### Knobs

| flag | default | what it changes |
| --- | --- | --- |
| `--students` | 150 | size of the class |
| `--questions` | 4 | questions, each asked twice |
| `--arrival-seconds` | 20 | how long the class takes to arrive |
| `--burst-seconds` | 5 | how long answering one bout takes |
| `--no-refresh-mid-burst` | off | leave out the teacher refresh |
| `--no-page-load` | off | skip the HTML and assets, drive only the API |
| `--no-purge` | off | keep the session, e.g. to inspect it |

Tightening `--arrival-seconds` and `--burst-seconds` is how to look for a
cliff: real classes are looser than anything you can set here, so a run that
holds at `--burst-seconds 0.5` has headroom.

## Reading the result

Relative signals, not absolute capacity — the load generator and the server may
be competing for the same cores, and 200 Python clients are not 200 phones on
hall wifi. Worth acting on:

- any failed request, especially a 500 or a `database is locked`;
- `db pool: peak N of 50` — at 50 the pool is the bottleneck;
- `health unanswered` above 0, which means the app stopped answering the one
  endpoint that needs no database;
- one endpoint far slower than the rest, or a p99 an order of magnitude past
  its p50;
- `slow:` lines in the deployment's log, which name any database request that
  held a concurrency slot for more than `SLOW_REQUEST_SECONDS`.

## The other option

A second Serve app from the same image, its own volume, `MOCK_LOGIN=true` and
`ENVIRONMENT=development` — then point the harness at it with no key and no
cookie. It exercises the same platform, proxy and image with nothing real
inside it, and it is the better choice if you want to try something that might
break the app rather than measure one that works.
