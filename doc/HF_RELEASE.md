# Hugging Face Hub + PyPI Release — Live

LiveChord-original ML assets are published. Status: **live as of 2026-05-09**.

Plan reference: [`hf-spaces-fuzzy-dream.md`](../../../.claude/plans/hf-spaces-fuzzy-dream.md) (in `~/.claude/plans/`).

## What's live

| Asset | URL | Size | Notes |
|---|---|---|---|
| HF Hub: beat-refiner | https://huggingface.co/livechord-music/livechord-beat-refiner | 12.5 MB safetensors + config.json | Bidirectional Transformer beat / downbeat / chord-boundary refiner |
| HF Hub: bar-arbitrator | https://huggingface.co/livechord-music/livechord-bar-arbitrator | 750 KB ONNX | Phase 0 rule + Phase 1 ONNX bar-grid post-processor |
| PyPI: beat-refiner | https://pypi.org/project/livechord-beat-refiner/0.1.0/ | sdist + wheel | `pip install livechord-beat-refiner` |
| PyPI: bar-arbitrator | https://pypi.org/project/livechord-bar-arbitrator/0.1.0/ | sdist + wheel | `pip install livechord-bar-arbitrator` |
| livechord.org footer | https://livechord.org (footer) | — | Backlink to `huggingface.co/livechord-music` |

End-to-end verified: PyPI install → `refine()` auto-downloads checkpoint from HF Hub → inference matches local-checkpoint output exactly (185→158 beats, 47→60 downbeats on a fixed test track).

## Staging dirs (kept for future updates)

| Package | Dir |
|---|---|
| beat-refiner | `c:\Users\hitea\hf-hub-staging\livechord-beat-refiner\` |
| bar-arbitrator | `c:\Users\hitea\hf-hub-staging\livechord-bar-arbitrator\` |
| Publish scripts | `c:\Users\hitea\hf-hub-staging\publish_to_hub.py` + `publish_to_pypi.py` |
| License decision doc | `c:\Users\hitea\hf-hub-staging\LICENSE-DECISION.md` |

The staging dirs are **not** in the LiveChord git tree — they're independent working copies that get pushed to HF Hub / PyPI as separate repos.

## License

Both packages release under **Apache 2.0** (code + weights, single license).

The LiveChord main repo stays **AGPL v3** unchanged. Rationale lives in [`LICENSE-DECISION.md`](../../../hf-hub-staging/LICENSE-DECISION.md): the moat is the full server + frontend + AI pipeline (still AGPL); inference-only release packages need permissive licensing to be useful in the HF Hub ecosystem.

## Metrics (cited on the HF model card)

beat-refiner held-out F1 (gold-quality subset, n=510, ±93 ms):
- beat F1 = **0.920**, downbeat F1 = **0.936**

Full holdout (n=7,389):
- beat F1 = 0.881, downbeat F1 = 0.914

Source: [doc/beat_refiner_metrics.md](beat_refiner_metrics.md). Reproduce: [scripts/eval_beat_refiner_holdout.py](../scripts/eval_beat_refiner_holdout.py).

## Why no HF Space

`livechord.org` already hosts an interactive demo (the 15 PD/CC tracks shipped 2026-05-06). HF Spaces would be a redundant demo with worse UX (no piano player / chord ribbon / AI accompaniment), plus ZeroGPU costs $9/mo for marginal incremental traffic vs the free model-card backlink play.

## Workflow for future updates

### When `data/models/beat_refiner.pt` is retrained

1. Re-run conversion → safetensors:
   ```bash
   cd c:/Users/hitea/hf-hub-staging/livechord-beat-refiner
   python scripts/convert_checkpoint.py \
       --in c:/Users/hitea/Claude/LiveChord/data/models/beat_refiner.pt \
       --out-dir ./
   ```
2. Bump `__version__` in [livechord_beat_refiner/__init__.py](../../../hf-hub-staging/livechord-beat-refiner/livechord_beat_refiner/__init__.py) AND `version` in [pyproject.toml](../../../hf-hub-staging/livechord-beat-refiner/pyproject.toml). PyPI does NOT allow re-uploading the same version — bump even for tiny changes.
3. Re-run [scripts/eval_beat_refiner_holdout.py](../scripts/eval_beat_refiner_holdout.py) on full holdout. Update `doc/beat_refiner_metrics.md` AND the `model-index` block in the staging `README.md` — they must match.
4. Smoke test: `python examples/example_usage.py <audio>` against a known-good track.
5. Re-publish:
   ```bash
   # HF Hub (no version bump needed; just overwrites latest)
   python c:/Users/hitea/hf-hub-staging/publish_to_hub.py --token-file <path>

   # PyPI (requires version bump per step 2)
   python c:/Users/hitea/hf-hub-staging/publish_to_pypi.py --token-file <path>
   ```
6. **Don't forget** to update [doc/beat_refiner_metrics.md](beat_refiner_metrics.md) in this repo too, so the LiveChord side stays in sync with what the Hub README reports.

### When `backend/ai/beat_refiner_*.py` source code changes

The staging copies are **NOT** auto-synced. Manual ritual:

1. `cp backend/ai/beat_refiner_*.py c:/Users/hitea/hf-hub-staging/livechord-beat-refiner/livechord_beat_refiner/<corresponding>.py`
2. If new `from backend.ai.<x> import ...` lines appeared, change them to `from .<x> import ...`
3. Smoke test from a clean shell: `python examples/example_usage.py <audio>`
4. Bump version (PyPI). Republish per workflow above.

A `sync-from-source.sh` automation is on the roadmap but not built yet — v0.1 release cadence is too rare to justify it.

### Token handling

Both publish scripts (`publish_to_hub.py` / `publish_to_pypi.py`) accept `--token-file <path>`, read the token, then **delete the file after a successful run**. The token never appears as a shell argument and is never written into source. To re-publish you have to drop a fresh token into the file path each time. This is intentional friction — write tokens that can mutate Hub repos shouldn't sit on disk between releases.

## Bidirectional backlink

- HF model cards → `livechord.org?utm_source=huggingface&utm_medium=model_card` (utm-tagged so GA can attribute incoming traffic)
- `livechord.org` footer → `huggingface.co/livechord-music` (so Google sees both ends of the link graph)

The footer link is in [frontend/index.html](../frontend/index.html) inside the `<footer class="site-footer">` block. Don't strip it during future footer cleanups — the SEO value is the whole point of the HF release.
