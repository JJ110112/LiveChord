import librosa
import numpy as np

class MelodyExtractor:
    """
    主旋律萃取器 (Melody Extractor)
    使用 librosa.pyin 演算法，從音檔中擷取 F0 (Fundamental Frequency)，
    將連續的頻率量化為離散的 MIDI 音符序列。
    這將成為 Viterbi 解碼器中的「觀測序列 (Observation)」。
    """
    def __init__(self, sr=22050, fmin="C4", fmax="C6"):
        self.sr = sr
        # 人聲旋律範圍：C4 (~262Hz) 到 C6 (~1046Hz)
        # C3 太低會抓到貝斯，C4 起步更精準
        self.fmin_hz = librosa.note_to_hz(fmin)
        self.fmax_hz = librosa.note_to_hz(fmax)
        
    def extract_melody(self, audio_path):
        """
        解析音檔並回傳音符區段。
        回傳格式: [{"start": 1.2, "end": 2.5, "note": "G4", "midi": 67}, ...]
        """
        print(f"Loading {audio_path}...")
        # 轉成單聲道，取樣率降低至 22050 節省記憶體與計算時間
        y, sr = librosa.load(audio_path, sr=self.sr, mono=True)
        
        # 高通濾波：移除 200Hz 以下的貝斯/鼓，只保留旋律頻段
        print(f"Pre-filtering bass frequencies...")
        y = librosa.effects.preemphasis(y, coef=0.97)

        # HPSS 分離：取諧波成分（旋律），去掉打擊成分（鼓）
        y_harmonic, _ = librosa.effects.hpss(y)

        print(f"Extracting F0 using pYIN...")
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y_harmonic,
            fmin=self.fmin_hz,
            fmax=self.fmax_hz,
            sr=sr,
            fill_na=None
        )
        
        times = librosa.times_like(f0, sr=sr)
        
        melody_events = []
        current_note = None
        current_midi = None
        start_time = 0.0
        
        # 為了避免雜音造成的頻繁切換，我們作簡單的音符聚合 (Quantization & Segmentation)
        for t, freq, voiced in zip(times, f0, voiced_flag):
            if voiced and not np.isnan(freq):
                midi_float = librosa.hz_to_midi(freq)
                midi_int = int(round(midi_float))
                note_name = librosa.midi_to_note(midi_int)
                
                if current_note != note_name:
                    # 音高發生變化，結算上一個音符
                    if current_note is not None:
                        # 忽略太短的雜訊 (小於 0.1 秒)
                        if t - start_time >= 0.1:
                            melody_events.append({
                                "start": round(float(start_time), 3),
                                "end": round(float(t), 3),
                                "note": current_note,
                                "midi": current_midi
                            })
                    current_note = note_name
                    current_midi = midi_int
                    start_time = t
            else:
                # 無聲或未發音
                if current_note is not None:
                    if t - start_time >= 0.1:
                        melody_events.append({
                            "start": round(float(start_time), 3),
                            "end": round(float(t), 3),
                            "note": current_note,
                            "midi": current_midi
                        })
                    current_note = None
                    
        # 補上最後一個音符
        if current_note is not None and len(times) > 0:
            end_t = times[-1]
            if end_t - start_time >= 0.1:
                melody_events.append({
                    "start": round(float(start_time), 3),
                    "end": round(float(end_t), 3),
                    "note": current_note,
                    "midi": current_midi
                })
                
        # 後處理：合併短暫中斷的相同音符、過濾異常跳躍
        cleaned = []
        for evt in melody_events:
            if cleaned and cleaned[-1]["note"] == evt["note"]:
                gap = evt["start"] - cleaned[-1]["end"]
                if gap < 0.15:  # 短暫中斷 < 150ms → 合併
                    cleaned[-1]["end"] = evt["end"]
                    continue
            # 過濾異常八度跳躍（前後超過 12 半音且持續 < 0.15s）
            if cleaned and abs(evt["midi"] - cleaned[-1]["midi"]) > 12:
                dur = evt["end"] - evt["start"]
                if dur < 0.15:
                    continue  # 跳過瞬間的八度錯誤
            cleaned.append(evt)

        return cleaned

if __name__ == "__main__":
    import json
    import sys
    
    if len(sys.argv) > 1:
        # CLI 測試
        test_file = sys.argv[1]
        extractor = MelodyExtractor()
        try:
            results = extractor.extract_melody(test_file)
            print("--- Extraction Complete ---")
            print(f"Total Melody Segments: {len(results)}")
            # 印出前 10 個音符確認
            print(json.dumps(results[:10], indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Usage: python backend/ai/melody_extractor.py <path_to_audio_file>")
