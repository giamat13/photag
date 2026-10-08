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
- **Numbering ("FEATURE.FIX.SMALL-FIX", decided with the user; from 11.0.0 — earlier it was "FEATURE.FIX" with a trailing 0).**
  A release with a new feature raises the first number and resets the others (`12.0.0`); a release with only fixes
  raises the second and resets the third (`11.1.0`); a release with only small fixes raises the third (`11.1.1`);
  pre-releases add `-beta.N`. **Always write all three parts** (never `9.1`): programs
  installed before the change only recognise a code update `photag-code-X.Y.Z-rtN.zip` with three parts, and
  without it they fall back to the full installer (which Smart App Control may block). `9.1` and `9.1.0`
  are read as the same version (`updater.parse_version`), but only the three-part form is ever written. The tag is
  `v<version>` and the notes file is `docs/release-notes-v<version>.md`, both exactly as written in
  `app/version.py`.
- **Structure of the release notes (decided with the user, from 12.1.0):** a title line (`# photag X.Y.Z`) and then only these sections,
  in this order, leaving out the ones with nothing in them: `## Features` (things that really change something for the user, first),
  `## Fixes` (bugs a user could notice) and `## Small fixes` (minor things, wording, looks, internals). It matches the version number:
  a feature release has `## Features`, a fixes-only release has `## Fixes`, a small-fixes release only `## Small fixes`. The in-app
  "What's new" dialog shows the three headings in the user's language and, when several versions are skipped, merges them by section
  (`updater.merge_notes`). `tools/test_release_notes.py` checks the file of the current version.
- Each release has a `docs/release-notes-v<version>.md` file; its contents become the GitHub Release
  body (and what the in-app "What's new" dialog shows, fetched live from the GitHub release).
- CI (`.github/workflows/tests.yml`) runs automatically on every push to `main` that changes more than text (docs / READMEs are
  skipped), with the Windows tests split into parallel jobs (two halves + the browser tests). The release workflow ALSO runs the same
  tests (as a reusable workflow, in parallel with the builds) and its `publish` job needs them: nothing is published if they fail.
  So you can dispatch the release right after pushing, without waiting for the push's own test run — but never release on top of a
  run you already know is red.
- Releasing is **not** done by pushing a git tag (`git push origin refs/tags/vX.Y.Z` reliably fails
  in this sandbox with a disconnect). Instead, trigger `.github/workflows/release.yml` by hand via
  `workflow_dispatch` (e.g. `mcp__github__actions_run_trigger` with `method: run_workflow`,
  `workflow_id: release.yml`, `ref: main`). The workflow itself reads `app/version.py`, creates the
  tag (`vX.Y.Z`), builds the installer (`photagSetup.exe`), the portable ZIP, and the code-update
  ZIP, and publishes them to a GitHub Release using the matching release-notes file.
- If a feature commit lands on `main` *after* a version has already been released, bump the version
  again (e.g. 1.6.0 → 1.6.1) and release separately — don't fold unreleased commits into a release
  that's already been published.
