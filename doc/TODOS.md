# LiveChord — Open TODOs (non-urgent / blocked on upstream work)

Project-wide TODO list for items that are diagnosed but not actionable right now,
usually because they're blocked on a larger initiative (Phase 4, MIDI catalog,
model retrain). Items listed here should have **diagnosis + blocker + expected
resolution path**, not just a vague wish.

Last updated: 2026-05-16

---

## Quality Tracks (active focus, post-beta)

Beta wound down 2026-04-26. Going forward the project's main engineering bandwidth is on the upstream signal that all later layers (player, accompaniment, jazzify, section/phrase tools) feed on. Three tracks, in priority order. Each owns a slice of the AI Quality Pipeline summary in [CLAUDE.md](../CLAUDE.md#ai-quality-pipeline-current-focus).

### Track 1 — Beat stability

**Goal**: percentage of chord-JSONs where the user's 人工校對 queue confirms `downbeats[]` matches musical bars without a manual fix → keep climbing.

**Owners**: `beat_refiner` ([backend/ai/beat_refiner_*.py](../backend/ai/)) + `bar_arbitrator` ([backend/ai/bar_arbitrator.py](../backend/ai/bar_arbitrator.py)).

**Open work**:
- Quantify post-backfill v2 lift on the existing 13k-song corpus (no shipping eval harness yet — currently only smoke tests).
- Push `bar_arbitrator` Phase 1 model false-positive rate down. Conservative `_MIN_CANDIDATE_F1` gate is intentional but means many genuinely-broken songs fall through to the rule-based fallback.
- Edge genres still poor: slow ballads where the BPM ballad-halving heuristic fights the refiner, rubato, intros with long silence, songs with tempo-curve breaks (rallentando endings).
- `beat_refiner` chunked-overlap inference for >15 min songs (currently truncated at `MAX_FRAMES`).
- **Compound 6/8 + simple 3/4 meter classification** (shipped 2026-05-16, see [QA_BATTLE_STORY 番外篇 IX](QA_BATTLE_STORY.md)): serve-time sidecar fallback + secondary ratio-3 path + 3/4 detector. Open follow-ups:
  - Phase 1: propagate `beat_refiner` sigmoid confidences (`beat_logits` / `downbeat_logits`) into the bar-snap so high-confidence downbeats override the gate.
  - Phase 2: activate `beat_refiner`'s `chord_boundary_logits` head + train on feedback.db corrections.
  - Admin UI tool to surface degenerate-beat songs ([tools/backfill_degenerate_beats.py](../tools/backfill_degenerate_beats.py) is the CLI; UI wrap pending).
  - Backfill batch (PC-compute, madmom, ~93k JSONs) currently in progress 2026-05-16; expect ~10k+ degenerate songs upgraded to densely-sampled beats[]/downbeats[].

### Track 2 — Chord accuracy

**Goal**: raise BTC raw-output quality + reduce reliance on the post-correction layer. Beta-era ★ ratings in `feedback.db` are the labeled signal; the chord-quality LED already surfaces them in the player.

**Open work**:
- Pipe 人工校對 corrections back into a fine-tune corpus for BTC. Today corrections are stored under `data/human_feedback/` + applied at serve time; they don't yet feed model retrain.
- Decide whether to filter beta-user-analyzed YouTube MV chord JSONs out of the chord2vec retrain corpus (see [doc/SCALING.md](SCALING.md) §2 isolation decision tree). For now both library and beta uploads go in.
- Address the chord-quality LED's binary-ish ratings: 3-rating threshold for color flip means low-traffic songs never light up, even when one rater gave a strong signal.

### Track 3 — Phrase / section detection

**Goal**: section_detect DL path actually triggers on most library songs (today rule-based fallback covers ~82% because melody/bass densities are zero for unprocessed songs).

**Open work**:
- Expand hybrid extraction coverage so `melody_density + bass_density > 0` for more of the library — gates the DL classifier in [section_detect._classify_dl](../backend/ai/section_detect.py).
- Phase 4 MIDI catalog (see [doc/PHASE_4_HYBRID_MELODY.md](PHASE_4_HYBRID_MELODY.md)) gives clean melody ground-truth → feeds both Track 3 and Track 1's downstream metrics.
- A-B phrase picker UX is healthy; bottleneck is the upstream label quality, not the picker.

---

## Audio / Accompaniment

### AI 伴奏：同音同時重複 events（"ghost notes"）

**Symptom**: accompaniment JSONs contain pairs of events with same `time` (±1 ms)
and same `pitch` but different `finger`:
```json
{"time": 0.2515, "pitch": 37, "velocity": 30, "finger": 3}
{"time": 0.2516, "pitch": 37, "velocity": 30, "finger": 5}
```
Both get scheduled and played — same sample fires twice with ~1 ms offset,
adding ~6 dB. Contributes to LH feeling louder than intended.

**Diagnosed**: Likely melody-extraction bleed — when V1 pYIN extracts melody
over a polyphonic track, secondary voices (chord tones from the accompaniment
track, not the actual melody) get picked up and land as extra fingering hints.
The AI accompaniment generator emits one event per finger suggestion.

**Why not fix now**:
- Frontend dedup would hide the data but leave bad training signal in backend.
- Backend dedup in `accompaniment*.py` risks regressing fingering diversity
  (sometimes two valid fingerings genuinely deserve two rendered hints — e.g.
  thumb-under transitions).
- Root cause is upstream melody-track-isolation quality.

**Blocker**: Phase 4 MIDI reverse-ID + catalog completion.

**Expected resolution path**:
1. Once MIDI Catalog (Phase 4.1) has curated 10-50 clean MIDI sources, use
   those as the melody ground truth — no bleed from polyphonic tracks.
2. Reverse-ID (Phase 4 / Y:\ Chroma_Index) identifies which CDs have matching
   MIDIs. For those, melody comes from the MIDI's explicit `MELODY` track, not
   from pYIN extraction over audio.
3. Regenerate affected accompaniment JSONs after the new melody source is in
   place. Dedup will naturally disappear because finger-hint branching is
   phrase-aware rather than signal-noise-driven.

**Workaround shipped (2026-04-21)**: velocity-driven + LH pedagogical bias in
[player.js playNote()](../frontend/js/player.js) brings effective LH:RH ratio
from 3.0x to 1.6x despite the duplicate events. Good enough until root fix.

---

## Dev Environment — `data/tmp/` is backend-owned scratch, not user-writable

**Symptom**: Scripts and test files written under `c:\Users\hitea\Claude\LiveChord\data\tmp\` (or `V:\data\tmp\` on NUC) silently vanish within 10-15 minutes. `__pycache__/` (a directory, not a file) survives. Procmon at userspace VFS level captures nothing useful.

**Root cause** (FOUND 2026-04-21 22:30): Backend itself. [`backend/process_queue.py:796-805`](../backend/process_queue.py#L796-L805) runs a `tmp-cleanup` daemon thread that every **5 minutes** scans `data/tmp/` and `unlink()`s any **file** older than **10 minutes**:

```python
TMP_DIR = DATA_DIR / "tmp"
_CLEANUP_INTERVAL = 300   # 5 min
_MAX_TMP_AGE = 600        # 10 min

def _cleanup_loop():
    while True:
        time.sleep(300)
        for f in TMP_DIR.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > 600:
                f.unlink()
```

Purpose: clean up uploaded audio intermediates after process-worker finishes. Thread starts automatically when uvicorn loads `process_queue` (which is always, in any LiveChord backend run — personal 8800, beta 8801, local dev 8802).

**Why this masquerades as mystery**:
- `__pycache__` survives because `.is_file()` returns False on directories
- Procmon does see the `unlink()` but on the Claude Code / bash session we were filtering against "SetDispositionInformationFile" — Python's `os.unlink` translates differently
- Storage Sense was a red herring; we disabled it but the reaper was still a running backend thread

**Bake test proof (2026-04-21 19:25 → 22:30)**:
- `c:\Users\hitea\Claude\LiveChord\tmp\bait_root.py` → **survived 3+ hours** ✓ (not in TMP_DIR scope)
- `c:\Users\hitea\Claude\LiveChord\scripts_test\bait_scripts_dir.py` → **survived** ✓
- `c:\Users\hitea\Claude\LiveChord\data\tmp\bait_post_ss_disable.py` → **deleted at 20:12** (13 min after drop, despite SS disabled)

**Rule for AI agents + future contributors**:
1. **Never write scripts, plans, or long-lived data into `data/tmp/`** — it's backend-owned scratch with a 10-min TTL. Python bytecode survives (protected by `is_file()` check) but source files don't.
2. **Project-local scratch dirs that survive**: `c:/Users/hitea/Claude/LiveChord/tmp/`, `c:/Users/hitea/Claude/LiveChord/scripts_test/`, `c:/Users/hitea/Claude/LiveChord/data/scratch/` (anything NOT matching `data/tmp`)
3. **If you need OS-level temp**: `c:/Users/hitea/AppData/Local/Temp/midi_reorg/` is stable (OS won't touch, backend won't touch).
4. **Phase 4 POC convention update**: earlier CLAUDE.md noted `data/tmp/midi_align_multitrack.py` as Phase 4 POC location — **update that doc**. The file was almost certainly eaten by this cleanup thread, explaining why it "disappeared" across sessions. See [doc/PHASE_4_HYBRID_MELODY.md](PHASE_4_HYBRID_MELODY.md) line 281 reference.

**Side note — Storage Sense was disabled during diagnosis but is unrelated**. User may re-enable at will (`HKCU:\...\StoragePolicy\01 = 1`). It was never the culprit.

---

## (Add new TODOs below as they come up)
