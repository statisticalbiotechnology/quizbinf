# GitHub Actions workflows — activation required

These two workflows are **not active yet**. They live here because the
automated session that wrote them used a token without the `workflow` scope,
which GitHub refuses to let create or update files under `.github/workflows/`.

Move them into place and push from a machine with normal push rights:

```bash
mkdir -p .github/workflows
git mv deploy/github-workflows/ci.yml            .github/workflows/ci.yml
git mv deploy/github-workflows/publish-image.yml .github/workflows/publish-image.yml
git rm deploy/github-workflows/README.md
git commit -m "Activate CI and image publishing workflows"
git push
```

## What they do

| File | Trigger | Effect |
| --- | --- | --- |
| `ci.yml` | every push and PR | backend `pytest`; frontend unit tests + production build |
| `publish-image.yml` | pushes to `main`, `v*` tags, manual dispatch | builds `deploy/Dockerfile` and pushes to `ghcr.io/<owner>/quizbinf` |

## After the first image build

`publish-image.yml` needs no configured secret — it authenticates with the
built-in `GITHUB_TOKEN` and the `packages: write` permission.

One manual step is required before SciLifeLab Serve can pull the image:
**GHCR packages are private by default.** Go to the repository's *Packages* →
`quizbinf` → *Package settings* → *Change visibility* → **Public**. (Serve can
alternatively be given pull credentials, but public is simpler for an open
course app.)

Image tags produced:

- `latest` — only from the default branch
- `sha-<commit>` — immutable, **prefer this for deployments**
- `<branch-name>` — for test builds
- `1.2.3`, `1.2` — from `v1.2.3` tags

Deploy an immutable `sha-` or version tag on Serve rather than `latest`, so a
redeploy is reproducible and a restart cannot silently pick up new code.
