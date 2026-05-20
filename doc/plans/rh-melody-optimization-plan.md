# RH Melody Optimization Plan

**Date**: 2026-05-20
**Status**: planning
**Tracking**: `LiveChord-jqnm`

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

The optimization goal is not simply "replace pYIN with Basic Pitch" or "always use vocals". The goal is a source-aware melody resolver that chooses the best playable lead line for the song, keeps the existing full-mix pYIN path as a fallback, and exposes enough metadata to know what source was selected.

## 2. Current System

Primary user-visible path:

| Step | File / cache | Role |
|---|---|---|
| API | `backend/ai_api.py` `/api/ai/melody` | Reads `V:\data\melodies\<hash>.json` or runs extraction |
| Extractor | `backend/ai/melody_extractor.py` | Loads full audio with `librosa.load(..., mono=True)`, applies preemphasis + HPSS harmonic, runs `librosa.pyin` |
| Schema | `backend/ai/melody_schema.py` | Normalizes events into schema v2 and `voice_lane = "rh_melody"` |
| Frontend | `frontend/js/player.js` | Reads `/api/ai/melody`, filters events, maps them into RH melody waterfall / keyboard / jianpu |

Existing non-primary paths:

| Path | Output | Current status |
|---|---|---|
| `backend/ai/melody_extractor_v2.py` | `V:\data\melodies_v2\<hash>.json` | Basic Pitch shadow / experiment; not the player source |
| `backend/batch_hybrid_worker.py` | `V:\data\hybrid_melody\<hash>.mid` | Demucs vocals + Basic Pitch + sanitizer for hybrid features; not connected to `/api/ai/melody` |
| `doc/PHASE_4_HYBRID_MELODY.md` | planned `midi_aligned` / `melodies_v4` | Hybrid architecture POC; not yet promoted into the primary API |

## 3. Product Definition

RH melody should mean:

1. A playable, teachable lead line for the right-hand lane.
2. Usually the main vocal line for vocal songs.
3. The lead instrument for instrumental songs.
4. A stable single melodic line, not a dense transcription of every high voice.
5. A transparent source selection, so the UI and debugging tools can distinguish `full_mix_pyin`, `vocal_stem`, `lead_instrument`, `midi_aligned`, and fallback results.

Non-goals:

| Non-goal | Reason |
|---|---|
| Treat RH melody as a literal "right-hand instrument stem" | Most source audio is mixed commercial audio and has no RH stem |
| Always use vocals | Instrumental songs, intros, solos, and non-vocal lead sections need instrument lead |
| Directly promote Basic Pitch V2 | Prior shadow analysis showed polyphonic extras are architectural, not just threshold noise |
| Break existing `V:\data\melodies` cache | Current user-visible behavior must remain available as fallback |

## 4. Target Architecture

Introduce a backend resolver layer:

```text
GET /api/ai/melody
  -> MelodyResolver
      -> read selected RH cache if present
      -> else inspect candidate caches
      -> else run current full-mix pYIN fallback
      -> return legacy-compatible melody[] plus source metadata
```

Candidate priority for production reads:

| Priority | Candidate | Source | Intended use |
|---:|---|---|---|
| 1 | `midi_aligned` | Curated MIDI aligned to audio | Highest-quality classic songs with known MIDI |
| 2 | `vocal_rmvpe` / `melodies_v4` | Demucs vocals + vocal-specialized F0 tracker | Vocal songs after PC precompute |
| 3 | `instrument_lead` | Other/harmonic/full-mix lead candidate | Instrumental songs and prominent solos |
| 4 | `full_mix_pyin` | Existing `V:\data\melodies` | Universal fallback and regression safety |
| Shadow only | `vocal_basic_pitch` / `hybrid_melody` | Demucs vocals + Basic Pitch MIDI | Candidate evidence, not primary until scored |

Recommended cache layout:

```text
V:\data\melodies\<hash>.json                    # existing full-mix pYIN, keep stable
V:\data\melody_candidates\<hash>\full_mix_pyin.json
V:\data\melody_candidates\<hash>\vocal_rmvpe.json
V:\data\melody_candidates\<hash>\instrument_lead.json
V:\data\melody_candidates\<hash>\vocal_basic_pitch.json
V:\data\melodies_rh_v2\<hash>.json              # selected resolver output, behind flag
V:\data\midi_aligned\<hash>.json                # curated Tier 1 output
V:\data\melodies_v4\<hash>.json                 # existing Phase 4 precomputed target
```

The final response should remain backward-compatible:

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
    "selected_by": "fallback",
    "confidence": 0.62,
    "cache_version": "rhmelody-v2"
  },
  "quality_flags": ["fallback_full_mix"]
}
```

Frontend can ignore new metadata initially. Debug UI, admin review, and future user-facing labels can use it later.

## 5. Source Selection

The resolver should score candidates instead of assuming one extractor wins everywhere.

Candidate-level features:

| Feature | Purpose |
|---|---|
| Note density per second and per bar | Penalize dense accompaniment bleed and overly sparse failed extraction |
| Median range and range span | Detect bass leakage, octave jumps, and impossible RH lines |
| Continuity / jump rate | Prefer singable or lead-instrument-like motion |
| Confidence distribution | Detect weak F0 tracking or noisy transcription |
| Section coverage | Avoid candidates that disappear for most of the song unless expected |
| Vocal stem energy ratio | Prefer vocal route when vocals are clearly present |
| Chord / key compatibility | Penalize chromatic noise without overfitting to chord tones |
| Agreement with full-mix pYIN | Use current pYIN as a weak reference, not ground truth |
| Library path / genre prior | POP/K-POP/C-POP likely vocal; Jazz/Jam/Relax may be instrumental or solo-led |

Song-level routing signals:

| Signal | Decision impact |
|---|---|
| Curated MIDI exists with high alignment confidence | Select MIDI-aligned |
| Strong vocal stem and vocal candidate passes density/continuity gates | Select vocal candidate |
| Weak vocal stem but strong harmonic/full-mix lead | Select instrument lead or full-mix pYIN |
| Multiple candidates disagree and no one passes gates | Keep current pYIN fallback |
| Candidate is much denser than current pYIN without better continuity | Reject or shadow only |

## 6. Implementation Phases

### Phase 0 - Baseline and Documentation

Deliverables:

| Item | Output |
|---|---|
| Current-source documentation | This plan plus links to current API/extractor/frontend paths |
| Review set | 50-100 songs across vocal pop, duet/harmony, instrumental, jazz solo, piano intro, guitar riff, weak vocal, dense backing vocals |
| Baseline metrics | Current pYIN note count, density, coverage, continuity, obvious failure tags |

Exit gate:

| Gate | Requirement |
|---|---|
| Reproducibility | A fixed review list can be rerun and compared by hash |
| No product change | `/api/ai/melody` behavior remains unchanged |

### Phase 1 - Metadata and Debug Visibility

Deliverables:

| Item | Output |
|---|---|
| Melody source metadata | Add optional `melody_source` and `quality_flags` to finalized payloads |
| Admin/debug view | Show source id, algorithm, cache path, note count, density, and flags |
| Frontend label safety | Keep "Right hand (melody)" as a lane label, but do not imply vocal/instrument source unless metadata supports it |

Exit gate:

| Gate | Requirement |
|---|---|
| Compatibility | Existing frontend still works with only `melody[]` |
| Cache safety | Existing `V:\data\melodies` files are not invalidated |

### Phase 2 - Candidate Builder

Deliverables:

| Candidate | Implementation note |
|---|---|
| `full_mix_pyin` | Wrap current `MelodyExtractor` output as a named candidate |
| `vocal_basic_pitch` | Convert existing `hybrid_melody` / Basic Pitch output into melody JSON for comparison only |
| `vocal_rmvpe` | Add PC-side extractor if model/runtime is approved |
| `instrument_lead` | Start with full-mix harmonic pYIN plus optional Demucs `other`/harmonic route |
| `midi_aligned` | Read existing Phase 4 aligned JSON when present |

Exit gate:

| Gate | Requirement |
|---|---|
| Shadow completeness | Candidate files can be generated without changing player output |
| Observability | Candidate generation logs runtime, note count, failure reason, and cache path |

### Phase 3 - Quality Scoring and Resolver

Deliverables:

| Item | Output |
|---|---|
| `MelodyCandidateScore` | Shared scoring object with density, range, continuity, coverage, source prior, and flags |
| `MelodyResolver` | Chooses best candidate or falls back to `full_mix_pyin` |
| Golden review report | Per-song before/after diff with selected source and manual verdict |

Exit gate:

| Gate | Requirement |
|---|---|
| Manual review | At least 80% of reviewed songs are equal or better than current pYIN |
| Severe regression | No more than 10% of reviewed songs are clearly worse than current pYIN |
| Vocal songs | Main vocal source selection accuracy reaches at least 85% on vocal subset |
| Instrumental songs | Lead-line selection accuracy reaches at least 75% on instrumental/solo subset |

### Phase 4 - Shadow API Rollout

Deliverables:

| Item | Output |
|---|---|
| Feature flag | `ENABLE_RH_MELODY_RESOLVER=false` by default |
| Shadow endpoint | Optional `/api/ai/melody-v2` or debug query parameter for comparison |
| Background queue | Fresh uploads keep current pYIN response, then enqueue higher-quality candidate generation |

Exit gate:

| Gate | Requirement |
|---|---|
| Latency | Cached resolver read is effectively file-read speed; fresh fallback no slower than current pYIN path |
| Rollback | Turning the flag off immediately returns current full-mix pYIN behavior |
| Logs | Selection decisions are inspectable per hash |

### Phase 5 - Primary Promotion

Deliverables:

| Item | Output |
|---|---|
| `/api/ai/melody` integration | Resolver becomes primary read path behind production flag |
| Cache promotion | Selected results write to `melodies_rh_v2`, not directly over legacy pYIN |
| Admin controls | Force source, invalidate selected cache, compare candidates |

Exit gate:

| Gate | Requirement |
|---|---|
| Production sample | Public-mode QA on representative hashes passes waterfall, keyboard, jianpu, and practice modes |
| Operations | Backfill/queue progress is visible and restart-safe |
| Data safety | Legacy `melodies` cache remains available for fallback and comparison |

## 7. File-Level Work Plan

Likely backend additions:

| File | Change |
|---|---|
| `backend/ai/melody_resolver.py` | New source-aware resolver and cache priority logic |
| `backend/ai/melody_candidate.py` | Candidate schema and normalization helpers |
| `backend/ai/melody_quality.py` | Density/range/continuity/source-prior scoring |
| `backend/ai_api.py` | Route `/api/ai/melody` through resolver behind flag |
| `backend/process_queue.py` | Queue candidate generation after upload melody fallback |
| `backend/batch_hybrid_worker.py` or new PC worker | Generate precomputed vocal/instrument candidates |

Likely frontend additions:

| File | Change |
|---|---|
| `frontend/js/player.js` | Optionally expose source/debug metadata; no required change for legacy playback |
| Admin/debug pages | Candidate comparison, selected source, force/invalidate controls |

## 8. Validation Set

The first review set should deliberately include songs that current full-mix pYIN struggles with:

| Category | What it tests |
|---|---|
| Clean single vocal | Vocal route should beat or match pYIN |
| Quiet vocal over loud accompaniment | Source selection must avoid amplitude-only mistakes |
| Backing vocals above lead | Reject highest-pitch-wins artifacts |
| Duet / harmony | Prefer main line or stable lead, not alternating random voices |
| Piano intro before vocal | Preserve instrumental intro when musically important |
| Guitar/sax/synth instrumental lead | Select instrument lead, not empty vocal stem |
| Dense piano right hand | Avoid converting accompaniment texture into melody |
| Jam/backing tracks | Do not invent vocal melody from accompaniment |

Recommended review fields:

```text
hash
path
expected_source: vocal | instrument | midi | fallback
current_pyin_grade: good | ok | bad
resolver_grade: good | ok | bad
selected_source
failure_tag
review_note
```

## 9. Risks and Decisions

| Risk / decision | Plan |
|---|---|
| Demucs + Basic Pitch vocal path has known extras | Treat as shadow evidence unless quality gates pass |
| RMVPE runtime and dependency weight | Keep PC-side first; do not put heavy ML on NUC request path |
| Vocal-only can drop piano/guitar intros | Source scoring must allow section-aware or instrument-lead fallback |
| Cache version confusion | Never overwrite legacy `melodies` during rollout; write selected output to versioned cache |
| UI label confusion | Separate "RH melody lane" from "source = vocal/instrument/full mix" metadata |
| Manual review cost | Start with a small stratified set; expand only after the resolver beats pYIN on hard cases |

## 10. Immediate Next Steps

| Order | Work |
|---:|---|
| 1 | Build the review-list manifest and baseline current `full_mix_pyin` metrics |
| 2 | Add optional `melody_source` metadata to current `/api/ai/melody` output |
| 3 | Implement candidate wrapper for current pYIN and existing `hybrid_melody` outputs |
| 4 | Implement `melody_quality.py` scoring and a report command |
| 5 | Decide whether RMVPE is the approved Tier 2 tracker before starting large PC backfill |
