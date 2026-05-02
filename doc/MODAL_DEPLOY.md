# Modal Serverless GPU — Deployment Runbook

LiveChord's BTC chord detection runs PyTorch on a GPU. The NUC has a local GPU, so personal/beta deployments don't need anything else. **For the VPS deployment** (Phase G — `LIVECHORD_MODE=public` on a CPU-only Hetzner / Contabo box), BTC inference is offloaded to [Modal](https://modal.com)'s serverless GPU pool.

This doc is the operator runbook: register → push the model → deploy → flip the env flag → verify.

## Cost expectations

- T4 GPU on Modal billed by the second. LiveChord does ~10-15 s of BTC inference per analyzed song.
- `min_containers=1` keeps one warm container so the first uploader of the day doesn't pay the 15-20 s cold start. Modal charges this idle time at a discount (about $1-3/month for T4).
- Variable cost: **0.000164 USD/GPU-second** on T4 → analyzing 100 songs/day ≈ **$5/month**. Free tier gives $30/month credit which covers ~600 analyses on its own.
- Total expected: **$3-8/month** for the LiveChord traffic profile we've sized for. See `frontend/sponsor.html` "Where the money goes" for the user-facing version.

## One-time setup (do this on the host that will deploy — your PC is fine)

### 1. Install Modal

```bash
pip install modal
```

### 2. Authenticate

```bash
modal token new
```

Opens a browser, signs you in to your Modal account, writes `~/.modal.toml` with the API token. Free tier signup is at https://modal.com if you don't have one yet.

### 3. Create the model-weight volume

```bash
modal volume create livechord-btc-model
```

This is a persistent named volume that survives function deploys.

### 4. Upload the BTC model checkpoint

```bash
modal volume put livechord-btc-model backend/btc/btc_model_large_voca.pt /btc.pt
```

Path conventions:
- **Local source**: `backend/btc/btc_model_large_voca.pt` (the file `chord_detect.py:_load_model` already loads on the NUC)
- **Volume destination**: `/btc.pt` (mounted as `/btc_model/btc.pt` inside the deployed function — `modal_btc.py` symlinks it back to where chord_detect expects)

Verify:
```bash
modal volume ls livechord-btc-model
# expected: btc.pt   <size>   <date>
```

### 5. Deploy the function

```bash
modal deploy backend/modal_btc.py
```

First deploy takes 2-3 minutes (Modal builds the image: torch + librosa + numpy + numba + soundfile + ffmpeg). Subsequent deploys reuse cached layers and finish in ~30 s.

After deploy, the function is reachable as `livechord-btc/detect_chords_and_key_modal`. The dashboard at https://modal.com/apps shows the deploy status.

### 6. Flip the dispatcher env flag on the host that will *call* Modal (the VPS)

```bash
# .env on the VPS
LIVECHORD_USE_MODAL_BTC=1
```

`backend/chord_detect.py:detect_chords_and_key_isolated` checks this on every call. When set, it routes BTC to Modal instead of running locally. On Modal failure it falls back to local (so the site doesn't break if Modal's down — but the VPS has no GPU, so the local fallback would CPU-trudge through inference at ~5× slower; consider it emergency-only).

## Verification

Smoke test from the VPS shell:

```bash
cd /srv/livechord
python -c "
import os
os.environ['LIVECHORD_USE_MODAL_BTC'] = '1'
import sys; sys.path.insert(0, 'backend')
from chord_detect import detect_chords_and_key_isolated
chords, key = detect_chords_and_key_isolated('/path/to/test.mp3')
print(f'Got {len(chords)} chords, key={key}')
"
```

Expected: ~10-15 s wall clock (warm container), prints chord count and key. Modal dashboard's "Function calls" panel should tick up by 1.

If the call hangs ~25-30 s the first time, that's the cold-container start. `min_containers=1` should prevent this from being the steady state.

## Day-2 operations

### Updating the model weight

```bash
modal volume put livechord-btc-model backend/btc/btc_model_large_voca.pt /btc.pt --force
```

The next function call symlinks to the new file (no redeploy needed; the symlink check in `modal_btc.py` runs every cold start, but warm containers keep the existing symlink — if you need an immediate cutover, restart the deployed function via the Modal dashboard).

### Watching costs

Modal dashboard → Apps → livechord-btc → Compute usage. The "Cost" column ticks per second of GPU time. Set a budget alert in `Settings → Billing`.

### Rolling back

If a Modal deploy regresses chord quality, the easiest rollback is:
1. `git checkout <previous-commit> -- backend/modal_btc.py backend/chord_detect.py`
2. `modal deploy backend/modal_btc.py`

Or kill Modal entirely by setting `LIVECHORD_USE_MODAL_BTC=0` on the VPS — falls back to local CPU inference. Not great, but works.

### Tearing down

If you migrate off Modal entirely (e.g. moved to a GPU VPS):

```bash
modal app stop livechord-btc
modal volume delete livechord-btc-model
```

## Architecture in 30 seconds

```
VPS (CPU only)                          Modal cloud
┌────────────────────────┐              ┌──────────────────────────────┐
│ FastAPI uvicorn        │              │ livechord-btc app            │
│   ↓                    │  audio bytes │   T4 container               │
│ chord_detect           │ ───────────▶ │   loads model from volume    │
│   .detect_chords_and_  │   over Modal │   runs BTC inference         │
│    key_isolated()      │   SDK        │   returns chords + key       │
│   ↓                    │ ◀─────────── │                              │
│ modal_btc.detect_via_  │  result dict │  min_containers=1 (warm)     │
│  modal()               │              │  scaledown_window=60s        │
└────────────────────────┘              └──────────────────────────────┘
```

The Modal function imports the same `chord_detect.detect_chords_and_key` we run locally — so chord output is bit-identical between local and Modal paths. Only the GPU transport changes.
