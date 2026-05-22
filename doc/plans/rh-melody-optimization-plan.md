# RH Melody Optimization Plan

**Date**: 2026-05-20 (revised after critical review; cleaned after Phase 0 instrumentation and sponsor removal)
**Status**: Phase 0 instrumentation complete; Phase 0.5 shadow-candidate foundation ready for small A/B smoke tests
**Tracking**: `LiveChord-2t5y` (active Phase 0/1), `LiveChord-zpu1` (review-log hardening follow-up)

## 1. Summary

Current RH melody is not extracted from an explicit right-hand instrument, vocal stem, piano stem, or lead-instrument stem. The primary path is:

```text
full mix audio -> mono -> harmonic component -> librosa pYIN F0 -> melody events
```

The frontend labels this event stream as RH melody because it is rendered and played as the learner's right-hand melody lane. That label describes the teaching lane, not the audio source. In practice, the result behaves like "the most trackable dominant F0 in the full mix":

| Song context | Current likely result |
|---|---|
| Single vocal is stable and prominent | Often resembles vocal melody |
| Instrumental song or strong solo | Often follows instrument lead |
| Backing vocals, high accompaniment, piano right hand, guitar riff, duet, dense harmony | Can jump to the wrong line |

The optimization goal is not "replace pYIN with one neural model" or "always use vocals". The goal is a **source-aware melody resolver** that:

1. First, classifies the song into a small set of song types (vocal pop, solo piano, jazz/multi-solo, MR/karaoke, ambient/no-lead, mixed-section)
2. Routes each song type to an extractor path designed for that type, with two explicit algorithm pipelines that fill pYIN's weakest cases:
   - **Vocal-led**: HTDemucs v4 vocal stem → CREPE F0 tracking → vibrato smoothing → note segmentation. FTANet on full mix is kept as a fallback only when the stem is unusable.
   - **Solo piano**: Magenta Onsets and Frames polyphonic transcription → velocity/density pre-filter → Skyline candidate set → Temperley Bayesian Viterbi (range / interval / key priors) → post-merge
3. Keeps the existing full-mix pYIN path as a guaranteed fallback
4. Exposes enough metadata that the UI and admin tools can see what source was selected, and is allowed to return "no melody" as a confident outcome

Rationale for the algorithm choice (desk comparison lives in §4.2):

- If Phase 2 adopts `vocal_stem_crepe`, **Demucs precompute becomes shared required infrastructure** for both `vocal_stem_crepe` and `instrument_lead`; the vocal-stem path then costs no extra separation pass. Given that, HTDemucs vocal stem → CREPE is engineering-simpler than FTANet on full mix, has MIT / Apache-style permissive licensing across the core code stack, and CREPE-on-stem is the benchmark hypothesis Phase 2 must prove against pYIN.
- **Polyphonic AMT is mature enough to use as Stage 1; symbolic RH selection is the real risk.** The previous draft handwaved the Stage-2 selector as "prefer sustained/high-salience upper voice"; this revision commits to a named baseline (Skyline + Temperley Viterbi) so Phase 2 has a concrete deliverable rather than an unbounded research project.

Project status for license and governance decisions: LiveChord is a personal hobby / non-commercial project. `livechord.org` currently has single-digit daily users, no ads, no paywall, no paid tier, no marketing, and no actual sponsorship revenue. The old Buy Me a Coffee / sponsor surface was removed on 2026-05-20 (`/sponsor` now returns 404), so the public presentation matches the actual hobby/non-commercial status.

The plan is also constrained by a **prove-it-first** discipline. Before building 4-tier candidates + scoring + resolver, Phase 0 must show that the existing pYIN failure modes actually need a new audio source, not just better post-filtering.

## 2. Current System

Primary user-visible path:

| Step | File / cache | Role |
|---|---|---|
| API | `backend/ai_api.py` `/api/ai/melody` | Reads `V:\data\melodies\<hash>.json` or runs extraction |
| Extractor | `backend/ai/melody_extractor.py` | Loads full audio with `librosa.load(..., mono=True)`, applies preemphasis + HPSS harmonic, runs `librosa.pyin` |
| Schema | `backend/ai/melody_schema.py` | Normalizes events into schema v2 and `voice_lane = "rh_melody"` |
| Frontend | `frontend/js/player.js` | Reads `/api/ai/melody`, filters events, maps them into RH melody waterfall / keyboard / jianpu / score-render |

Existing non-primary paths:

| Path | Output | Current status |
|---|---|---|
| `backend/ai/melody_extractor_v2.py` | `V:\data\melodies_v2\<hash>.json` | Basic Pitch shadow / experiment; not the player source |
| `backend/batch_hybrid_worker.py` | `V:\data\hybrid_melody\<hash>.mid` | Demucs vocals + Basic Pitch + sanitizer for hybrid features; not connected to `/api/ai/melody` |
| `doc/PHASE_4_HYBRID_MELODY.md` | planned `midi_aligned` / `melodies_v4` | Hybrid architecture POC; not yet promoted into the primary API |

## 3. Product Definition

RH melody should mean:

1. A playable, teachable lead line for the right-hand lane.
2. For vocal-led songs: usually the main vocal line.
3. For instrumental songs: the dominant lead line for that song's structure (see §3.1).
4. A stable single melodic line, not a dense transcription of every high voice.
5. **An empty result is allowed and is a valid resolver outcome** for ambient / drone / pure-percussion / jam tracks that have no playable RH line.
6. A transparent source selection, so the UI and debugging tools can distinguish `full_mix_pyin`, `vocal_stem_crepe`, `vocal_full_mix_ftanet`, `solo_piano_polyphonic`, `instrument_lead`, `midi_aligned`, and fallback results.

### 3.1 Instrumental is not a single category

Instrumental songs fail differently and need different routing. The plan treats them as five sub-types, not one:

| Sub-type | Audio character | Why pYIN fails | Routing intent |
|---|---|---|---|
| **Solo piano** (Chopin, jazz piano, lo-fi piano) | Polyphonic, RH+LH on the same stem | Monophonic F0 grabs LH bass or chord tones | Polyphonic transcription + top-voice extraction, ideally MIDI-aligned |
| **Fingerstyle guitar / harp / koto** | Melody and bass interleaved on the same instrument | F0 jumps between melody and bass | Polyphonic + top-voice; weaker than solo piano because no good MIDI corpus |
| **Jazz combo / multi-solo** | Lead voice changes mid-song (sax → piano → guitar) | One source assumption fails | Future scope: section-aware switching. v1: pick the dominant solo voice for the song |
| **Ambient / jam / drone** | No single melodic line | Resolver invents melody from texture | Allow empty melody output with `quality_flags: ["no_lead_detected"]` |
| **MR / karaoke (vocal-removed)** | Original arrangement minus lead vocal | vocal stem candidate is empty | Detect "vocal-cavity" + route to `full_mix_pyin` knowing the user wants the implied vocal line back |

For songs with **MIDI alignment available** (Phase 4 hybrid), instrumental songs benefit disproportionately — classical and jazz solo piano have abundant high-quality MIDI corpora (IMSLP, Maestro, the user's `X:\` library), whereas vocal MIDI is rare and often inaccurate. The resolver should preferentially attempt MIDI alignment on instrumental candidates before any audio extraction.

Non-goals:

| Non-goal | Reason |
|---|---|
| Treat RH melody as a literal "right-hand instrument stem" | Most source audio is mixed commercial audio and has no RH stem |
| Always use vocals | Many user songs are instrumental, and even vocal songs have piano/guitar intros |
| Directly promote Basic Pitch V2 as a universal replacement | Prior shadow analysis showed polyphonic extras are architectural for vocal songs. Piano needs a dedicated **polyphonic piano transcription** route, not generic monophonic F0; the chosen Stage-1 model is Magenta Onsets and Frames (Apache 2.0, mature) with ByteDance hFT as a deferred alternative if license/runtime justify a switch |
| Break existing `V:\data\melodies` cache | Current user-visible behavior must remain available as fallback |
| **Section-aware source switching in v1** | Section detection is currently on the rule-based fallback path for ~82% of the library; building section-aware melody routing on top of unreliable section data invites compound failure. Listed as future scope, see §11. |

## 4. Target Architecture

Introduce a backend resolver layer:

```text
GET /api/ai/melody
  -> MelodyResolver
      -> classify song type (vocal / solo_piano / instrumental_solo / mr / no_lead / unknown)
      -> read selected RH cache if present
      -> else inspect candidate caches per type-specific priority
      -> else run current full-mix pYIN fallback
      -> return legacy-compatible melody[] plus source metadata
```

Candidate priority is **type-specific**, not universal. The single global ladder in the previous draft hides the fact that the best candidate for a vocal pop song is bad for solo piano.

### 4.1 Type-specific candidate ladders

**Vocal-led songs** (pop, K-pop, C-pop, ballad with clear vocal):

| Priority | Candidate | Source |
|---:|---|---|
| 1 | `vocal_stem_crepe` | HTDemucs v4 vocal stem → CREPE F0 → vibrato median filter → note segmentation |
| 2 | `midi_aligned` | Curated MIDI (rare for vocal — usually a fallback miss) |
| 3 | `vocal_full_mix_ftanet` | FTANet on full mix as a fallback when the Demucs stem is missing, empty, or visibly artifacted |
| 4 | `full_mix_pyin` | Existing baseline |

Phase 0.5 review result: `vocal_stem_crepe` beat `full_mix_pyin` on 12/12 vocal smoke songs. This is enough to promote the **candidate path**, but not enough to ship the **resolver route** by itself. The ship blocker is the song-type gate: if the classifier sends solo piano / instrumental songs into the vocal path, an empty vocal stem can produce an empty or near-empty melody, which is worse than the conservative pYIN fallback.

Resolver v0 therefore requires:

- `vocal_led` classifier precision measured on a held-out hand-labeled evaluation set before promotion. Precision must be at least as high as the observed vocal B-win rate target (>= 92%) before the route can become automatic.
- Retreat-to-baseline hard gate: if `vocal_stem_crepe` active coverage / density is abnormally low relative to `full_mix_pyin` (initial rule: active duration or active density < 30% of baseline), select `full_mix_pyin` and stamp `quality_flags: ["vocal_candidate_retreat_low_coverage"]`.
- Pre-ship residual audit on the 12 vocal smoke songs: quantify intro/interlude missing coverage and phrase-tail pitch-jump artifacts. If either appears in >30% of reviewed vocal songs, fix the post-filter before enabling resolver v0.

Classifier short-circuit check (2026-05-22): no existing cache contains a real song-type prediction. `data/chords` only provides sparse genre/category hints, legacy `data/melodies` has no classifier metadata, and `data/melody_candidates` already has the right `melody_source.song_type` / `song_type_confidence` schema but the values are hardcoded as `"unknown"` / `None`. Therefore the next step is to build the classifier and stamp those fields, not to measure a nonexistent classifier.

**Solo piano** (classical, jazz piano, lo-fi piano):

| Priority | Candidate | Source |
|---:|---|---|
| 1 | `midi_aligned` | Curated MIDI — high hit rate for this sub-type |
| 2 | `solo_piano_polyphonic` | Magenta Onsets and Frames → velocity/density pre-filter → Skyline candidate set → Temperley Bayesian Viterbi → post-merge |
| 3 | `full_mix_pyin` | Fallback only — known to fail on polyphonic piano |

Phase 0.5 review result: `solo_piano_polyphonic` beat `full_mix_pyin` on 7/9 solo-piano smoke songs; 2/9 were `neither` due to crossed/interleaved hands and accompaniment bleed. This is useful but not yet the 85% solo-piano gate.

Stage 2.1 priority is **hand-state continuity**, not only stronger top-voice bias:

- Track the recent selected melody trajectory and penalize candidates that jump more than an octave away from the running hand state unless the surrounding context supports a real RH leap.
- Add an onset-density gate: when there is only one plausible onset in the window, skip the expensive selector and keep the trivial path; activate RH selection only when there is a polyphonic conflict.
- Keep top-voice / skyline as a candidate generator, not as the whole decision rule. Debussy-like textures can briefly put LH above RH, so pure highest-pitch wins is known to fail.
- If two tuning rounds cannot move the 9-song piano set beyond 7/9-8/9, ship piano only for high-confidence solo-piano subtypes and retreat the rest to `full_mix_pyin`.

**Instrumental solo** (jazz combo, fingerstyle, sax/guitar lead):

| Priority | Candidate | Source |
|---:|---|---|
| 1 | `midi_aligned` | If available |
| 2 | `instrument_lead` | Demucs `other` stem + pYIN (only if Demucs precompute is committed; see §4.3) |
| 3 | `full_mix_pyin` | Fallback |

Phase 0.5 review result: `instrument_lead` beat `full_mix_pyin` on only 2/6 instrumental smoke songs; 4/6 were `neither`. This route is **not** a general instrumental fallback. It is only applicable when the song has a clear monophonic lead instrument. Fusion pads, organ comping, multi-instrument trades, and jam tracks can make the target itself ill-defined.

Resolver v0 must not auto-promote `instrument_lead`. The A/B tooling should keep an `applicable` review field so future analysis can distinguish "candidate failed" from "the song has no single lead to extract". A real instrumental route needs a lead-existence classifier, which is a later phase.

**MR / karaoke**:

| Priority | Candidate | Source |
|---:|---|---|
| 1 | `full_mix_pyin` | The implied vocal line lives in the mix; vocal stem will be empty |

**No-lead / ambient / jam**:

| Outcome | Detail |
|---|---|
| Empty melody | `melody: []`, `quality_flags: ["no_lead_detected"]`, `melody_source.id = "no_lead"` |

**Unknown / classifier low-confidence**:

| Outcome | Detail |
|---|---|
| `full_mix_pyin` | Conservative fallback |

### 4.2 Two pipelines that fill the pYIN gap

This revision makes the two non-pYIN paths explicit, comparison-driven, and named down to the algorithm and license. They are not generic "better pitch trackers"; each is a concrete two-stage pipeline that solves a specific failure class.

The pipelines below are chosen from a desk review and must still be validated by Phase 2 benchmarks before promotion. The comparison table is in §4.2.3; it records the proposed baseline choices, not a completed audio benchmark.

#### 4.2.1 Vocal-led extraction: HTDemucs v4 vocal stem → CREPE

Reference algorithms:
- Separation: Rouard et al., "Hybrid Transformers for Music Source Separation" (ICASSP 2023). Package: `demucs` (Meta AI, MIT license).
- F0 tracking: Kim et al., "CREPE: A Convolutional Representation for Pitch Estimation" (ICASSP 2018). Package: `crepe` / `torchcrepe` (MIT license).

Pipeline:

```text
mixed audio
-> HTDemucs v4 (4-stem: vocals / bass / drums / other)
-> vocal stem (16 kHz mono)
-> CREPE F0 (10 ms hop, 'full' or 'tiny' model, viterbi voicing)
-> vibrato median filter + voiced-island consolidation
-> note segmentation (onset/offset from F0 derivative + energy)
-> `vocal_stem_crepe` candidate
```

Operational details:

| Detail | Decision |
|---|---|
| Separator model | HTDemucs v4 (`htdemucs` or `htdemucs_ft` per Phase 2 benchmark) |
| Stem used | `vocals` (4-stem mode); `other` reused by `instrument_lead` so Demucs runs once per song |
| Tracker model size | Default `crepe.full` (~96 MB) for PC precompute; `crepe.tiny` (~9 MB) reserved for any future on-NUC fallback |
| Tracker sample rate | 16 kHz (CREPE native) — **no 8 kHz downsampling**, unlike FTANet; downstream confidence is directly comparable to LiveChord's existing pYIN at 22.05 kHz after sample-rate normalization |
| Voicing decision | CREPE viterbi voicing + confidence threshold (default 0.5, tuned in Phase 3) |
| Vibrato smoothing | Sliding median filter, window ~5 frames, applied before note segmentation; preserves phrase bends in metadata only |
| Note segmentation | Onset where F0 derivative exceeds threshold OR voicing transitions; minimum note duration 80 ms (matches existing `MelodyExtractor` floor) |
| Confidence | Per-note: mean CREPE confidence × voicing fraction × stem energy ratio at note time |

Why HTDemucs + CREPE is the proposed Tier 1 vocal baseline:

1. If `vocal_stem_crepe` is adopted, Demucs is shared required infrastructure for both `vocal_stem_crepe` and `instrument_lead` (§4.3), so the vocal stem is a byproduct of the same separation pass.
2. CREPE on a clean vocal stem is expected to beat pYIN on RPA/RCA; Phase 2 must measure this on LiveChord's vocal subset rather than assume it.
3. License stack is permissive (HTDemucs MIT, CREPE / torchcrepe MIT). Magenta's NC-inheriting weights are fine under LiveChord's current non-commercial scope, but the vocal route having no NC entanglement is still operationally simpler.
4. No 8 kHz resampling — sample-rate confidence calibration is trivial.
5. Two-stage pipeline is **simpler** than FTANet's CFP + frequency-temporal attention + per-frame distribution + post-decode chain.

FTANet on full mix is retained as a Tier 3 fallback (`vocal_full_mix_ftanet`) for the edge case where Demucs fails or the vocal stem comes out empty/artifacted. This is a small fraction of the library but worth catching.

This route is PC/precompute-first. It must not run in the NUC request path until runtime is measured and cache reads are proven file-speed.

#### 4.2.2 Solo piano extraction: Magenta Onsets and Frames → Skyline + Temperley Viterbi

Reference algorithms:
- Stage 1 (AMT): Hawthorne et al., "Onsets and Frames: Dual-Objective Piano Transcription" (ISMIR 2018). Package: `magenta` / `tensorflow_magenta` (Apache 2.0). Pretrained weights from MAESTRO v3.0.0 (CC BY-NC-SA — see §9 license risk row).
- Stage 2 (symbolic selection):
  - Skyline: classic uppermost-voice heuristic (Uitdenbogerd & Zobel, 1999), implemented as a few-hundred-line custom Python pass over `pretty_midi` events.
  - Temperley Bayesian model: Temperley, *The Cognition of Basic Musical Structures* (2001), Chapter 4. Reimplementable in ~150 lines via Viterbi over three priors (range, interval, key).

Pipeline:

```text
solo piano audio
-> Onsets and Frames inference
   -> 88-key onset probability
   -> 88-key frame probability
   -> 88-key offset probability
   -> velocity regression
   -> pedal estimation
-> polyphonic note events (full MIDI, retained for debug)
-> Stage 2 RH selection:
   1. LH/RH split (range pivot ~C4, pedal-aware)
   2. Velocity pre-filter: drop RH notes below median - 1.5*MAD
   3. Density pre-filter: 0.25 s window, ≥5 simultaneous/near-onset notes tagged as `texture` (arpeggio / Alberti)
   4. Skyline candidate set: per time step take highest non-`texture` RH note(s)
   5. Temperley Viterbi on candidate set:
        - State = previous selected pitch
        - Transition prior: gaussian over semitone interval (σ ≈ 5 semitones)
        - Emission prior: gaussian around running median pitch (σ ≈ 8 semitones)
        - Key prior: Krumhansl-Kessler key profile, key auto-detected from full transcription
   6. Post-merge: combine same-pitch notes across <120 ms gaps, drop notes <50 ms
-> `solo_piano_polyphonic` candidate
```

Operational details:

| Detail | Decision |
|---|---|
| Stage-1 model | Magenta Onsets and Frames (Apache 2.0 code; MAESTRO-trained weights inherit CC BY-NC-SA, permissible under LiveChord's non-commercial scope — see §9) |
| Stage-1 alternative under evaluation | Kong et al. 2021 high-resolution piano transcription (`qiuqiangkong/piano_transcription_inference`, MIT). Only swap in if Phase 2 benchmark shows ≥3 pp F1 advantage AND maintenance status improves |
| Full polyphonic MIDI kept for debug | Stored alongside `solo_piano_polyphonic.json`; helps admin diff RH selection against ground truth |
| Stage-2 LH/RH split | Range pivot configurable per-song; defaults to C4 with hand-cross tolerance window of ±5 semitones over 250 ms |
| Stage-2 Viterbi prior weights | Phase 2 ships rule-based defaults from Temperley 2001; admin can override per song; ML voice separator deferred to §11 |
| Arpeggio detection | Inter-onset interval < 100 ms + interval cluster pattern recognition; arpeggio is suppressed but its uppermost endpoint is kept as a melody candidate |
| Confidence | Combine Stage-1 onset confidence, Stage-2 Viterbi path probability, and the fraction of candidate set retained vs. dropped |

Why this fills a real gap (and why this specific Stage 2 algorithm):

- Solo piano is polyphonic by definition; a monophonic tracker collapses multiple voices into one unstable F0 path.
- **Stage 1 (multi-pitch AMT) is solved.** Magenta Onsets and Frames is 5+ years stable, broadly benchmarked, Apache 2.0 code, and has ONNX export paths. ByteDance hFT scores marginally higher on MAPS F1 but has lukewarm maintenance.
- **Stage 2 (symbolic RH selection) is the actual research problem**, and the previous draft handwaved it. Skyline + Temperley Viterbi is the proposed rule-based baseline:
  - Skyline alone fails when arpeggios spike above the melody — hence the velocity + density pre-filter that strips texture before skyline runs.
  - Temperley's three priors (range, interval, key) are not arbitrary — they are the empirically validated cognitive priors humans use to follow melodic lines in polyphony. Piano RH satisfies these priors better than almost any other instrument.
  - The whole Stage 2 fits in ~300 lines of `pretty_midi` + numpy. No model weights, no training data, no maintenance debt.

ML-based symbolic voice separators (PiJAMA, FCN-over-piano-roll) are deferred to §11 — they may beat Temperley on Bach fugues, but the v1 hypothesis is that Temperley is strong enough for the LiveChord library mix (pop arrangements, classical Romantic, lo-fi piano).

This route is also PC/precompute-first. It is evaluated only for `solo_piano` / piano-led candidates and should not be offered as a universal full-mix replacement.

#### 4.2.3 Algorithm survey: what was compared before picking

The choices in §4.2.1 / §4.2.2 are the result of a comparison, not a default. Summary of evaluated alternatives:

**Vocal-led candidates considered:**

| Candidate | Outcome | Primary reason |
|---|---|---|
| HTDemucs vocal stem → CREPE | **Selected as proposed Tier 1** | Clean permissive license stack (MIT); CREPE on a stem is the benchmark hypothesis; Demucs is shared with `instrument_lead` if this route is adopted |
| HTDemucs vocal stem → RMVPE | Deferred to §11 | Better noise tolerance, but RMVPE weights are from the RVC voice-cloning community with ambiguous provenance; CREPE on a Demucs v4 stem is already sufficient |
| HTDemucs vocal stem → pYIN | Dropped from ladder | Strictly worse than CREPE on the same stem; not worth a separate tier |
| FTANet (full mix) | **Retained as Tier 3 fallback** | Useful when Demucs stem is missing/empty; not Tier 1 because the two-stage stem pipeline is engineer-cleaner and licensed cleaner |
| FTANet (vocal stem) | Dropped | Duplicate cost to HTDemucs + CREPE with no measured advantage |
| Omnizart vocal mode | Deferred to §11 | Apache 2.0 and one-stop, but TF 1.x dependency conflicts with LiveChord's PyTorch stack; maintenance has slowed |
| CREPE (full mix, no separation) | Dropped | Monophonic assumption collapses on full mix, same failure mode as pYIN |
| SPICE / JDC / DeepSalience | Dropped | None beat HTDemucs + CREPE on a clean stem; packaging and maintenance weaker than CREPE |

**Solo piano candidates considered:**

| Candidate | Outcome | Primary reason |
|---|---|---|
| Magenta Onsets and Frames + Skyline + Temperley | **Selected as proposed Tier 1** | Apache 2.0 code; mature; pedal-aware; Stage 2 is concrete and ~300 LOC |
| ByteDance hFT (`qiuqiangkong`) + Skyline + Temperley | Phase 2 swap candidate | ~2-4 pp higher MAPS F1 but maintenance is slow; swap only if benchmark justifies |
| Spotify Basic Pitch + symbolic selection | Dropped from primary | Generic polyphonic, not piano-specialized; overproduces ornaments |
| Omnizart piano mode | Deferred to §11 | Stage 2 is built-in but lower accuracy than Magenta; TF 1.x burden |
| ML symbolic voice separator (PiJAMA / FCN) | Deferred to §11 | Promising for jazz / fugue, but training data is sparse; compare later only if the Temperley baseline fails the solo-piano gate |
| Skyline alone (music21) | Insufficient | Fails on arpeggio peaks; kept only as the candidate-set step inside the §4.2.2 pipeline |
| Pure velocity filter | Insufficient | Classical mezza voce melody under ff arpeggio breaks the dynamic assumption |

### 4.3 `instrument_lead` honesty clause

The previous draft listed `instrument_lead` as a candidate but described it as "full-mix harmonic pYIN plus optional Demucs other/harmonic route". The first half is identical to the current path — it would be relabeling, not a new technique. This plan commits explicitly:

- **`instrument_lead` requires Demucs `other` stem precompute.** If Phase 2 does not commit Demucs precompute on PC-side, this candidate is **dropped**, not silently relabeled. Resolver simply falls through to `full_mix_pyin` for the instrumental-solo sub-type, with `quality_flags: ["instrument_lead_unavailable"]`.
- **`instrument_lead` is applicable only when there is a clear monophonic lead instrument.** Phase 0.5 showed this candidate is useful for some sax/guitar lead songs but unreliable for fusion or jam tracks where the "lead" target is ambiguous. Treat lead-existence detection as a separate future classifier, not a selector-tuning problem.

### 4.4 Cache layout

```text
V:\data\melodies\<hash>.json                            # existing full-mix pYIN, keep stable
V:\data\melody_candidates\<hh>\<hash>\full_mix_pyin.json
V:\data\melody_candidates\<hh>\<hash>\vocal_stem_crepe.json
V:\data\melody_candidates\<hh>\<hash>\vocal_full_mix_ftanet.json
V:\data\melody_candidates\<hh>\<hash>\solo_piano_polyphonic.json
V:\data\melody_candidates\<hh>\<hash>\solo_piano_polyphonic_full.mid   # debug: full polyphonic transcription before Stage-2 selection
V:\data\melody_candidates\<hh>\<hash>\instrument_lead.json
V:\data\melodies_rh_v2\<hh>\<hash>.json                 # selected resolver output, behind flag
V:\data\midi_aligned\<hh>\<hash>.json                   # curated Tier 1 output
V:\data\melodies_v4\<hh>\<hash>.json                    # existing Phase 4 precomputed target
V:\data\stems\<hh>\<hash>\{vocals,bass,drums,other}.wav # HTDemucs precomputed stems, shared by vocal_stem_crepe + instrument_lead
```

`<hh>` = first 2 chars of `<hash>`. **All new caches use the same sharding scheme as the chord-JSON migration (`abe6172`).** Flat-directory layouts (the previous draft's design) would put 100k+ files on SMB and regress the perf win we already paid for.

### 4.5 Response shape (additive, schema v2)

`schema_version` stays at `2`. New fields are **additive optional metadata** — existing frontend works unchanged:

```json
{
  "schema_version": 2,
  "path": "...",
  "melody": [
    { "start": 1.2, "end": 1.8, "time": 1.2, "duration": 0.6, "midi": 64, "pitch": 64, "voice_lane": "rh_melody" }
  ],
  "melody_source": {
    "id": "full_mix_pyin",
    "stem": "full_mix",
    "algorithm": "librosa.pyin",
    "song_type": "vocal_pop",
    "song_type_confidence": 0.78,
    "selected_by": "fallback",
    "candidate_score": 0.62,
    "margin_over_fallback": 0.00,
    "cache_version": "rhmelody-v2",
    "phase": "phase0"
  },
  "quality_flags": ["fallback_full_mix"]
}
```

Frontend can ignore new metadata initially. Debug UI, admin review, and future user-facing labels can use it later. `cache_version` follows the versioned cache family (`rhmelody-v2`, aligned with `melodies_rh_v2/`); rollout phase is stored separately as `phase`. `selected_by="legacy_primary"` means the current full-mix pYIN path produced the payload before any resolver decision existed. `selected_by="fallback"` is reserved for the future resolver choosing `full_mix_pyin` after comparing candidates.

### 4.6 Deployment scope (server-mode matters)

`/api/ai/melody` runs on three modes with very different constraints. The resolver must be explicit about what runs where:

| Mode | Resolver behavior |
|---|---|
| **NUC `personal`** (LAN, 8800) | Full resolver. Demucs / FTANet-style vocal / piano-transcription candidates are precomputed on PC, then synced to V:\ |
| **PC `personal_local`** (8803) | Full resolver. Same data as NUC via V:\ mount |
| **VPS `public`** (livechord.org) | **`full_mix_pyin` only**, no resolver, no candidate generation. Demucs on CPX21 is too slow for upload-path latency; Modal dispatching melody extraction is out of scope for v1 |

Resolver gating uses `LIVECHORD_MODE`:
- `personal` → resolver active behind feature flag
- `public` → resolver code path is bypassed, legacy `MelodyExtractor` is called directly

This is the same shape as the existing public-mode-disables-YT pattern. No code in the resolver should be VPS-bound.

## 5. Source Selection

The resolver scores candidates against a **conservative bias toward the existing pYIN fallback**. This mirrors the `bar_arbitrator` lesson: "only acts when candidate grid scores above `_MIN_CANDIDATE_F1`". Without this anchor, hand-tuned 9-feature scoring will silently regress songs where pYIN was fine.

### 5.1 Conservative decision rule

```text
selected = full_mix_pyin
  UNLESS
    candidate.score - full_mix_pyin.score >= MIN_MARGIN
    AND candidate.passes_hard_gates
```

`MIN_MARGIN` is tuned per song type during Phase 3, biased toward not switching. `passes_hard_gates` rejects candidates with obviously broken output (zero coverage, extreme density, all-sub-50-Hz, etc.) regardless of score.

### 5.2 Candidate features

| Feature | Purpose | Notes |
|---|---|---|
| Note density per second and per bar | Penalize dense accompaniment bleed and overly sparse failed extraction | Phase 0 exposes `density_when_active_per_s`, computed over the melody's active span. Full-song density requires known song duration and is a later scoring feature |
| Median range and range span | Detect bass leakage, octave jumps, impossible RH lines | |
| Continuity / jump rate | Prefer singable or lead-instrument-like motion | |
| Confidence distribution | Detect weak F0 tracking or noisy transcription | |
| Section coverage | Avoid candidates that disappear for most of the song | |
| Vocal stem energy ratio | Prefer vocal route when vocals are clearly present | Only computed if vocal stem candidate ran |
| Chord / key compatibility | Penalize chromatic noise without overfitting to chord tones | |
| Agreement with full-mix pYIN | Use current pYIN as a weak reference, not ground truth | |
| Library path / genre prior | Hint only | **Demoted from routing gate.** User's library has been rearranged repeatedly; folder ≠ ground truth. Max 5% weight in score |

### 5.3 Song-type classifier (front gate)

A small classifier picks the type before scoring:

| Signal | Used to detect |
|---|---|
| Demucs vocal stem RMS ratio (if computed) | `vocal_pop` vs `instrumental_*` vs `mr` |
| HPSS percussive ratio | `solo_piano` vs `instrumental_solo` vs `ambient` |
| Spectral centroid distribution | `solo_piano` vs others |
| Library path tag | weak hint only |
| Curated MIDI presence | bonus to `solo_piano` / `instrumental_solo` |
| Total onset density | `ambient` / `no_lead` detection |

Classifier emits `song_type` + `song_type_confidence`. Low confidence → resolver routes to `unknown` → `full_mix_pyin`.

Current implementation status: schema slots already exist under `melody_source`, but no classifier populates them yet. The Phase 0.5 smoke queue `group` field is a manual review label, not a model prediction, and must not be used as if it were classifier output.

Phase 0.5 classifier plan:

| Stage | Work | Exit condition |
|---|---|---|
| A1 | Hand-label 30-50 additional songs from `library_cache.json` as held-out `vocal_led` / `solo_piano` / `instrumental_lead` / `no_clear_lead` | Evaluation set is separate from the 27-song smoke set |
| A2 | Build the first `classify_song_type()` hook using cheap available features: Demucs vocal-stem energy ratio where available, HPSS/spectral/onset features, weak path/category prior, curated MIDI presence | Emits `song_type`, `song_type_confidence`, and `song_type_source` |
| A3 | Wire the prediction into shadow candidate generation so candidate caches stamp `melody_source.song_type` / `song_type_confidence` instead of `"unknown"` | Re-running the 27 smoke songs with `--force` backfills classifier metadata |
| A4 | Add an admin/eval switch for prediction source: classifier output vs manual queue label | Resolver experiments can compare classifier-on and oracle/manual routing |
| B1 | Compute predicted-vs-human confusion matrix on held-out labels | `vocal_led` precision >= 92% before automatic vocal routing |
| B2 | Compute vocal residual metrics on the 12 smoke vocal songs | Intro/interlude missing coverage and phrase-tail jump artifacts each <= 30% |

If a cheap LR/decision-tree classifier stays below 85% vocal precision, consider an ingest-time LLM metadata classifier as a fallback. That is acceptable only if it runs once per song, records the prompt/model/version in `song_type_source`, and still obeys the same held-out precision gate.

Stage A metadata-only result (2026-05-22): the 48-song held-out label set is complete. The original metadata hint baseline reached only `vocal_led` precision 0.667. A first metadata Naive Bayes baseline was artificially depressed by unseen-token Laplace bias; after fixing unseen tokens to contribute zero evidence, the fair leave-one-out baseline is `vocal_led` precision 0.786. Metadata-only routing is still rejected for resolver v0 because it remains below the 0.92 vocal precision gate. Continue with audio-derived features, especially Demucs vocal-stem energy ratio and cheap HPSS/onset features, before considering an LLM metadata fallback.

Stage A2 feature extraction status (2026-05-22): `backend/ai/song_type_audio_features.py` and `tools/extract_rh_song_type_audio_features.py` now extract cheap mix features (`hpss_harmonic_ratio`, `onset_density_per_s`, `spectral_flatness_mean`, `spectral_centroid_mean`, RMS variation, zero-crossing rate) plus cached-stem energy ratios when stems already exist. The first full 48-song extraction wrote `V:\data\melody_reviews\phase0_5_song_type_audio_features.jsonl` with 48/48 ok rows. All held-out rows currently report `missing_cached_stems`, so the next classifier pass can compare metadata+mix features immediately; Demucs vocal-stem energy ratio still requires a deliberate stem precompute pass for this held-out set.

Stage A2 classifier result (2026-05-22): `metadata_audio_nb_v1` joined metadata tokens with fixed-bucket mix-feature pseudo-tokens, keeping the same Naive Bayes infrastructure and avoiding a new sklearn/runtime dependency. Leave-one-out on the 48-song held-out set improved `vocal_led` precision from 0.786 to 0.867 and recall from 0.688 to 0.813. A follow-up `metadata_audio_nb_v2` pass removes common metadata stop tokens (`of`, `the`, `is`, etc.) after false-vocal diagnostics showed function words could overpower audio evidence; before full stem coverage this moved optimistic `vocal_led` precision to 0.923 with recall 0.75. After precomputing Demucs stems for all 48 held-out rows, the same NB bucket model fell back to `vocal_led` precision 0.867 / recall 0.813, showing that the multiclass NB is not the right consumer for stem ratios.

Audit caveat: the first `metadata_audio_nb_v1` bucket thresholds were chosen after inspecting the 48-row feature distribution, so 0.867 should be treated as an optimistic internal-progress number rather than a leakage-clean held-out result. Before using the number as a ship metric, freeze audio buckets from an unlabeled calibration pool or switch to calibration-set z-score buckets. The conclusion is unchanged because the optimistic number is still below 0.92.

Error diagnostic (2026-05-22): `tools/diagnose_rh_song_type_classifier.py` writes per-row LOO predictions and focus slices. The first diagnostic report, `V:\data\melody_reviews\phase0_5_song_type_metadata_audio_nb_diagnostics.json`, found 8 total errors and 3 `vocal_led -> instrumental_lead` misses. Segment checks on those 3 misses did not show a simple "instrumental intro only" pattern; mid-song mix features remained tonal/instrumental-like. `Flashback` (Camille/Julie Berthollet) is a classical-crossover vocal duet/choral texture, not an instrumental boundary case; the model misses it because the full-mix spectral profile is close to chamber/instrumental crossover. The other two (`Darkness on the Delta`, `Suantraí`) are also true vocal-led but acoustically close to jazz/tonal textures in the full mix. A false-vocal slice found `Old Man River` (solo piano) and `Dark Is the Night` (instrumental lead) as the two precision-costing cases; stop-token removal fixed `Old Man River`, leaving `Dark Is the Night` as the only false-vocal prediction in the pre-stem run.

Stem-gate result (2026-05-22): `tools/precompute_rh_song_type_stems.py` generated/cached Demucs stems for all 48 held-out rows, and `tools/evaluate_rh_vocal_gate.py` evaluated a conservative binary route gate instead of the multiclass NB. This is an architectural pivot: NB remains useful for broad metadata/mix hints, but a dominant continuous signal like vocal-stem energy should be consumed by an explicit resolver gate rather than diluted into bucket tokens and Laplace smoothing. Rule: `duration_s >= 30` and `vocal_stem_energy_ratio >= 0.30` -> allow `vocal_stem_crepe`; otherwise fallback. The 0.30 threshold is empirical from the 48-row Phase 0.5 gap, not a domain prior; on that set, 0.25 gives one false positive, 0.30 gives zero false positives, and 0.35 loses extra recall. The 30s minimum guards against unreliable Demucs ratios on very short clips. On the 48 held-out rows this yields `vocal_led` precision 1.000 and recall 0.875 (14 TP, 0 FP, 2 FN). The two misses are low-vocal-ratio vocals (`TSUKIAKARINO MICHISHIRUBE`, `玖壹壹-夢醒時分Remix`, ratios ~0.19). This is the first route candidate that clears the 0.92 precision gate on the held-out set, but it is still threshold-calibrated on the same 48 rows and must be validated on a larger or leakage-clean set before resolver promotion.

Leakage-clean gate validation setup (2026-05-22): `tools/sample_rh_vocal_gate_validation.py` sampled `V:\data\melody_reviews\phase0_5_vocal_gate_validation_queue.jsonl`, 100 rows from `library_cache.json`, excluding the prior 48 song-type labels and 27 A/B smoke rows. The queue is for binary review (`vocal_led`, `not_vocal`, `unknown`) and should be labeled before running full stem precompute/evaluation on that set. Initial metadata-hint distribution is 68 vocal, 26 instrumental, 3 solo piano, 3 no-clear-lead; this is a validation queue, not a balanced training set. Gate reports include both strict precision (unknown counted as not-vocal) and lenient precision (unknown excluded from denominators), so ambiguous human labels do not silently distort the ship gate. After labeling, run the gate at 0.25 / 0.30 / 0.35 and pick the highest-recall threshold that keeps strict or explicitly-approved lenient precision >= 0.92.

Leakage-clean calibration update (2026-05-22): the 100-song validation queue is labeled and evaluated. Human labels: 48 `vocal_led`, 52 `not_vocal`, 0 `unknown`. Demucs precompute generated stems for 100/100 rows, feature extraction wrote 100/100 ok rows with cached stems, and `tools/evaluate_rh_vocal_gate.py` wrote `V:\data\melody_reviews\phase0_5_vocal_gate_validation_eval_fine_sweep.json`. The original 0.30 gate is safe but too conservative on this validation set (`precision=1.000`, `recall=0.604`, 29 TP / 0 FP / 19 FN). Fine sweep found a clean ratio gap: highest `not_vocal` ratio 0.05445, lowest `vocal_led` ratio 0.06541. Thresholds 0.055 / 0.060 / 0.065 all yield `precision=1.000`, `recall=1.000` on this 100-song validation set; 0.050 yields 2 false positives, and 0.070 starts losing recall. Resolver v0 should therefore use `vocal_stem_energy_ratio >= 0.06` with `duration_s >= 30`, while documenting that 0.06 is validation-calibrated and should be monitored after larger-scale rollout.

Resolver v0 wiring (2026-05-22): `backend/ai/melody_resolver.py` is the first `/api/ai/melody` source selector. It is active only outside public mode and can be disabled with `LIVECHORD_RH_MELODY_RESOLVER=0`. It never runs Demucs, CREPE, or pYIN; it only reads existing candidate caches. The hot path first checks for `vocal_stem_crepe.json`, so songs without a vocal candidate fall back without loading stem WAVs. If the candidate exists, the resolver computes cached-stem ratio features, applies `classify_vocal_gate()` defaults (`0.06`, `30s`), retreats when candidate active duration or active density is below 30% of full-mix pYIN, writes the selected payload to `V:\data\melodies_rh_v2\<hh>\<hash>.json`, and stamps `melody_source.resolver_gate` with gate reason/ratio/duration/stem status for drift monitoring. Public/VPS mode continues to serve legacy full-mix pYIN only.

Vocal residual audit (2026-05-22): `tools/report_rh_vocal_residuals.py` now computes the Stage B residual proxies on the 12 vocal smoke songs: 10s-window candidate coverage against active full-mix pYIN windows, plus phrase-tail pitch jumps in the last 0.5s before silence gaps. The report at `V:\data\melody_reviews\phase0_5_vocal_residual_report.json` has 12/12 rows, 0 songs above the 30% coverage-gap threshold, 0 songs above the 30% phrase-tail-jump threshold, and `passes_stage_b_residual_gate=true`. This closes the pre-ship vocal-route Stage B gate; remaining work is post-ship drift monitoring on real resolver selections.

`unknown` is not a learnable class yet: the held-out plus validation sets have only one `unknown` row total. Resolver v0 should treat unknown as a low-confidence fallback outcome (`song_type_confidence < threshold -> full_mix_pyin`), not as a normal supervised class, unless a larger ambiguous-label set is collected.

## 6. Implementation Phases

### Phase 0 - Failure-mode survey and metadata visibility

The previous draft put "baseline metrics" first and added metadata second. This is reversed: metadata is the cheap, no-risk change, and is needed for the survey to be reproducible.

Deliverables:

| Item | Output | Status |
|---|---|---|
| Add `melody_source` + `quality_flags` to `/api/ai/melody` (additive, optional) | Survey can record per-song selected source | Done |
| Admin/debug view shows current source + flags | Reproducible inspection; `quality_flags` should be visible in Phase 0, not after resolver tuning | Done |
| Admin survey panel | Load queue, open player, inspect, tag, and submit Phase 0 review entries | Done |
| **Failure-mode taxonomy is frozen before listening review starts** | One primary tag per song/segment, optional secondary flags; avoids drifting standards across 200 songs | Done |
| **Failure-mode survey on 200 equal-probability random reviewable songs** | Tagged distribution of failures: `pyin_fine / wrong_octave / bass_leakage / wrong_line_backing_vocal / accompaniment_chord_tone / accompaniment_riff_lead / sparse_threshold / sparse_genuine_silence / duet_alternating / solo_piano_polyphonic_collapse / no_lead_present / audio_quality / no_issue_audible`; no subtype stratification, to avoid biasing the observed current fail rate | Pending human listening review |
| Phase 0 summary/report | Completion count, tag distribution, post-filter-fixable ratio, secondary/audio-quality breakdown | Pending |
| Public reference dataset hook | Run current pYIN against MedleyDB-Melody / MIR-1K subset (≥20 songs), record RPA/RCA against ground truth | Pending |

Phase 0 review taxonomy:

| Primary tag | Definition | Post-filter fixable? |
|---|---|---|
| `pyin_fine` | Current pYIN is musically usable; resolver should not switch away | No action |
| `wrong_octave` | Correct contour but octave is visibly/audibly displaced or jumps against the running median | Usually yes |
| `bass_leakage` | pYIN follows bass/LH below the intended lead line | Usually yes |
| `wrong_line_backing_vocal` | pYIN follows harmony/backing vocal above or beside the lead | Usually no; needs source routing |
| `accompaniment_chord_tone` | pYIN follows accompaniment chord tones that a chord-tone/range gate can plausibly suppress | Yes |
| `accompaniment_riff_lead` | pYIN follows a real riff or accompaniment lead line instead of the intended melody | No; needs source routing or section logic |
| `sparse_threshold` | Lead exists but pYIN voiced/confidence thresholds miss too many notes | Yes |
| `sparse_genuine_silence` | Sparse output reflects real vocal/lead rests rather than extractor failure | No |
| `duet_alternating` | Two lead voices alternate and pYIN switches between them inconsistently | No; needs source or arrangement-aware routing |
| `solo_piano_polyphonic_collapse` | Solo piano polyphony collapses into a mixed monophonic line | No; needs polyphonic/top-voice route |
| `no_lead_present` | The audio genuinely has no stable RH lead line | No; correct output is empty melody |
| `audio_quality` | Failure is mainly caused by the recording itself: heavy reverb, clipping, low bitrate/sample-rate artifacts, strong noise, live-room bleed, or bad source separation | Usually no; lower resolver confidence and surface the flag |
| `no_issue_audible` | JSON/metric looks suspicious but playback is acceptable to a human reviewer | No action |

Review rule: every reviewed song or highlighted segment gets exactly one primary tag, plus optional secondary flags such as `audio_quality_secondary`, `mixed_section_single_source`, `quantization_jitter`, `source_intro_missing`, or `needs_ab_replay`. The primary `audio_quality` tag is important because it separates algorithm defects from cases where every extractor is likely to be unstable; use `audio_quality_secondary` only when the main failure is another tag but source quality also affected confidence. Phase 0's post-filter decision uses all reviewed primary tags in the denominator: tags marked post-filter-fixable count toward Phase 1 post-filters; tags marked not fixable count against them.

Survey tooling:

```bash
python tools/sample_melody_phase0_survey.py --sample-size 200 --seed 20260520 --force
```

The script samples `library_cache.json` tracks when that cache is available, because it is already the fastest auditable list of playable songs; if no library cache exists, it falls back to scanning sharded chord cache files and checking audio existence. It writes `data/melody_reviews/phase0_survey_queue.jsonl` plus `phase0_survey_queue.summary.json`. `--chords-root` defaults to `LIVECHORD_CHORDS_DIR`, then `V:\data\chords` when present; repo-local `data/chords` is only a development fallback and prints a warning. Review tags are written through `POST /api/ai/melody/debug/tag` into `data/melody_reviews/phase0_tags.jsonl`; the endpoint reuses `/api/ai/melody/debug` metadata, validates the frozen taxonomy, stores the reviewer, `survey_id`, `failure_tag`, `secondary_flags`, `audio_quality_note`, optional segment, machine proxies, and current melody stats.

Current Phase 0 artifacts:

| Artifact | Location / status |
|---|---|
| Survey queue | `V:\data\melody_reviews\phase0_survey_queue.jsonl`, 200 rows, seed `20260520` |
| Survey summary | `V:\data\melody_reviews\phase0_survey_queue.summary.json` |
| Review log | `V:\data\melody_reviews\phase0_tags.jsonl` |
| Admin panel | `http://192.168.50.6:8800/admin` -> RH survey |
| Public behavior | Unchanged; VPS remains `full_mix_pyin` only |

Exit gate:

| Gate | Requirement |
|---|---|
| Failure distribution known | We can quote percentages, not vibes |
| Taxonomy consistency | Failure-mode definitions above were used for the whole review set |
| Admin visibility | `quality_flags`, source id, note count, density, and primary failure tag can be inspected in the admin/debug UI |
| Reference metrics anchored | At least one external dataset score for current pYIN, used as floor for all later phases |
| No product change | `/api/ai/melody` behavior remains unchanged |
| Compatibility | Existing frontend still works with only `melody[]` |
| Cache safety | Existing `V:\data\melodies` files are not invalidated |

**Decision branch out of Phase 0**: If the survey shows >50% of failures are post-process fixable (`wrong_octave`, `bass_leakage`, short-gap fragmentation, chord-tone confidence errors), **Phase 1 is prioritized and Phase 2 waits**. If the >50% cluster is specifically octave-jump / median-drift behavior, invest first in the Phase 1 octave-fold filter instead of building candidate routing. A 4-tier resolver is overkill if pYIN is mostly fine and a few cheap filters close the gap.

### Phase 1 - Cheap post-filters on existing pYIN

Conditional on Phase 0 decision branch. If post-filters can resolve most failures, this is the entire shipped change and Phases 2-5 may not be needed.

Deliverables:

| Filter | What it fixes |
|---|---|
| Octave-fold around running median (move to extractor cache) | Reduces visual octave jumps; currently lives in player `_filterMelody` only |
| Bass-leakage gate | Drop notes with MIDI < 48 AND confidence < threshold AND below median by > 1 octave |
| Chord-tone-aware confidence boost | Notes within chord-tone semitone distance get a confidence bump; chromatic noise gets a penalty |
| Coverage gap close | Merge same-pitch notes across short gaps |

Exit gate:

| Gate | Requirement |
|---|---|
| Re-run survey | Failure-mode percentages compared to Phase 0 |
| Reference metric improvement | RPA/RCA on MedleyDB-Melody subset improves or stays equal |
| No regression | No song in the survey gets clearly worse |

### Phase 2 - Candidate builder (only if Phase 1 insufficient)

Deliverables:

| Candidate | Implementation note |
|---|---|
| `full_mix_pyin` | Wrap current `MelodyExtractor` output as a named candidate |
| `vocal_stem_crepe` | HTDemucs v4 vocal stem → CREPE (`torchcrepe.full`) → vibrato median filter → note segmentation; primary vocal-led path per §4.2.1 |
| `vocal_full_mix_ftanet` | FTANet on full mix — fallback only, generated only when `vocal_stem_crepe` is missing or hard-gate-failed |
| `solo_piano_polyphonic` | Magenta Onsets and Frames → Skyline pre-pass → Temperley Viterbi → post-merge per §4.2.2. Stores full polyphonic MIDI alongside the selected melody for debug. **Piano sub-type only.** |
| `instrument_lead` | Demucs `other` stem + pYIN — **only if Demucs precompute is committed**, else dropped per §4.3 |
| `midi_aligned` | Read existing Phase 4 aligned JSON when present |

Demucs precompute is shared infrastructure: the same `htdemucs` invocation populates `vocals/` (consumed by `vocal_stem_crepe`), `other/` (consumed by `instrument_lead`), and is available to future candidates without re-running separation.

Exit gate:

| Gate | Requirement |
|---|---|
| Shadow completeness | Candidate files can be generated without changing player output |
| Observability | Candidate generation logs runtime, note count, failure reason, and cache path |
| Sharded layout | New caches written to `<hh>/<hash>/` per §4.4, not flat |

### Phase 3 - Song-type classifier, quality scoring, resolver

Deliverables:

| Item | Output |
|---|---|
| Song-type classifier | §5.3 — emits type + confidence |
| `MelodyCandidateScore` | Shared scoring object with density, range, continuity, coverage, source prior, flags |
| `MelodyResolver` | Conservative decision rule per §5.1 — only switches when margin and hard gates pass |
| Golden review report | Per-song before/after with selected source, classifier output, and manual verdict |
| Automated A/B diff replay tool | UI/debug page that jumps to disagreement windows and toggles `selected_source` vs `full_mix_pyin` without hand-reading JSON |

Exit gate (tiered, because instrumental sub-types differ):

| Subset | Threshold |
|---|---|
| Overall: equal-or-better than current pYIN on manual review | ≥ 80% |
| Severe regression | ≤ 10% |
| Vocal songs: main vocal source selection accuracy | ≥ 85% |
| **Solo piano: lead-line selection accuracy** | **≥ 85%** (raised — user is a piano player, this sub-type matters more than the previous lumped 75%) |
| **Other instrumental (jazz solo, fingerstyle, etc.)** | **≥ 70%** |
| MR / karaoke: correctly routed to `full_mix_pyin` | ≥ 90% |
| Ambient / no-lead: correctly emits empty melody | ≥ 80% |
| Reference dataset (MedleyDB-Melody / MIR-1K subset) RPA | Improves over Phase 0 baseline |

Manual review uses both a single annotator (the user) AND machine-computable proxies:

| Machine proxy | Definition |
|---|---|
| `vocal_alignment_ratio` | Time-overlap of selected melody onsets with Demucs vocal stem RMS peaks |
| `chord_tone_distance_median` | Median semitone distance from each melody note to nearest chord tone at that time |
| `extreme_range_excursion_rate` | Fraction of notes > 1 octave from running median |
| `coverage_gap_fraction` | Fraction of song with no melody event |

These are reportable per-song and per-subset, removing pure-vibes dependence on a single reviewer.

Golden review workflow must reduce manual-review fatigue. The report should not only render static metrics; it should generate a clickable review queue of disagreement windows where the resolver selected a different source than `full_mix_pyin`, where machine proxies disagree, or where `quality_flags` are high risk. Each item should support quick A/B playback between `Selected Source` and `Full-mix pYIN`, with the same audio time range, waterfall/keyboard overlay, and optional score preview. The reviewer records the primary failure tag from the Phase 0 taxonomy directly from that view.

### Phase 4 - Shadow API rollout

Deliverables:

| Item | Output |
|---|---|
| Feature flag | `ENABLE_RH_MELODY_RESOLVER=false` by default; gated additionally on `LIVECHORD_MODE=personal` |
| Shadow endpoint | Optional `/api/ai/melody-v2` or debug query parameter for comparison |
| Background queue | Fresh uploads keep current pYIN response, then enqueue higher-quality candidate generation on PC-side |

Exit gate:

| Gate | Requirement |
|---|---|
| Latency | Cached resolver read ≈ file-read speed; fresh fallback no slower than current pYIN path |
| Rollback | Turning the flag off immediately returns current full-mix pYIN behavior |
| Logs | Selection decisions are inspectable per hash |
| **Downstream verified** | RH waterfall, keyboard, jianpu, **AND score-render (VexFlow)** all render correctly. Score is sensitive to quantization (canonical duration must equal meter window); switching extractors can shift quantization assumptions — explicit QA required, not assumed |
| **MIDI visual jitter verified** | For `midi_aligned` selections, run a playback visual QA that checks waterfall cursor, note onset timing, and VexFlow measure quantization do not visibly wobble against the audio after DTW warp. Small timestamp/BPM drift that is inaudible can still be obvious in the UI, so this is a separate gate from alignment confidence |

### Phase 5 - Primary promotion

Deliverables:

| Item | Output |
|---|---|
| `/api/ai/melody` integration | Resolver becomes primary read path behind production flag (personal mode only) |
| Cache promotion | Selected results write to `melodies_rh_v2`, not directly over legacy pYIN |
| Admin controls | Force source, invalidate selected cache, compare candidates |
| **Public mode parity check** | VPS continues serving `full_mix_pyin` only; verify no resolver code accidentally activates when `LIVECHORD_MODE=public` |

Exit gate:

| Gate | Requirement |
|---|---|
| Production sample | Personal-mode QA on representative hashes passes waterfall, keyboard, jianpu, score-render, and practice modes |
| Operations | Backfill/queue progress is visible and restart-safe |
| Data safety | Legacy `melodies` cache remains available for fallback and comparison |
| **Definition of "done"** | After 3 months of `ENABLE_RH_MELODY_RESOLVER=true`, admin "force source" override is used on < 10% of inspected songs. If higher, scoring is not good enough and flag stays as override-able. If lower, the flag is removed and resolver is locked on. See §11. |

## 7. File-Level Work Plan

Likely backend additions:

| File | Change |
|---|---|
| `backend/ai/melody_resolver.py` | New source-aware resolver, type-specific ladders, conservative decision rule |
| `backend/ai/melody_classifier.py` | Song-type classifier (§5.3) |
| `backend/ai/melody_candidate.py` | Candidate schema and normalization helpers |
| `backend/ai/melody_quality.py` | Density/range/continuity/source-prior scoring + machine proxies |
| `backend/ai/melody_post_filter.py` | Phase 1 octave-fold / bass-leakage / chord-tone post-filters |
| `backend/ai/stem_separation.py` | PC-side HTDemucs v4 wrapper; shared stem cache at `V:\data\stems\<hh>\<hash>\` consumed by vocal-stem and instrument-lead candidates |
| `backend/ai/vocal_melody_crepe.py` | PC-side vocal-stem → CREPE candidate wrapper; owns torchcrepe inference, vibrato filtering, and note segmentation |
| `backend/ai/vocal_melody_ftanet.py` | PC-side FTANet fallback candidate wrapper for full-mix vocal extraction; runs only when stem path is unusable |
| `backend/ai/piano_melody_transcriber.py` | PC-side Magenta Onsets and Frames wrapper; emits full polyphonic MIDI consumed by `piano_rh_selector` |
| `backend/ai/piano_rh_selector.py` | Stage 2 symbolic selector: LH/RH split, velocity + density pre-filter, Skyline candidate set, Temperley Bayesian Viterbi, post-merge |
| `backend/ai_api.py` | Route `/api/ai/melody` through resolver behind flag; bypass in public mode |
| `backend/process_queue.py` | Queue candidate generation after upload melody fallback (personal mode only); writes a `candidate_generation_pending` marker for the PC worker to pick up |
| `backend/batch_hybrid_worker.py` or new PC worker | Generate precomputed HTDemucs stems, CREPE vocal melody, FTANet fallback, piano transcription + RH selection, and instrument-lead candidates |

Likely frontend additions:

| File | Change |
|---|---|
| `frontend/js/player.js` | Optionally expose source/debug metadata; no required change for legacy playback |
| Admin/debug pages | Candidate comparison, selected source, force/invalidate controls, song-type label |
| `frontend/i18n/*.json` + `i18n.js` `DICT_VERSION` | If user-facing source labels added, **lockstep bump `DICT_VERSION` AND every HTML's `i18n.js?v=` ref** per the established cache-bust discipline |

## 8. Validation Set

Stratified review set, **not just "50-100 songs"** — distribution matters and is fixed up-front so subset gates in §6 Phase 3 are measurable:

| Sub-type | Count | What it tests |
|---|---:|---|
| Clean single vocal | 25 | Vocal route should beat or match pYIN |
| Quiet vocal over loud accompaniment | 15 | Source selection must avoid amplitude-only mistakes |
| Backing vocals above lead | 10 | Reject highest-pitch-wins artifacts |
| Duet / harmony | 10 | Prefer main line or stable lead |
| Piano intro before vocal | 10 | Mixed-section behavior — flagged as known v1 weakness, see §11 |
| Solo piano (classical) | 20 | MIDI-aligned + polyphonic path |
| Solo piano (jazz / lo-fi) | 10 | Polyphonic without MIDI |
| Guitar/sax/synth instrumental lead | 15 | `instrument_lead` if Demucs committed, else `full_mix_pyin` |
| Fingerstyle guitar | 5 | Polyphonic interleaved melody/bass |
| Dense piano right hand (Rachmaninoff-style accompaniment) | 5 | Avoid converting accompaniment texture into melody |
| MR / karaoke | 10 | Detect vocal cavity, route to `full_mix_pyin` |
| Ambient / jam / drone | 10 | Empty melody is the correct output |
| Reference dataset cross-check (MedleyDB-Melody / MIR-1K) | 20+ | External RPA/RCA anchor |
| **Total** | **165+** | |

Recommended golden review fields (the 165+ quota above is for later resolver-quality expectations, not the unbiased 200-song Phase 0 failure-rate survey):

```text
hash
path
sub_type: vocal_clean | vocal_quiet | backing_vocal | duet | piano_intro | solo_piano_classical | solo_piano_jazz | instrument_solo | fingerstyle | dense_rh | mr | ambient | reference
expected_source: vocal_stem_crepe | vocal_full_mix_ftanet | solo_piano_polyphonic | instrument_lead | midi_aligned | full_mix_pyin | no_lead
current_pyin_grade: good | ok | bad
resolver_grade: good | ok | bad
selected_source
selected_song_type
machine_proxies: { vocal_alignment_ratio, chord_tone_distance_median, extreme_range_excursion_rate, coverage_gap_fraction }
failure_tag
secondary_flags: [audio_quality_secondary, quantization_jitter, mixed_section_single_source, source_intro_missing]
audio_quality_note: none | reverb_high | clipped | noisy | low_bitrate | live_room_bleed | separation_artifact
stats: { density_when_active_per_s, active_duration_s, midi_min, midi_max, midi_median }
review_note
```

## 9. Risks and Decisions

| Risk / decision | Plan |
|---|---|
| Pretrained-weight licenses for CREPE / FTANet / Magenta MAESTRO / ByteDance hFT | Phase 2 commit-gate: each model's training-data + weight license is recorded but does **not** block deployment under the current LiveChord scope. LiveChord is non-commercial everywhere today: NUC personal, PC local, and the public livechord.org hobby service have no ads, no paywall, no paid tier, no marketing, single-digit daily users, and no actual sponsorship revenue. Notes: CREPE / torchcrepe MIT; Magenta code Apache 2.0 with MAESTRO-trained weights inheriting CC BY-NC-SA — fine under current non-commercial scope, **re-evaluate before any monetization**; FTANet weights from `yushuai/FTANet-melodic` still need explicit verification; ByteDance hFT MIT but maintenance is slow. ShareAlike re-licensing only becomes relevant if LiveChord distributes derivative *trained* weights — out of scope for v1 and gated by a separate review if it ever arises |
| Public sponsor/donation surface creates avoidable license ambiguity | Resolved 2026-05-20. The Buy Me a Coffee / sponsor page, navigation, sitemap/SEO references, i18n copy, README mention, and config-driven BMC CTA were removed or hidden; `/sponsor` now returns 404. Keep this surface removed unless a future monetization decision triggers a separate license/governance review |
| HTDemucs runtime and dependency weight | PC-side precompute only; htdemucs full-quality model is ~80 MB; per-song separation ~2-6× realtime on CPU; cache stems once and reuse across `vocal_stem_crepe` + `instrument_lead` |
| CREPE runtime | `torchcrepe.full` is ~96 MB and faster than realtime on CPU per vocal stem; `tiny` (~9 MB) reserved for any future NUC fallback. No on-NUC use in v1 |
| VPS requirements drift after shadow deps | Phase 0.5 added personal/shadow dependencies (`torchcrepe` plus Demucs/OmegaConf `antlr4-python3-runtime<4.10`). The VPS systemd service does not auto-install requirements after `git pull`, and public mode should not execute candidate generation. Before the next VPS deploy, run `pip install -r backend/requirements.txt` before `systemctl restart livechord`; if the VPS footprint becomes a problem, split personal-only shadow deps into `requirements-personal.txt` before public deploy |
| FTANet runtime and dependency weight | Fallback path only; PC-side; runs only when `vocal_stem_crepe` is missing or hard-gate-failed |
| Magenta TF / LiveChord PyTorch coexistence | PC precompute uses an isolated venv OR ONNX-exported Onsets-and-Frames; avoid forcing TF into the main LiveChord backend image |
| Stage-2 Temperley prior weights drift into endless tuning | Phase 2 ships rule-based defaults from Temperley 2001 verbatim; admin can override per song; an ML voice separator is §11, not v1 |
| RMVPE runtime and dependency weight | Deferred to §11; evaluate only if `vocal_stem_crepe` fails on a measurable subset of noisy stems |
| Vocal-only can drop piano/guitar intros | Acknowledged as v1 weakness; mixed-section songs may be tagged `quality_flags: ["mixed_section_single_source"]`. Section-aware switching is §11 future scope |
| Cache version confusion | Never overwrite legacy `melodies` during rollout; write selected output to versioned `melodies_rh_v2`; `schema_version` stays at 2, new fields are additive metadata |
| UI label confusion | Separate "RH melody lane" from "source = vocal/solo-piano/instrument/full mix" metadata |
| Manual review cost | Stratified set (§8) fixed up-front; machine proxies (§6 Phase 3) reduce single-annotator dependence |
| Manual review still takes too long | Phase 3 requires automated A/B diff replay, so review focuses on windows where resolver and pYIN disagree instead of full-song listening |
| Audio quality masquerades as algorithm failure | Phase 0 taxonomy includes `audio_quality`; resolver confidence should be reduced rather than overfitting a new extractor to bad source audio |
| `instrument_lead` becomes a relabel of `full_mix_pyin` | §4.3 commits the candidate is dropped if Demucs precompute is not committed — no silent relabeling |
| Genre/library prior overfits to user's folder layout | Demoted to ≤5% scoring weight; never a routing gate |
| Hand-tuned scoring regresses songs that were fine | Conservative decision rule (§5.1) — resolver only switches when candidate beats fallback by `MIN_MARGIN` and passes hard gates |
| Score-render quantization mismatch | Phase 4 exit gate explicitly verifies VexFlow rendering after extractor swap |
| VPS accidentally runs resolver | §4.6 + Phase 5 exit gate — resolver bypassed when `LIVECHORD_MODE=public` |
| Plan becomes a perpetual epic alongside neural_arranger | §6 Phase 5 includes a hard "definition of done" — 3-month admin-override usage rate |
| Phase 4 hybrid_melody / `melodies_v4` is itself unshipped | `midi_aligned` Tier is gated on Phase 4 landing; if Phase 4 stays POC, resolver routes around it transparently |

## 10. Current Next Steps

Phase 0 instrumentation is complete, but current RH quality is poor enough that a 200-song baseline-only survey would be hard to judge. Before the full listening survey, build a small **Phase 0.5 shadow-candidate foundation**: generate candidate caches without changing `/api/ai/melody`, then let the Admin review compare current `full_mix_pyin` against the new routes.

| Order | Work | Status |
|---:|---|---|
| 1 | Add shared shadow candidate cache helpers for `V:\data\melody_candidates\<hh>\<hash>\*.json` and `V:\data\stems\<hh>\<hash>\*.wav` | Done |
| 2 | Add persistent HTDemucs stem-cache wrapper so `vocals` and `other` are separated once and reused by `vocal_stem_crepe` / `instrument_lead` | Done |
| 3 | Add `vocal_stem_crepe` wrapper: vocals stem -> CREPE F0 -> vibrato smoothing -> note segmentation -> candidate cache | Done |
| 4 | Add `solo_piano_polyphonic` Stage-2 selector: Magenta/polyphonic notes -> Skyline candidates -> Temperley Viterbi -> candidate cache | Done |
| 5 | Add a one-song/batch shadow generation entry point for small A/B trials without changing formal RH playback | Done |
| 6 | Add Admin candidate compare/read endpoint and UI hook so review can switch `full_mix_pyin` vs shadow candidates | Done |
| 7 | Run a 20-30 song A/B smoke set (vocal, solo piano, instrumental) to see whether the new routes are worth scaling | Done: 27-song queue reviewed. `vocal_stem_crepe` 12/12 B wins, `solo_piano_polyphonic` 7/9 B wins, `instrument_lead` 2/6 B wins with 4/6 marked not applicable/no clear lead |
| 8 | Build the song-type classifier Stage A (§5.3): held-out label set, cheap-feature classifier, cache stamping into `melody_source.song_type`, classifier-vs-manual switch | Done for vocal gate: held-out 48-song label queue completed; metadata hint baseline `vocal_led` precision 0.667, fair metadata NB LOO 0.786 after unseen-token fix. Metadata+mix/stem NB is not sufficient. The leakage-clean 100-song vocal gate validation is labeled and evaluated; threshold 0.06 gives strict precision 1.000 / recall 1.000 on this validation set |
| 9 | Vocal route ship audit Stage B: confusion matrix on held-out labels, `vocal_led` precision >= 92%, 30% retreat gate metrics, and 12-song vocal residual metrics | Done: leakage-clean vocal gate passed at threshold 0.06; resolver v0 is wired for cached `vocal_stem_crepe` promotion with 30% low-coverage retreat and public-mode bypass; 12-song vocal residual report passed both 30% thresholds. Post-ship drift monitoring is a follow-up, not a blocker |
| 10 | Add Phase 0 survey summary/report output: completion count, primary-tag distribution, post-filter-fixable ratio, secondary/audio-quality breakdown | Pending |
| 11 | Finish the 200-song equal-probability listening survey through the Admin RH survey panel, using candidate comparison where available | Pending human review |
| 12 | Run current `full_mix_pyin` against a MedleyDB-Melody / MIR-1K subset (≥20 songs); record RPA/RCA as the external floor for later phases | Pending |
| 13 | **Decision branch**: classify failure modes. If cheap post-filters still dominate, Phase 1 is prioritized; if vocal/piano shadow routes clearly rescue the bad cases and classifier gates pass, proceed into Phase 2 candidate generation at larger scale | Pending Phase 0/0.5 data |

Completed cleanup and instrumentation:

| Work | Status |
|---|---|
| Freeze the Phase 0 failure-mode taxonomy before reviewing the 200-song sample | Done |
| Add optional `melody_source` + `quality_flags` metadata to current `/api/ai/melody` output (Phase 0, no behavior change) | Done |
| Visualize source id, `quality_flags`, note density, and failure tag in Admin/debug UI | Done |
| Generate the 200-song Phase 0 random survey queue | Done |
| Remove the Buy Me a Coffee / sponsor surface (`/sponsor`, header/menu links, sitemap/SEO references, i18n copy, README mention) | Done |

## 11. Explicit Future Scope (v2+)

The plan deliberately does not attempt these in v1. They are listed so they aren't smuggled in via scope creep:

| Future item | Why deferred |
|---|---|
| **Section-aware source switching** | Requires reliable section detection. CLAUDE.md notes `section_detect` is on rule-based fallback for ~82% of the library. Building source-routing on top of unreliable section boundaries compounds failure. Revisit after section detection improves (Phase 3 of [doc/PHASE_4_HYBRID_MELODY.md](../PHASE_4_HYBRID_MELODY.md)) |
| Multi-source blended output | Same as above — needs reliable section boundaries to splice sources without audible seams |
| User per-song source override (UI control) | Admin override exists in Phase 5; user-facing override is a separate UX decision |
| RMVPE as approved tracker for noisy stems | Deferred. Re-evaluate only if Phase 2/3 review shows `vocal_stem_crepe` failing on a measurable subset of Demucs-stem artifacts; CREPE on Demucs v4 stems is expected to be sufficient |
| Omnizart as a one-stop vocal/piano/polyphonic candidate generator | Deferred. Apache 2.0 and one-stop, but TF 1.x conflicts with LiveChord's PyTorch stack and maintenance has slowed; revisit if the project's maintenance status improves |
| ML symbolic voice separator (PiJAMA / FCN over piano-roll) replacing Temperley Viterbi | Deferred. Promising for fugues and dense jazz arrangements but training data and packaging are immature; Temperley Viterbi is the v1 baseline |
| ByteDance hFT replacing Magenta Onsets and Frames | Phase 2 swap candidate. Only swap if a benchmark shows ≥3 pp MAPS F1 advantage on the LiveChord library AND repo maintenance status improves |
| HTDemucs v4 alternatives (RoFormer / MDX-Net) | Deferred. v4 is current SOTA for vocal/other separation; switch only on a measured stem-quality gain |
| Trained ML resolver (replacing rule-based scoring) | `bar_arbitrator` pattern: rule-based first (Phase 0), trained model later. Same applies here. No ML resolver in v1 |
| VPS resolver path | VPS stays on `full_mix_pyin` until Modal-dispatched melody extraction is approved as a cost line |

## 12. Definition of Done

The plan is **complete** when:

1. Resolver runs in production (NUC personal mode) for 3 months with `ENABLE_RH_MELODY_RESOLVER=true`
2. Admin "force source" override is used on < 10% of inspected songs
3. Phase 3 exit-gate subset accuracies are still met against a refreshed validation set
4. The feature flag is removed; resolver is locked on for personal mode
5. VPS public mode is verified to still use `full_mix_pyin` only

If condition 2 fails (> 10% override rate), scoring is not good enough — the flag stays override-able and the plan returns to Phase 3 instead of declaring done.
