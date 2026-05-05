# Audio Sample Credits

LiveChord ships local sample sets so the AI accompaniment synth runs
without a CDN dependency. Each subdirectory is one instrument; this
file records the source and license for every set.

## grand-piano/

**Salamander Grand Piano V3** by Alexander Holm.
Licensed under [CC-BY-3.0](https://creativecommons.org/licenses/by/3.0/).

Sources:
- Original 16-bit/44.1 kHz multi-velocity samples: <https://github.com/sfzinstruments/SalamanderGrandPiano>
- Mirrored 128 kbps mono MP3s (the set we ship): <https://tonejs.github.io/audio/salamander/>

Sampled at every 3 semitones (MIDI 21–108); pitch-shifted between
sample points via `BufferSourceNode.playbackRate`.

## nylon-guitar/, steel-guitar/, organ/, accordion/

Sourced from **[Tonejs-Instruments](https://github.com/nbrosowsky/tonejs-instruments)** (Niko Brosowsky), curated multi-sample CC-BY-3.0 instruments aimed at Tone.js but format-compatible with our SampleSynth.

Mappings:
- `nylon-guitar/`  ← `samples/guitar-nylon/`     (29 notes, MIDI 35–82)
- `steel-guitar/`  ← `samples/guitar-acoustic/`  (37 notes, MIDI 38–74)
- `organ/`         ← `samples/organ/`            (21 notes, MIDI 24–84)
- `accordion/`     ← `samples/harmonium/`        (38 notes, MIDI 36–74; harmonium chosen as the closest CC-BY accordion-family stand-in)

All redistributable under the same CC-BY-3.0 with attribution to the
original sample set authors enumerated in the upstream repo's README.

## upright-piano/, rhodes/, wurlitzer/, synth-pad/

No local samples shipped — these voices are produced procedurally by
SampleSynth's oscillator engine (Web Audio `OscillatorNode` with an
ADSR envelope, optionally sweetened by a bell-harmonic mix-in for
Rhodes/Wurlitzer or a tremolo LFO). They sit in the manifest as
oscillator-type entries; future commits may swap them for real
sample sets.
