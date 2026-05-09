# Hugging Face Hub Release — Status & Runbook

Two LiveChord-original ML assets are staged for publication on Hugging Face Hub. This doc captures the staging location, what's done, and the manual steps remaining to actually push to Hub.

Plan reference: [`hf-spaces-fuzzy-dream.md`](../../../.claude/plans/hf-spaces-fuzzy-dream.md) (in `~/.claude/plans/`).

## What's published-ready

### `livechord-beat-refiner` — bidirectional Transformer beat / downbeat / chord-boundary refiner

| | |
|---|---|
| Staging dir | `c:\Users\hitea\hf-hub-staging\livechord-beat-refiner\` |
| Model file | `hub_release/model.safetensors` (12.5 MB) |
| Config | `hub_release/config.json` |
| Source | `livechord_beat_refiner/{__init__,features,model,infer}.py` (1135 LOC, zero `backend.*` deps) |
| License | Apache 2.0 (see `LICENSE-DECISION.md` in staging root) |
| Smoke test | ✅ `python examples/example_usage.py <audio>` produces refined beats locally |
| Metrics | beat F1 = 0.920 (gold), 0.881 (full) — see [doc/beat_refiner_metrics.md](beat_refiner_metrics.md) |

### `livechord-bar-arbitrator` — Phase 0 rule + Phase 1 ONNX bar / downbeat post-processor

| | |
|---|---|
| Staging dir | `c:\Users\hitea\hf-hub-staging\livechord-bar-arbitrator\` |
| Model file | `hub_release/bar_arbitrator_v1.onnx` (750 KB) |
| Source | `livechord_bar_arbitrator/{__init__,arbitrator}.py` — inlined `parse_chord_name`, no `backend.*` deps |
| License | Apache 2.0 |
| Smoke test | ✅ Loaded a real chord JSON (136 chords, 134 downbeats), ONNX inference detected 3/4 time correctly + emitted 140 corrected downbeats |

## License decision

Both packages release under **Apache 2.0** (code + weights, single license). Rationale in `c:\Users\hitea\hf-hub-staging\LICENSE-DECISION.md` — short version: the LiveChord main repo stays AGPL v3 (the moat), but inference-only release packages need permissive licensing to be useful in HF Hub's ecosystem.

## Why no Space — recap

`livechord.org` already hosts an interactive demo (the 15 PD/CC tracks shipped 2026-05-06). HF Spaces would be a redundant demo with worse UX (no piano player, no chord ribbon, no AI accompaniment), plus it costs algoritmic compute (ZeroGPU = HF Pro $9/mo) for marginal traffic vs the free model-card backlink play.

## Manual steps to actually go live

These require user-side decisions (HF account, name) and aren't auto-runnable:

1. **HF org name** — chose `livechord-music` (the username `livechord` was taken). Repo URLs are `huggingface.co/livechord-music/livechord-beat-refiner` and `huggingface.co/livechord-music/livechord-bar-arbitrator`. The `_HUB_REPO_ID` constants in both packages reflect this.
2. **`huggingface-cli login`** — paste a write token from huggingface.co/settings/tokens.
3. **Create the two model repos**:
   ```bash
   huggingface-cli repo create livechord-beat-refiner --type model
   huggingface-cli repo create livechord-bar-arbitrator --type model
   ```
4. **Push from the staging dirs** (each becomes a separate HF Hub git repo):
   ```bash
   cd c:/Users/hitea/hf-hub-staging/livechord-beat-refiner
   git init && git lfs install && git lfs track "*.safetensors"
   # Add files (everything in the staging dir EXCEPT .gitignore patterns + scripts/)
   # Move hub_release/{model.safetensors,config.json} to repo root before push.
   git add .gitattributes README.md LICENSE pyproject.toml requirements.txt \
           livechord_beat_refiner/ examples/ model.safetensors config.json
   git commit -m "Initial release v0.1.0"
   git remote add origin https://huggingface.co/livechord-music/livechord-beat-refiner
   git push origin main
   ```
   Same flow for `livechord-bar-arbitrator` (use `git lfs track "*.onnx"`).
5. **Publish to PyPI** (optional, for `pip install livechord-beat-refiner` to work outside `pip install git+...`):
   ```bash
   cd c:/Users/hitea/hf-hub-staging/livechord-beat-refiner
   pip install build twine
   python -m build  # creates dist/
   twine upload dist/*  # needs a PyPI account
   ```
6. **Post-publish**:
   - Add a low-key `⚡ open models on Hugging Face` link in [`frontend/index.html`](../../frontend/index.html) footer pointing at the Hub repos (bidirectional backlink — Google sees both ends).
   - Verify GA `utm_source=huggingface` traffic shows up after ~48h.
   - Check `site:huggingface.co livechord.org` in Google after a week.

## Workflow for future updates

When `data/models/beat_refiner.pt` or `bar_arbitrator_v1.{pt,onnx}` get retrained:

1. Re-run the conversion script:
   ```bash
   cd c:/Users/hitea/hf-hub-staging/livechord-beat-refiner
   python scripts/convert_checkpoint.py \
       --in c:/Users/hitea/Claude/LiveChord/data/models/beat_refiner.pt \
       --out-dir ./hub_release/
   ```
2. Bump `__version__` in `livechord_beat_refiner/__init__.py` AND in `pyproject.toml`.
3. Re-run `python examples/example_usage.py <audio>` smoke test before push.
4. Re-run [`scripts/eval_beat_refiner_holdout.py`](../scripts/eval_beat_refiner_holdout.py) on full holdout, update `doc/beat_refiner_metrics.md` numbers AND the `model-index` block in `hub_release/README.md` (pre-pushed) AND `livechord-beat-refiner/README.md` (staging) — they should match.
5. `git commit && git push` to the HF Hub remote.

When the LiveChord source files change in `backend/ai/beat_refiner_*.py`:

1. The staging copies are **NOT** auto-synced. Re-run the copy + import-rewrite ritual:
   - `cp backend/ai/beat_refiner_*.py c:/Users/hitea/hf-hub-staging/livechord-beat-refiner/livechord_beat_refiner/<...>.py`
   - Change `from backend.ai.beat_refiner_features import ...` → `from .features import ...` in any new files.
   - Re-run the smoke test.

A full "auto-sync staging from source" script is on the roadmap but not yet built — manual is fine for the v0.1 release cadence.
