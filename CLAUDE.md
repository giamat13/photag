# Working on photag

## Task tracking
Keep the TODO list (TaskCreate / TaskUpdate) current for your own work, not just the user's
original requests. Add a task when you start something, mark it `in_progress` when you begin it,
`completed` the moment it's actually done (code pushed and CI verified, not just written) — update
it continuously as you go, not only at the end of a session or when asked.

## Git workflow
All development happens directly on `main` — no feature branches, no pull requests. Commit and
push straight to `main`.

## How versions and releases work
- `app/version.py` (`__version__`) is the single source of truth for the app's version. Bump it
  before every release.
- Each release has a `docs/release-notes-vX.Y.Z.md` file; its contents become the GitHub Release
  body (and what the in-app "What's new" dialog shows, fetched live from the GitHub release).
- CI (`.github/workflows/tests.yml`) runs automatically on every push to `main` (Windows runner:
  server/import/backup/update/library tests, then UI smoke tests). Always check it went green on
  the commit you're about to release — don't release on top of a red run.
- Releasing is **not** done by pushing a git tag (`git push origin refs/tags/vX.Y.Z` reliably fails
  in this sandbox with a disconnect). Instead, trigger `.github/workflows/release.yml` by hand via
  `workflow_dispatch` (e.g. `mcp__github__actions_run_trigger` with `method: run_workflow`,
  `workflow_id: release.yml`, `ref: main`). The workflow itself reads `app/version.py`, creates the
  tag (`vX.Y.Z`), builds the installer (`photagSetup.exe`), the portable ZIP, and the code-update
  ZIP, and publishes them to a GitHub Release using the matching release-notes file.
- If a feature commit lands on `main` *after* a version has already been released, bump the version
  again (e.g. 1.6.0 → 1.6.1) and release separately — don't fold unreleased commits into a release
  that's already been published.
