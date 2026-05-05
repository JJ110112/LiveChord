# Contributing to LiveChord

Thanks for being interested! LiveChord is a one-person hobby project, so contribution overhead is kept deliberately light.

## Before opening a PR

**For typo fixes, comment cleanups, doc tweaks** — go straight to a PR, no discussion needed.

**For anything else** — please open an issue first describing what you want to do and why. The project has a lot of intentional design choices that aren't obvious from the code (most are documented in [CLAUDE.md](CLAUDE.md) and [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md)). A 5-minute conversation in an issue can save you a weekend on a PR that won't merge.

## What we welcome

- **Bug fixes** with reproduction steps
- **Performance improvements** with before/after measurements
- **New instrument views** (mandolin? banjo? Rhodes?) following the existing instrument-module pattern in `frontend/js/`
- **AI quality improvements** — better chord detection, better beat tracking, better section detection. Open an issue + share the corpus you're testing against
- **Internationalization** — new languages slotted into `frontend/i18n/<lang>.json` matching the existing key structure
- **Documentation improvements** — both code comments and `doc/` material
- **Accessibility** improvements — colour contrast, keyboard navigation, screen-reader labels

## What we don't want

- **Re-introducing YouTube extraction.** Removed deliberately when LiveChord went open-source under AGPL — see commits prefixed `chore(yt-removal stage N/4)`. The author would rather LiveChord stay strictly upload-only than fight YouTube's anti-scraping defenses or skirt their ToS. PRs that re-enable yt-dlp or proxy YouTube will be closed without merge.
- **Tracking, analytics SDKs, advertising integrations** — privacy stays minimal.
- **Framework migrations.** The frontend is intentionally vanilla JS with no build step. Don't propose a React/Vue/Svelte rewrite — it would shrink the contributor pool to people comfortable with that framework.
- **Code style "modernization" PRs** that shuffle existing code without improving behaviour.
- **Breaking changes to chord JSON / database schemas** without a migration path. The project still serves data written by the original beta deployment in 2026-04.

## Code style

### Python

- Match the surrounding code. No formatter is enforced; just don't fight existing patterns.
- 4-space indent, double-quoted strings is fine if that's what the file uses.
- New endpoints follow the "long-running jobs are loosely coupled from the client" pattern documented in [CLAUDE.md](CLAUDE.md) — synchronous pre-flight validation, then enqueue + return a job ID, then daemon worker, then status polling.
- File I/O endpoints use plain `def` (not `async def`) so FastAPI dispatches them to the thread pool.
- Subprocess calls that read user-facing text must specify `encoding="utf-8", errors="replace"` (Windows cp950 default drops CJK).

### JavaScript

- Vanilla, no framework. No build step.
- Stay inside the existing IIFE in `player.js` for player code.
- Bump the `?v=N` cache buster on `<script>` / `<link>` tags whenever you edit the corresponding `.js` / `.css`. Otherwise users see stale code from browser cache.

### CSS

- Follow the existing `base.css` / `home.css` / `player.css` split.
- New components: append to the matching file, don't create a new stylesheet unless it's a new page.

### i18n

- New user-facing strings go in `frontend/i18n/en.json` (the source of truth) AND `frontend/i18n/zh-TW.json` (the only other supported language). Match the existing dotted-key namespace conventions (`home.*`, `player.*`, `common.*`, etc).
- When you change a translation, bump `DICT_VERSION` in `frontend/js/i18n.js` AND the `i18n.js?v=N` cache buster across HTML files.

## Tests

- The repo has a `backend/tests/` directory. New backend changes that touch chord-detection / beat-detection / section-detection should ideally include a test against a small public corpus (any of the GTZAN / RWC / Isophonics excerpts are fair game).
- Frontend changes are validated by manual Playwright runs (see [doc/QA.md](doc/QA.md)). PRs touching player UX should include a brief description of the test path you walked through.

## Reporting bugs

Open a GitHub issue with:

- Browser + OS (e.g. "Chrome 138 on Windows 11")
- The exact URL you were on
- What you did, what you expected, what happened
- A screenshot if it's a UI bug
- For analysis errors: the song name + audio source if you can share it

For sensitive reports (security issues, data leaks): email hiteacherwu@gmail.com directly. Don't open a public issue.

## Code of conduct

Be excellent to each other. Pull requests, code reviews, and issues should focus on the code and the work, not the person. Personal attacks, harassment, or sustained rudeness are grounds for being blocked from the repo.

## License + sign-off

By submitting a contribution you agree that it's licensed under the same AGPL-3.0 license as the rest of the project (see [LICENSE](LICENSE)). No CLA needed.

If you'd like to be listed in the README acknowledgments after your PR merges, mention it in the PR description.

Thanks again — happy hacking.
