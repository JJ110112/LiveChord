# RH Melody Optimization Plan

**Date**: 2026-05-20 (revised after critical review)
**Status**: planning
**Tracking**: `LiveChord-jqnm`, `LiveChord-nfdz`

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

The optimization goal is not "replace pYIN with Basic Pitch" or "always use vocals". The goal is a **source-aware melody resolver** that:

1. First, classifies the song into a small set of song types (vocal pop, solo piano, jazz/multi-solo, MR/karaoke, ambient/no-lead, mixed-section)
2. Routes each song type to an extractor path designed for that type
3. Keeps the existing full-mix pYIN path as a guaranteed fallback
4. Exposes enough metadata that the UI and admin tools can see what source was selected, and is allowed to return "no melody" as a confident outcome

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
6. A transparent source selection, so the UI and debugging tools can distinguish `full_mix_pyin`, `vocal_stem`, `solo_piano_polyphonic`, `instrument_lead`, `midi_aligned`, and fallback results.

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
| Directly promote Basic Pitch V2 as a universal replacement | Prior shadow analysis showed polyphonic extras are architectural for vocal songs; but Basic Pitch is the **right tool for solo piano** specifically (its polyphony is a feature, not a bug, when paired with top-voice post-filtering) |
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
| 1 | `midi_aligned` | Curated MIDI (rare for vocal — usually a fallback miss) |
| 2 | `vocal_stem_f0` | Demucs vocals + pitch tracker (pYIN or RMVPE if approved) |
| 3 | `full_mix_pyin` | Existing baseline |

**Solo piano** (classical, jazz piano, lo-fi piano):

| Priority | Candidate | Source |
|---:|---|---|
| 1 | `midi_aligned` | Curated MIDI — high hit rate for this sub-type |
| 2 | `solo_piano_polyphonic` | Basic Pitch + top-voice-over-time post-filter |
| 3 | `full_mix_pyin` | Fallback only — known to fail on polyphonic piano |

**Instrumental solo** (jazz combo, fingerstyle, sax/guitar lead):

| Priority | Candidate | Source |
|---:|---|---|
| 1 | `midi_aligned` | If available |
| 2 | `instrument_lead` | Demucs `other` stem + pYIN (only if Demucs precompute is committed; see §4.2) |
| 3 | `full_mix_pyin` | Fallback |

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

### 4.2 `instrument_lead` honesty clause

The previous draft listed `instrument_lead` as a candidate but described it as "full-mix harmonic pYIN plus optional Demucs other/harmonic route". The first half is identical to the current path — it would be relabeling, not a new technique. This plan commits explicitly:

- **`instrument_lead` requires Demucs `other` stem precompute.** If Phase 2 does not commit Demucs precompute on PC-side, this candidate is **dropped**, not silently relabeled. Resolver simply falls through to `full_mix_pyin` for the instrumental-solo sub-type, with `quality_flags: ["instrument_lead_unavailable"]`.

### 4.3 Cache layout

```text
V:\data\melodies\<hash>.json                         # existing full-mix pYIN, keep stable
V:\data\melody_candidates\<hh>\<hash>\full_mix_pyin.json
V:\data\melody_candidates\<hh>\<hash>\vocal_stem_f0.json
V:\data\melody_candidates\<hh>\<hash>\solo_piano_polyphonic.json
V:\data\melody_candidates\<hh>\<hash>\instrument_lead.json
V:\data\melodies_rh_v2\<hh>\<hash>.json              # selected resolver output, behind flag
V:\data\midi_aligned\<hh>\<hash>.json                # curated Tier 1 output
V:\data\melodies_v4\<hh>\<hash>.json                 # existing Phase 4 precomputed target
```

`<hh>` = first 2 chars of `<hash>`. **All new caches use the same sharding scheme as the chord-JSON migration (`abe6172`).** Flat-directory layouts (the previous draft's design) would put 100k+ files on SMB and regress the perf win we already paid for.

### 4.4 Response shape (additive, schema v2)

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

### 4.5 Deployment scope (server-mode matters)

`/api/ai/melody` runs on three modes with very different constraints. The resolver must be explicit about what runs where:

| Mode | Resolver behavior |
|---|---|
| **NUC `personal`** (LAN, 8800) | Full resolver. Demucs / Basic Pitch / RMVPE precompute on PC, candidates synced to V:\ |
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

## 6. Implementation Phases

### Phase 0 - Failure-mode survey and metadata visibility

The previous draft put "baseline metrics" first and added metadata second. This is reversed: metadata is the cheap, no-risk change, and is needed for the survey to be reproducible.

Deliverables:

| Item | Output |
|---|---|
| Add `melody_source` + `quality_flags` to `/api/ai/melody` (additive, optional) | Survey can record per-song selected source |
| Admin/debug view shows current source + flags | Reproducible inspection; `quality_flags` should be visible in Phase 0, not after resolver tuning |
| **Failure-mode taxonomy is frozen before listening review starts** | One primary tag per song/segment, optional secondary flags; avoids drifting standards across 200 songs |
| **Failure-mode survey on 200 equal-probability random reviewable songs** | Tagged distribution of failures: `pyin_fine / wrong_octave / bass_leakage / wrong_line_backing_vocal / accompaniment_chord_tone / accompaniment_riff_lead / sparse_threshold / sparse_genuine_silence / duet_alternating / solo_piano_polyphonic_collapse / no_lead_present / audio_quality / no_issue_audible`; no subtype stratification, to avoid biasing the observed current fail rate |
| Public reference dataset hook | Run current pYIN against MedleyDB-Melody / MIR-1K subset (≥20 songs), record RPA/RCA against ground truth |

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

The script scans sharded chord cache files, keeps reviewable songs with resolvable audio by default, samples with equal probability, and writes `data/melody_reviews/phase0_survey_queue.jsonl` plus `phase0_survey_queue.summary.json`. `--chords-root` defaults to `LIVECHORD_CHORDS_DIR`, then `V:\data\chords` when present; repo-local `data/chords` is only a development fallback and prints a warning. Review tags are written through `POST /api/ai/melody/debug/tag` into `data/melody_reviews/phase0_tags.jsonl`; the endpoint reuses `/api/ai/melody/debug` metadata, validates the frozen taxonomy, stores the reviewer, `survey_id`, `failure_tag`, `secondary_flags`, `audio_quality_note`, optional segment, machine proxies, and current melody stats.

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
| `vocal_stem_f0` | Demucs vocals + pYIN (RMVPE deferred unless explicitly approved as Tier 2 tracker; not auto-added) |
| `solo_piano_polyphonic` | Basic Pitch + top-voice post-filter — **piano sub-type only**, not a universal candidate |
| `instrument_lead` | Demucs `other` stem + pYIN — **only if Demucs precompute is committed**, else dropped per §4.2 |
| `midi_aligned` | Read existing Phase 4 aligned JSON when present |

Exit gate:

| Gate | Requirement |
|---|---|
| Shadow completeness | Candidate files can be generated without changing player output |
| Observability | Candidate generation logs runtime, note count, failure reason, and cache path |
| Sharded layout | New caches written to `<hh>/<hash>/` per §4.3, not flat |

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
| `backend/ai_api.py` | Route `/api/ai/melody` through resolver behind flag; bypass in public mode |
| `backend/process_queue.py` | Queue candidate generation after upload melody fallback (personal mode only) |
| `backend/batch_hybrid_worker.py` or new PC worker | Generate precomputed vocal/solo-piano/instrument candidates |

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
expected_source: vocal_stem_f0 | solo_piano_polyphonic | instrument_lead | midi_aligned | full_mix_pyin | no_lead
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
| Demucs + Basic Pitch vocal path has known extras | Treat as shadow evidence; vocal route uses Demucs vocal stem + pYIN, not Basic Pitch, unless explicitly approved |
| RMVPE runtime and dependency weight | Keep PC-side only; do not put heavy ML on NUC request path or VPS at all |
| Vocal-only can drop piano/guitar intros | Acknowledged as v1 weakness; mixed-section songs may be tagged `quality_flags: ["mixed_section_single_source"]`. Section-aware switching is §11 future scope |
| Cache version confusion | Never overwrite legacy `melodies` during rollout; write selected output to versioned `melodies_rh_v2`; `schema_version` stays at 2, new fields are additive metadata |
| UI label confusion | Separate "RH melody lane" from "source = vocal/solo-piano/instrument/full mix" metadata |
| Manual review cost | Stratified set (§8) fixed up-front; machine proxies (§6 Phase 3) reduce single-annotator dependence |
| Manual review still takes too long | Phase 3 requires automated A/B diff replay, so review focuses on windows where resolver and pYIN disagree instead of full-song listening |
| Audio quality masquerades as algorithm failure | Phase 0 taxonomy includes `audio_quality`; resolver confidence should be reduced rather than overfitting a new extractor to bad source audio |
| `instrument_lead` becomes a relabel of `full_mix_pyin` | §4.2 commits the candidate is dropped if Demucs precompute is not committed — no silent relabeling |
| Genre/library prior overfits to user's folder layout | Demoted to ≤5% scoring weight; never a routing gate |
| Hand-tuned scoring regresses songs that were fine | Conservative decision rule (§5.1) — resolver only switches when candidate beats fallback by `MIN_MARGIN` and passes hard gates |
| Score-render quantization mismatch | Phase 4 exit gate explicitly verifies VexFlow rendering after extractor swap |
| VPS accidentally runs resolver | §4.5 + Phase 5 exit gate — resolver bypassed when `LIVECHORD_MODE=public` |
| Plan becomes a perpetual epic alongside neural_arranger | §6 Phase 5 includes a hard "definition of done" — 3-month admin-override usage rate |
| Phase 4 hybrid_melody / `melodies_v4` is itself unshipped | `midi_aligned` Tier is gated on Phase 4 landing; if Phase 4 stays POC, resolver routes around it transparently |

## 10. Immediate Next Steps

| Order | Work |
|---:|---|
| 1 | Freeze the Phase 0 failure-mode taxonomy before reviewing the 200-song sample; yes, this happens before listening review starts |
| 2 | Add optional `melody_source` + `quality_flags` metadata to current `/api/ai/melody` output (Phase 0, no behavior change) |
| 3 | Visualize source id, `quality_flags`, note density, and failure tag in Admin/debug UI immediately, so resolver weights can be tuned from inspected evidence |
| 4 | Build stratified review-list manifest per §8 (sub-type counts fixed) |
| 5 | Run current `full_mix_pyin` against the manifest + a MedleyDB-Melody / MIR-1K subset; record RPA/RCA + failure-mode tags |
| 6 | **Decision branch**: classify failure modes. If >50% are post-filter-fixable, Phase 1 is the entire next sprint (post-filters only); if octave-jump dominates, prioritize the octave-fold filter first; if not, proceed to Phase 2 candidate builder |
| 7 | If proceeding to Phase 2: decide on Demucs commitment (yes → `vocal_stem_f0` + `instrument_lead` are real candidates; no → drop `instrument_lead`, vocal candidate uses only what's already available via `batch_hybrid_worker`) |
| 8 | If proceeding to Phase 2: confirm Basic Pitch + top-voice post-filter is approved as the solo-piano path before any PC backfill |

## 11. Explicit Future Scope (v2+)

The plan deliberately does not attempt these in v1. They are listed so they aren't smuggled in via scope creep:

| Future item | Why deferred |
|---|---|
| **Section-aware source switching** | Requires reliable section detection. CLAUDE.md notes `section_detect` is on rule-based fallback for ~82% of the library. Building source-routing on top of unreliable section boundaries compounds failure. Revisit after section detection improves (Phase 3 of [doc/PHASE_4_HYBRID_MELODY.md](../PHASE_4_HYBRID_MELODY.md)) |
| Multi-source blended output | Same as above — needs reliable section boundaries to splice sources without audible seams |
| User per-song source override (UI control) | Admin override exists in Phase 5; user-facing override is a separate UX decision |
| RMVPE as approved Tier 2 tracker | Deferred until runtime / dependency cost is evaluated against pYIN-on-vocal-stem |
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
