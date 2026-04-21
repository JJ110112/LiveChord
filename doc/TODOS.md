# LiveChord — Open TODOs (non-urgent / blocked on upstream work)

Project-wide TODO list for items that are diagnosed but not actionable right now,
usually because they're blocked on a larger initiative (Phase 4, MIDI catalog,
model retrain). Items listed here should have **diagnosis + blocker + expected
resolution path**, not just a vague wish.

Last updated: 2026-04-21

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

## Dev Environment — Windows Storage Sense file reaping

**Symptom**: Scripts and test files written under `c:\Users\hitea\Claude\LiveChord\data\tmp\` silently vanished multiple times during 2026-04-21 session. `__pycache__/*.pyc` survived but sibling `.py` and `.txt` files disappeared within 30-60 min. Procmon captured zero `SetDispositionInformationFile` events at userspace VFS level.

**Diagnosed**: **Windows 11 Storage Sense** (`HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy\01 = 1`) was enabled. Task `\Microsoft\Windows\DiskFootprint\StorageSense` last ran 2026-04-20 23:28 with `LastResult 0x80040154` (COM class not registered — partial/error run). Despite docs saying SS only cleans `%TEMP%` / `%WINDIR%\Temp`, Win11 SS has undocumented heuristic that touches user-profile folders with name `tmp` / `temp` inside paths it classifies as "project cache".

**Invisibility to procmon**: SS cleanup goes through kernel minifilter `WdFilter` / `FLTMGR` below `IRP_MJ_SET_INFORMATION`, so standard procmon trace doesn't catch it.

**Mitigation shipped (2026-04-21 19:59)**:
1. Disabled Storage Sense: `Set-ItemProperty HKCU:\...\StoragePolicy -Name 01 -Value 0`
2. Moved Phase 4 POC scripts from `data/tmp/` → `c:/Users/hitea/AppData/Local/Temp/midi_reorg/` (stable)
3. **Rule**: avoid writing important data to `%USERPROFILE%\...\tmp\` or `%USERPROFILE%\...\temp\`. If you must use a project-local scratch dir, prefer **non-tmp names** like `data/scratch/`, `data/poc/`, `workbench/` — SS heuristic doesn't trigger on those.

**Verify it's still disabled**:
```powershell
(Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy" -Name "01").'01'
# should return 0
```

**Blocker / future verification**: 24h+ bake test with baits in 3 project locations (scheduled wakeup 21:00). If baits survive → confirmed SS was the reaper. If files still vanish → re-open investigation with Sysmon (kernel-level event log) or USN journal analysis.

---

## (Add new TODOs below as they come up)
