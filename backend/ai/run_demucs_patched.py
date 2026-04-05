import sys
import os
import soundfile as sf
import demucs.audio

def custom_save_audio(wav, path, samplerate, bitrate=320, clip='rescale', bits_per_sample=24, as_float=False, **kwargs):
    # wav is a PyTorch tensor of shape [channels, time]
    # We transpose to [time, channels] for soundfile
    wav_np = wav.cpu().numpy().T
    sf.write(str(path), wav_np, samplerate)

# Inject monkey patch
demucs.audio.save_audio = custom_save_audio

from demucs.separate import main
if __name__ == '__main__':
    # Add dummy first arg if needed, sys.argv is handled by main()
    main()
