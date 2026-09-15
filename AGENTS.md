# Working rules for this repo

## Version number (mandatory)

- The app version lives in **`VERSION`** at the repo root (plain text, `MAJOR.MINOR.PATCH`).
- The sidebar renders it at the bottom (`v0.7.0 · <short commit>`), so anyone can
  check that the deployed instance (Streamlit Community Cloud) is running the
  latest push.
- **Bump it on every change** that reaches `main`: patch (`0.7.0 → 0.7.1`) for
  fixes and tweaks, minor (`0.7.x → 0.8.0`) for new user-facing features or
  scans/universes. Include the bumped `VERSION` file in the same commit.
- Methodology spec versions in `docs/specs/OPEN_QUESTIONS.md` (the `v0.5.x` line)
  are a separate, frozen numbering — do not mix the two.

## Conventions that already exist

- Scanner math (`_highlighted`, gates, scoring) changes only via an approved
  brief; UI-only changes never touch it. Options data never ranks or hides names.
- Tests must stay green (`python -m pytest`) before any push. The suite freezes
  highlight membership and refresh-scope behavior.
- Scan artifacts live in git under `data/scans/`; raw caches do not. The nightly
  cron (`tools/nightly_push.sh`) scans locally (SEC blocks datacenter IPs) and
  pushes — Streamlit Cloud redeploys on the push.
- A second agent may be working in this tree: commit only your own files when
  the tree is dirty, and sweep-commit finished batches after the tests pass.
