"""
主旋律萃取器 (Melody Extractor) — Phase 11b 升級版

升級內容:
  1.1 Onset Detection 節拍對齊 — librosa.onset + pYIN 雙重證據
  1.2 Vibrato 穩態量化 — 滑動窗口中位數濾波
  1.3 頻率範圍自適應 — 先粗掃再精掃
  1.4 置信度輸出 — 每個音符附帶 confidence

學術依據:
  - Mauch & Dixon (2014). "pYIN: A Fundamental Frequency Estimator." ICASSP.
  - Böck & Widmer (2013). "Maximum Filter Vibrato Suppression." DAFx.
  - de Cheveigné & Kawahara (2002). "YIN." JASA, 111(4).
"""

import librosa
import numpy as np
from scipy.ndimage import median_filter


class MelodyExtractor:
    """
    主旋律萃取器 — Phase 11b 升級版

    改進:
      - onset_detect 輔助切割（快速經過音/裝飾音/同音反覆）
      - vibrato 中位數濾波（消除 ±50 cent 震盪雜訊）
      - 頻率範圍自適應（不再硬編碼 C4-C6）
      - confidence 輸出（voiced_prob 平均值）
    """

    def __init__(self, sr=22050, fmin="C3", fmax="C7",
                 min_note_dur=0.08, gap_merge=0.15,
                 vibrato_window=5, adaptive_range=True,
                 fast_mode=False):
        """
        fast_mode: 批次用高速模式 — 關閉 adaptive_range + onset detection，
                   保留 vibrato filter (低成本)。速度約提升 40-50%。
        """
        self.sr = sr
        self.fmin_hz = librosa.note_to_hz(fmin)
        self.fmax_hz = librosa.note_to_hz(fmax)
        self.min_note_dur = min_note_dur
        self.gap_merge = gap_merge
        self.vibrato_window = vibrato_window
        self.adaptive_range = adaptive_range if not fast_mode else False
        self.fast_mode = fast_mode

    def extract_melody(self, audio_path):
        """
        解析音檔並回傳音符區段。

        回傳格式:
        [
          {
            "start": 1.2,
            "end": 2.5,
            "note": "G4",
            "midi": 67,
            "confidence": 0.85   # NEW: voiced probability 平均值
          },
          ...
        ]
        """
        if not self.fast_mode:
            print(f"Loading {audio_path}...")
        y, sr = librosa.load(audio_path, sr=self.sr, mono=True)

        # --- 前處理 ---
        if not self.fast_mode:
            print("Pre-filtering...")
        y = librosa.effects.preemphasis(y, coef=0.97)
        y_harmonic, _ = librosa.effects.hpss(y)

        # --- 1.3 自適應頻率範圍 ---
        fmin_hz, fmax_hz = self._adaptive_range(y_harmonic, sr) if self.adaptive_range \
            else (self.fmin_hz, self.fmax_hz)

        # --- pYIN F0 提取 ---
        if not self.fast_mode:
            print(f"Extracting F0 (pYIN, {librosa.hz_to_note(fmin_hz)}-{librosa.hz_to_note(fmax_hz)})...")
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y_harmonic,
            fmin=fmin_hz,
            fmax=fmax_hz,
            sr=sr,
            fill_na=None,
        )
        times = librosa.times_like(f0, sr=sr)

        # --- 1.2 Vibrato 穩態量化 ---
        f0_stable = self._vibrato_filter(f0, voiced_flag)

        # --- 1.1 Onset Detection (skip in fast_mode) ---
        onset_times = set() if self.fast_mode else self._detect_onsets(y_harmonic, sr)

        # --- 音符分割 (結合 pYIN + onset) ---
        melody_events = self._segment_notes(
            times, f0_stable, voiced_flag, voiced_probs, onset_times
        )

        # --- 後處理 ---
        melody_events = self._post_process(melody_events)

        return melody_events

    # ==================================================================
    # 1.1 Onset Detection
    # ==================================================================
    def _detect_onsets(self, y_harmonic, sr):
        """偵測 onset 時間點，作為音符邊界的輔助證據。"""
        onset_env = librosa.onset.onset_strength(y=y_harmonic, sr=sr)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr,
            backtrack=True,
        )
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)
        return set(round(float(t), 3) for t in onset_times)

    # ==================================================================
    # 1.2 Vibrato 穩態量化
    # ==================================================================
    def _vibrato_filter(self, f0, voiced_flag):
        """
        對 voiced 區段做中位數濾波，消除 vibrato 造成的 ±50 cent 震盪。
        """
        f0_copy = f0.copy()

        # 只對有聲區段做濾波
        voiced_mask = voiced_flag & ~np.isnan(f0_copy)
        if np.sum(voiced_mask) < self.vibrato_window:
            return f0_copy

        # 將 f0 轉成 MIDI (log domain) 再做中位數濾波
        midi_f0 = np.full_like(f0_copy, np.nan)
        midi_f0[voiced_mask] = librosa.hz_to_midi(f0_copy[voiced_mask])

        # 對 voiced 區段做 median filter
        # 先填補 nan（用前後值），濾波後恢復
        midi_filled = np.copy(midi_f0)
        # forward fill
        for i in range(1, len(midi_filled)):
            if np.isnan(midi_filled[i]) and not np.isnan(midi_filled[i-1]):
                midi_filled[i] = midi_filled[i-1]
        # backward fill
        for i in range(len(midi_filled)-2, -1, -1):
            if np.isnan(midi_filled[i]) and not np.isnan(midi_filled[i+1]):
                midi_filled[i] = midi_filled[i+1]

        if not np.all(np.isnan(midi_filled)):
            midi_filtered = median_filter(midi_filled, size=self.vibrato_window)
            # 只把 voiced 部分寫回
            f0_copy[voiced_mask] = librosa.midi_to_hz(midi_filtered[voiced_mask])

        return f0_copy

    # ==================================================================
    # 1.3 頻率範圍自適應
    # ==================================================================
    def _adaptive_range(self, y_harmonic, sr):
        """
        先做全頻段粗掃，找到能量最集中的頻率帶，再設定精掃範圍。
        """
        # 快速粗掃: 寬範圍 pYIN
        try:
            f0_coarse, voiced_coarse, _ = librosa.pyin(
                y_harmonic,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=sr,
                fill_na=None,
            )
        except Exception:
            return self.fmin_hz, self.fmax_hz

        # 取有效 f0 值
        valid = f0_coarse[voiced_coarse & ~np.isnan(f0_coarse)]
        if len(valid) < 20:
            return self.fmin_hz, self.fmax_hz

        # 取 5th ~ 95th percentile 作為有效範圍
        p5 = np.percentile(valid, 5)
        p95 = np.percentile(valid, 95)

        # 往外擴展半個八度的安全邊界
        fmin_adaptive = max(p5 / 1.5, librosa.note_to_hz("C2"))
        fmax_adaptive = min(p95 * 1.5, librosa.note_to_hz("C7"))

        # 確保至少覆蓋兩個八度
        if fmax_adaptive / fmin_adaptive < 4.0:
            center = (fmin_adaptive + fmax_adaptive) / 2
            fmin_adaptive = center / 2
            fmax_adaptive = center * 2

        return float(fmin_adaptive), float(fmax_adaptive)

    # ==================================================================
    # 音符分割 (結合 pYIN + onset)
    # ==================================================================
    def _segment_notes(self, times, f0, voiced_flag, voiced_probs, onset_times):
        """
        將 F0 序列分割成離散音符。

        改進: onset 發生時強制切割（即使 pitch 未變 → 同音反覆）。
        """
        events = []
        current_note = None
        current_midi = None
        start_time = 0.0
        conf_accum = []  # 收集 voiced_prob 用於計算 confidence

        for i, (t, freq, voiced) in enumerate(zip(times, f0, voiced_flag)):
            t_rounded = round(float(t), 3)

            if voiced and not np.isnan(freq):
                midi_float = librosa.hz_to_midi(freq)
                midi_int = int(round(midi_float))
                note_name = librosa.midi_to_note(midi_int)

                # 檢查是否有 onset 觸發切割
                onset_hit = t_rounded in onset_times

                pitch_changed = (current_note != note_name)
                # onset + 同音 → 同音反覆 (re-articulation)
                force_cut = onset_hit and current_note is not None and not pitch_changed

                if pitch_changed or force_cut:
                    # 結算上一個音符
                    if current_note is not None:
                        dur = t - start_time
                        if dur >= self.min_note_dur:
                            avg_conf = float(np.mean(conf_accum)) if conf_accum else 0.5
                            events.append({
                                "start": round(float(start_time), 3),
                                "end": round(float(t), 3),
                                "note": current_note,
                                "midi": current_midi,
                                "confidence": round(avg_conf, 3),
                            })
                    current_note = note_name
                    current_midi = midi_int
                    start_time = t
                    conf_accum = [float(voiced_probs[i])] if voiced_probs[i] is not None else [0.5]
                else:
                    # 繼續累積同一音符
                    if voiced_probs[i] is not None:
                        conf_accum.append(float(voiced_probs[i]))
            else:
                # 無聲/未發音
                if current_note is not None:
                    dur = t - start_time
                    if dur >= self.min_note_dur:
                        avg_conf = float(np.mean(conf_accum)) if conf_accum else 0.5
                        events.append({
                            "start": round(float(start_time), 3),
                            "end": round(float(t), 3),
                            "note": current_note,
                            "midi": current_midi,
                            "confidence": round(avg_conf, 3),
                        })
                    current_note = None
                    conf_accum = []

        # 補上最後一個音符
        if current_note is not None and len(times) > 0:
            end_t = times[-1]
            dur = end_t - start_time
            if dur >= self.min_note_dur:
                avg_conf = float(np.mean(conf_accum)) if conf_accum else 0.5
                events.append({
                    "start": round(float(start_time), 3),
                    "end": round(float(end_t), 3),
                    "note": current_note,
                    "midi": current_midi,
                    "confidence": round(avg_conf, 3),
                })

        return events

    # ==================================================================
    # 後處理
    # ==================================================================
    def _post_process(self, events):
        """合併短暫中斷的相同音符 + 過濾異常跳躍。"""
        if not events:
            return events

        cleaned = []
        for evt in events:
            if cleaned and cleaned[-1]["note"] == evt["note"]:
                gap = evt["start"] - cleaned[-1]["end"]
                if gap < self.gap_merge:
                    # 合併: 取加權平均 confidence
                    old_dur = cleaned[-1]["end"] - cleaned[-1]["start"]
                    new_dur = evt["end"] - evt["start"]
                    total_dur = old_dur + new_dur
                    if total_dur > 0:
                        cleaned[-1]["confidence"] = round(
                            (cleaned[-1]["confidence"] * old_dur +
                             evt["confidence"] * new_dur) / total_dur, 3
                        )
                    cleaned[-1]["end"] = evt["end"]
                    continue

            # 過濾異常八度跳躍（超過 12 半音且持續 < 0.15s）
            if cleaned and abs(evt["midi"] - cleaned[-1]["midi"]) > 12:
                dur = evt["end"] - evt["start"]
                if dur < 0.15:
                    continue

            cleaned.append(evt)

        return cleaned


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        extractor = MelodyExtractor()
        try:
            results = extractor.extract_melody(test_file)
            print("--- Extraction Complete ---")
            print(f"Total Melody Segments: {len(results)}")

            # 統計
            if results:
                confs = [e["confidence"] for e in results]
                midis = [e["midi"] for e in results]
                print(f"Confidence: mean={np.mean(confs):.3f}, "
                      f"min={np.min(confs):.3f}, max={np.max(confs):.3f}")
                print(f"Range: {librosa.midi_to_note(min(midis))} - "
                      f"{librosa.midi_to_note(max(midis))}")
                # 低置信度音符比例
                low_conf = sum(1 for c in confs if c < 0.5) / len(confs)
                print(f"Low confidence (<0.5): {low_conf:.1%}")

            print(json.dumps(results[:10], indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("Usage: python -m ai.melody_extractor <path_to_audio_file>")
