# LiveChord

> Turn an audio file into a real-time, playable chord chart.
> Practice piano / guitar / ukulele / accordion with synced chord display, transpose, A-B loop, and slow-down playback — all in your browser.

🌐 **Live demo:** [livechord.org](https://livechord.org)
📚 **Docs:** [doc/](doc/) · architecture overview in [CLAUDE.md](CLAUDE.md)
🏷️ **License:** [AGPL-3.0](LICENSE)

---

## What it does

You upload an audio file (MP3 / FLAC / WAV / M4A / OGG, up to 200 MB).
LiveChord runs:

- a **chord-recognition transformer** (BTC) for the chord progression,
- a **beat-tracking model** (beat_this on GPU, librosa fallback) for bar lines and tempo,
- a **section detector** for verses / choruses / bridges,
- optional **bar arbitration** + **beat refinement** post-processors that clean up bar phase + downbeat alignment.

The result drives an interactive player: chord cards in time with the audio, with five instrument views (piano keyboard + waterfall, guitar / ukulele fretboards, accordion bass+chord buttons, arranger-style backing). Transpose to any key, slow playback to 0.5×, loop a single section A-B, and switch instrument view at any time.

LiveChord is a **general-purpose audio analysis tool**. It does not provide, host, or distribute copyrighted audio content — there's no music download, no streaming, no media library shipped with the project. The service only **analyzes** audio that the user provides and has the right to use.

## Status

LiveChord started as a personal project (NAS + home GPU server), ran an invite-only beta from 2026-04-16 to 2026-04-26, and has been **publicly launched** at [livechord.org](https://livechord.org) since 2026-05-03. The beta phase is over — anyone can sign up and use the public instance today, no invite code required. Features are stable for everyday practice; AI quality is actively being improved (see [doc/SCALING.md](doc/SCALING.md) and the "AI Quality Pipeline" section in [CLAUDE.md](CLAUDE.md)).

The public instance is **upload-only**: drop an MP3 / WAV / FLAC / M4A / OGG (≤ 100 MB through the Cloudflare edge) and LiveChord analyses it. YouTube URL ingest was removed from the codebase on 2026-05-04 on hobby-scale legality grounds; CI guards against it being re-introduced. Bring-your-own audio is the only supported source path.

First-time visitors don't have to upload anything to see the player working — the homepage ships **15 pre-analyzed Public-Domain / CC sample tracks** organized into Easy / Folk / Jazz / Classical sub-rows (Canon in D, Für Elise, Greensleeves, Twinkle Twinkle, …). Click a card and the chord ribbon, beats, downbeats, and melody waterfall all appear instantly. Built via [scripts/build_demo.py](scripts/build_demo.py); audio + chord JSONs ship in the repo under `data/demo/`.

This is a **one-person hobby project** — the author maintains it because they use it daily themselves. Pull requests welcome but bear in mind: low-friction issues that improve the hobby experience get attention faster than ambitious refactors.

## Quick start (local development)

```bash
# 1. Clone
git clone https://github.com/JJ110112/LiveChord.git
cd LiveChord

# 2. Python deps (one-time)
python -m venv .venv
.venv/Scripts/activate           # Windows
# source .venv/bin/activate      # macOS / Linux
pip install -r backend/requirements.txt

# 3. Copy env template and fill in any OAuth keys you want (optional)
cp .env.example .env

# 4. Run
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8800 --reload

# 5. Open http://127.0.0.1:8800
```

The default `LIVECHORD_MODE=personal` runs a single-user mode with no auth and no upload quota. Set `LIVECHORD_MODE=public` to mirror the livechord.org behaviour (anonymous + OAuth, daily quota, hidden NAS browse).

## Architecture (one-screen overview)

```
┌────────────────────────────────────────┐
│ Browser (Vanilla JS + Canvas)          │
│  index.html / player.html / process... │
└────────────────────────────────────────┘
                 ↑↓ HTTP
┌────────────────────────────────────────┐
│ FastAPI (uvicorn) on port 8800         │
│  ├─ process_api  (upload + jobs)       │
│  ├─ chord_api    (BTC detection)       │
│  ├─ ai_api       (sections + melody)   │
│  ├─ feedback_api / auth_api / ...      │
│  └─ static: frontend/                  │
└────────────────────────────────────────┘
                 ↓
┌────────────────────────────────────────┐
│ Workers (queue.Queue + daemon thread)  │
│  ├─ process_queue   chord pipeline     │
│  ├─ beat_upgrade_q  on-demand beats    │
│  └─ auto_worker     library batch      │
└────────────────────────────────────────┘
                 ↓
┌────────────────────────────────────────┐
│ Models                                 │
│  ├─ BTC chord transformer (PyTorch)    │
│  ├─ beat_this beat tracker (Modal GPU) │
│  ├─ section_detect (DL + rule-based)   │
│  ├─ bar_arbitrator + beat_refiner      │
│  └─ chord2vec, reharmonizer (Jazzify)  │
└────────────────────────────────────────┘
```

Notable design rules:

- **Long-running jobs are loosely coupled from the client** — the upload endpoint enqueues + returns a job ID in <100 ms; a daemon worker processes it; the client polls `/api/process/status/<id>` every few seconds. No long-poll, no WebSocket. See [CLAUDE.md "Coding Rules"](CLAUDE.md) for the full pattern.
- **No `async def` for file I/O endpoints** — plain `def` so FastAPI dispatches to the thread pool. Prevents the event loop from being blocked by chord JSON disk writes.
- **GIL-bound work runs in subprocesses** — BTC chord detection runs in a `ProcessPoolExecutor`; pure-Python loops on daemon threads still hold the GIL and starve request threads, so `chord2vec` retrain spawns a fresh interpreter via `sys.executable`.

## Repository layout

```
backend/                Python (FastAPI app)
  main.py               app + route mounts
  process_api.py        /api/process/upload + /status + /result
  process_queue.py      queue + worker for new uploads
  beat_upgrade_queue.py on-demand beat re-detection
  chord_detect.py       BTC chord recognition
  chord_api.py          read/write/rate chord JSONs
  ai/                   chord2vec, section_detect, bar_arbitrator,
                        beat_refiner, reharmonizer, neural_arranger
  modal_btc.py          BTC dispatch to Modal serverless GPU (optional)
  modal_beat_this.py    beat_this dispatch to Modal (optional)
  ...
frontend/               Vanilla JS / HTML / CSS (no build step)
  index.html            homepage
  player.html           the practice player (chord ribbon + instruments)
  process.html          standalone upload page
  js/player.js          ~7,000 lines of player logic (the heart of the UX)
  js/app.js             homepage logic
  css/                  base / home / player styles
data/                   runtime data (chord JSONs, models, audit DB).
                        Path is environment-dependent — see CLAUDE.md.
deploy/                 systemd unit, cloudflared config (VPS deploy)
doc/                    architecture, QA protocol, scaling roadmap, etc.
scripts/                training-corpus + bulk-migration scripts
```

## Documentation map

If you only read one file: [CLAUDE.md](CLAUDE.md) — the project's working notes, kept fresh because the author uses Claude Code for development. It documents conventions, environment quirks, intentional gotchas, and the "why" behind several non-obvious design choices.

| Doc | What it covers |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Single-source onboarding, current state, coding rules, AI quality pipeline status |
| [doc/QA.md](doc/QA.md) | QA protocol, test matrix, UI architecture rules |
| [doc/UX_CONVENTION.md](doc/UX_CONVENTION.md) | UX patterns (popups, toolbars, modals) — mandatory for UI changes |
| [doc/QA_BATTLE_STORY.md](doc/QA_BATTLE_STORY.md) | Past incidents and the lessons baked into the codebase |
| [doc/OPS.md](doc/OPS.md) | VPS operations runbook |
| [doc/SEO.md](doc/SEO.md) | Search visibility plan for livechord.org |
| [doc/SCALING.md](doc/SCALING.md) | Roadmap for scaling beyond a single GPU |

## Models + research credits

LiveChord stitches together work from several open-source projects. None of these are vendored — they're fetched at install time:

- **BTC** (Bi-directional Transformer for Chord recognition) — chord-detection backbone
- **beat_this** (CPJKU) — beat + downbeat tracker (preferred), with **madmom** as the rubato-aware fallback and **librosa** as the always-available baseline
- **basic-pitch** (Spotify) — melody pitch tracking
- **mir_eval / pretty_midi** — evaluation + MIDI utilities
- **PyTorch + ONNX Runtime** — model inference
- **FastAPI + uvicorn** — web framework
- **OAuth via Authlib** (Google + Discord) — sign-in, no password storage

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version:

- Open an issue first for anything bigger than a typo fix — the project is opinionated and a 5-minute conversation can save a 5-hour PR
- Style: Python = match the surrounding code (no formatter enforced); JS = no build step, vanilla, no framework
- The `data/` folder + the audit SQLite DBs are state, not source — never commit them

## License

LiveChord is licensed under the [GNU Affero General Public License v3.0](LICENSE).

In plain language: you can use, modify, and redistribute LiveChord freely, but if you run a modified version as a network service (e.g. host a fork at `your-livechord-fork.com`), you must make your modified source code available to users of that service. This matches the project's spirit — "free for everyone, including the people who fork it."

If AGPL doesn't fit your use case (e.g. proprietary commercial integration), email the author at livechordcookie@gmail.com to discuss.

## Acknowledgments

Built in 2026 by one person who wanted a chord chart that scrolls in time with the music, then kept extending it because piano practice is more fun when the visuals are right.

LiveChord is a personal, non-profit, educational project. There are no ads, no paywall, no paid tier, and no sponsorship — it exists to help people learn music, not to make money. If you find it useful, the best thank-you is to share it with another learner.

Thanks to everyone whose models and libraries are listed above — none of this would be possible without your work being open in the first place.
