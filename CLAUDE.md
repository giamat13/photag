# Working on photag

## Task tracking
Keep the TODO list (TaskCreate / TaskUpdate) current for your own work, not just the user's
original requests. Add a task when you start something, mark it `in_progress` when you begin it,
`completed` the moment it's actually done (code pushed and CI verified, not just written) — update
it continuously as you go, not only at the end of a session or when asked.

## Ideas from other projects
You may take **ideas** (features, UX) from **https://github.com/storytold/lightcraft** — the user approved it. Add the repo to the
session with `add_repo` (owner `storytold`, repo `lightcraft`) to read it. It is written in another programming language, so its
code cannot be copied or ported line by line: read it for what it does and how it feels, then build the same thing the photag way
(Python + the web UI, every string in all 16 locales, tests, release notes).

## Wording about Adobe / Lightroom
Never describe photag as "Lightroom-style", "in the spirit of Lightroom" or "a faithful take on Lightroom", in the README, the site,
the app, the Store listing or code comments, and keep "Lightroom" out of the Store name, description and keywords. It may appear only
as a compatibility fact ("import from a Lightroom catalog (.lrcat)"), with the line "Lightroom is a trademark of Adobe; photag is not
affiliated with Adobe." (see docs/MICROSOFT_STORE.md).

## Git workflow
All development happens directly on `main` — no feature branches, no pull requests. Commit and
push straight to `main`.

## How versions and releases work
- **Never release a version (not even a pre-release) before the change is known to work 100%.** Check it for real first — on the
  user's own machine when it can be done (run the program from source / F5, call the real endpoints, drive the real dialog headless),
  not only with the test suite and fakes. Pushes to `main` are fine at any time; releases are not. A regular release only after
  everything works. **When all the tests pass there is no reason not to make a regular release** (decided with the user) -- a pre-release
  is only for a huge update, or for something the tests may not cover that has to be checked for real. Only when there is truly no other
  way to check it (it can only be seen in a built, installed program) release a
  **pre-release** (`workflow_dispatch` with `prerelease=true`; the version in `app/version.py` stays plain, e.g. `18.0.0` -- the tests assume the running version is not a `-beta`; delete the pre-release and its tag before the regular release of the same version) and say clearly that it is a test build; the user asks
  before a regular release follows. A release that turns out bad is deleted (`gh release delete vX --cleanup-tag -y`) and made again.
- `app/version.py` (`__version__`) is the single source of truth for the app's version. Bump it
  before every release.
- **Numbering ("FEATURE.SMALL-FEATURE.FIX", decided with the user; from 18.1.0 — from 11.0.0 to 18.0.0 it was "FEATURE.FIX.SMALL-FIX", earlier "FEATURE.FIX" with a trailing 0).**
  A release with a new feature raises the first number and resets the others (`12.0.0`); a release with only small features
  raises the second and resets the third (`12.1.0`); a release with only fixes raises the third (`12.1.1`).
  A "feature" is an ordinary feature, not a huge one, and **several small features released together count as one feature**
  (decided with the user: three small additions in one release are `19.0.0`, not `18.4.0`);
  pre-releases add `-beta.N`. **Always write all three parts** (never `9.1`): programs
  installed before the change only recognise a code update `photag-code-X.Y.Z-rtN.zip` with three parts, and
  without it they fall back to the full installer (which Smart App Control may block). `9.1` and `9.1.0`
  are read as the same version (`updater.parse_version`), but only the three-part form is ever written. The tag is
  `v<version>` and the notes file is `docs/release-notes-v<version>.md`, both exactly as written in
  `app/version.py`.
- **Structure of the release notes (decided with the user, from 12.1.0):** a title line (`# photag X.Y.Z`) and then only these sections,
  in this order, leaving out the ones with nothing in them: `## Features` (things that really change something for the user, first),
  `## Small features` (minor additions, wording, looks, internals) and `## Fixes` (bugs a user could notice). It matches the version number:
  a feature release has `## Features` (several small features together are listed there), a small-features release has `## Small features`, a fixes-only release only `## Fixes`. (Releases up to 18.0.0 used `## Small fixes`; the updater still reads it.) The in-app
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
  ZIP, and publishes them to a GitHub Release using the matching release-notes file. Since 14.0.0 it also builds an
  **MSI** (`tools/make_msi.py`, WiX 5, per user, into `%LOCALAPPDATA%\Programs\photag-msi`); that step may fail without
  stopping the release (the release then has no .msi). To check the MSI without releasing, run the `msi-check` workflow by hand
  (it builds, silently installs, checks and uninstalls it).
- If a feature commit lands on `main` *after* a version has already been released, bump the version
  again (e.g. 1.6.0 → 1.6.1) and release separately — don't fold unreleased commits into a release
  that's already been published.

## Problem reports
"Help > Report a problem…" (app/report.py) creates an issue through a bot account whose token is the repository secret `REPORT_TOKEN`; the
release workflow writes it into `app/_report_token.py` (never committed). Setup and limits: docs/BUG_REPORTS.md. Do not put a token in the code.
