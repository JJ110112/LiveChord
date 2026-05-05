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
