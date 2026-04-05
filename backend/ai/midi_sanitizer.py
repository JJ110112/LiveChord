import os
import mido
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Basic music theory mappings to parse common chord symbols (e.g. "C:maj", "D:min")
# For a full implementation, we would integrate LiveChord's chord_table.py here.
NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
NOTE_TO_PITCH = {note: i for i, note in enumerate(NOTES)}

# Simplified mapping for basic chords relative to root
CHORD_INTERVALS = {
    'maj': [0, 4, 7],
    'min': [0, 3, 7],
    'dim': [0, 3, 6],
    'aug': [0, 4, 8],
    'maj7': [0, 4, 7, 11],
    'min7': [0, 3, 7, 10],
    '7': [0, 4, 7, 10]
}

class MidiSanitizer:
    """
    Sanitizes raw MIDI files transcribed by AI, snapping notes to the nearest valid chord tone.
    """
    def __init__(self):
        pass

    def _parse_chord_symbol(self, chord_symbol: str) -> List[int]:
        """
        Parses a chord symbol like 'C:maj' or 'D#:min7' and returns the pitch classes [0-11]
        """
        symbol = chord_symbol.replace('N', '') # handle no chord
        if not symbol:
            return []
            
        parts = symbol.split(':')
        root_str = parts[0]
        quality = parts[1] if len(parts) > 1 else 'maj'
        
        # Flattened notes normalize: Db -> C#
        if len(root_str) > 1 and root_str[1] == 'b':
            root_idx = NOTE_TO_PITCH.get(root_str[0], 0) - 1
            if root_idx < 0: root_idx = 11
        else:
            root_idx = NOTE_TO_PITCH.get(root_str, 0)
            
        intervals = CHORD_INTERVALS.get(quality, [0, 4, 7]) # default to major
        return [(root_idx + interval) % 12 for interval in intervals]

    def _get_chord_at_time(self, time_sec: float, chords: List[Dict[str, Any]]) -> str:
        """ Find the active chord symbol at a given timestamp """
        for chord in chords:
            start = chord.get('time', chord.get('start_time', 0))
            end = chord.get('end', chord.get('end_time', 9999))
            if start <= time_sec <= end:
                return chord.get('chord', 'N')
        return 'N'

    def _snap_pitch(self, pitch: int, valid_pitch_classes: List[int]) -> int:
        """ Shifting the pitch to the closest pitch class from the valid list """
        if not valid_pitch_classes:
            return pitch # Do not snap if no chord

        pitch_class = pitch % 12
        if pitch_class in valid_pitch_classes:
            return pitch

        # Find closest valid pitch class
        min_dist = 12
        best_pc = pitch_class
        for vpc in valid_pitch_classes:
            dist1 = abs(pitch_class - vpc)
            dist2 = 12 - dist1
            dist = min(dist1, dist2)
            if dist < min_dist:
                min_dist = dist
                best_pc = vpc
        
        # Adjust original pitch
        diff = best_pc - pitch_class
        if diff > 6:
            diff -= 12
        elif diff < -6:
            diff += 12
            
        return pitch + diff

    def _sanitize(self, raw_midi_path: str, chords: List[Dict[str, Any]], out_midi_path: str,
                   pitch_low: int, pitch_high: int):
        """
        Reads raw_midi_path, snaps all notes to the closest chord tone specified in `chords`,
        and writes the result to out_midi_path.
        """
        try:
            mid = mido.MidiFile(raw_midi_path)
        except Exception as e:
            logger.error(f"Failed to open raw MIDI {raw_midi_path}: {e}")
            return False

        out_mid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
        
        for i, track in enumerate(mid.tracks):
            out_track = mido.MidiTrack()
            out_mid.tracks.append(out_track)
            
            # Absolute time tracking to find chord
            abs_ticks = 0
            
            for msg in track:
                abs_ticks += msg.time
                original_time = msg.time
                
                # Convert ticks to seconds. Basic Pitch usually produces fixed tempo (e.g. 120bpm)
                # But here we approximate or we need Tempo map. Assuming fixed 120 BPM for now from Basic Pitch.
                time_sec = mido.tick2second(abs_ticks, mid.ticks_per_beat, mido.bpm2tempo(120))
                
                if msg.type in ['note_on', 'note_off']:
                    active_chord = self._get_chord_at_time(time_sec, chords)
                    valid_pitch_classes = self._parse_chord_symbol(active_chord)
                    
                    if valid_pitch_classes and active_chord != 'N':
                        new_pitch = self._snap_pitch(msg.note, valid_pitch_classes)
                        
                        # Constrain to target range
                        while new_pitch > pitch_high:
                            new_pitch -= 12
                        while new_pitch < pitch_low:
                            new_pitch += 12
                            
                        # Avoid boundary issues
                        new_pitch = max(0, min(127, new_pitch))
                        
                        out_msg = msg.copy(note=new_pitch)
                        out_track.append(out_msg)
                    else:
                        out_track.append(msg.copy())
                else:
                    out_track.append(msg.copy())

        try:
            out_mid.save(out_midi_path)
            logger.info(f"Sanitized MIDI saved to {out_midi_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save sanitized MIDI {out_midi_path}: {e}")
            return False

    def sanitize_bass(self, raw_midi_path: str, chords: List[Dict[str, Any]], out_midi_path: str):
        """Snap bass notes to chord tones, constrained to E1-G3 (28-55)."""
        return self._sanitize(raw_midi_path, chords, out_midi_path, pitch_low=28, pitch_high=55)

    def sanitize_melody(self, raw_midi_path: str, chords: List[Dict[str, Any]], out_midi_path: str):
        """Snap vocal/melody notes to chord tones, constrained to C3-C6 (48-84)."""
        return self._sanitize(raw_midi_path, chords, out_midi_path, pitch_low=48, pitch_high=84)

if __name__ == "__main__":
    # Test stub
    logging.basicConfig(level=logging.INFO)
    sanitizer = MidiSanitizer()
    fake_chords = [{"start_time": 0.0, "end_time": 10.0, "chord": "C:maj"}]
    # synthesizer.sanitize_bass("raw.mid", fake_chords, "sanitized.mid")
