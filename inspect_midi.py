import mido

files = ['midi/Samplab_Get It On.mid', 'midi/Track_Arpeggio_L1.mid']
with open('output.txt', 'w', encoding='utf-8') as out:
    for f in files:
        try:
            mid = mido.MidiFile(f)
            out.write(f"=== {f} ===\n")
            out.write(f"Tracks: {len(mid.tracks)}, Ticks per beat: {mid.ticks_per_beat}\n")
            for i, track in enumerate(mid.tracks):
                note_ons = [msg for msg in track if msg.type == 'note_on' and msg.velocity > 0]
                name = next((msg.name for msg in track if msg.type == 'track_name'), 'Unknown')
                out.write(f"  Track {i} ({name}): {len(note_ons)} notes\n")
            
            # Print the first 5 notes of track 0 (or useful track) to see the content details
            for idx, track in enumerate(mid.tracks):
                notes = [msg for msg in track if msg.type in ('note_on', 'note_off')]
                if len(notes) > 0:
                    out.write(f"  First 5 notes of Track {idx}:\n")
                    for msg in notes[:10]:
                        out.write(f"    {msg}\n")
                    break

        except Exception as e:
            out.write(f"Error reading {f}: {e}\n")
