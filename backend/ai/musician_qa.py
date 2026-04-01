"""
樂手視角 QA (Musician Playability QA)
用來評測 Jazzify 和弦序列在真實世界(鍵盤/吉他)上的「可演奏性」(Playability) 與「聲部連接」(Voice Leading)。
"""

from .preprocess import parse_chord_name
import math

class MusicianQA:
    def __init__(self, target_level=2, bpm=120):
        self.target_level = target_level
        self.bpm = max(bpm, 40)  # 防止除零
        self.warnings = []

    def _min_duration_for_complexity(self, chord_str):
        """根據 BPM + 和弦複雜度計算最短可演奏時間（秒）

        60 BPM 一拍 = 1.0 秒，複雜和弦至少需要 1 拍
        120 BPM 一拍 = 0.5 秒，複雜和弦至少需要 1 拍
        150 BPM 一拍 = 0.4 秒，簡單三和弦半拍就夠
        """
        beat_sec = 60.0 / self.bpm
        if self._is_complex(chord_str):
            return beat_sec * 1.5   # 複雜和弦至少 1.5 拍
        elif self._has_extension(chord_str):
            return beat_sec * 1.0   # 延伸和弦至少 1 拍
        else:
            return beat_sec * 0.5   # 簡單三和弦半拍

    def evaluate_progression(self, chords):
        """
        傳入 [{time, end, chord}, ...] 的和弦序列
        回傳: {
            "playability_score": 0~100,
            "warnings": [str, ...],
            "bpm": int
        }
        """
        self.warnings = []
        if not chords:
            return {"playability_score": 100, "warnings": [], "bpm": self.bpm}

        # 自動偵測 BPM（從和弦時值中位數推估）
        if len(chords) > 4:
            durations = [c.get("end", c["time"] + 2) - c["time"] for c in chords if c.get("end")]
            durations = [d for d in durations if 0.1 < d < 10]
            if durations:
                median_dur = sorted(durations)[len(durations) // 2]
                estimated_bpm = 60.0 / median_dur * 2  # 假設中位數 ≈ 2 拍
                self.bpm = max(40, min(200, round(estimated_bpm)))

        score = 100.0

        for i in range(1, len(chords)):
            prev = chords[i-1]
            curr = chords[i]

            # 1. BPM-aware 和弦密度檢查
            duration = curr.get("end", curr.get("time", 0) + 2) - curr.get("time", 0)
            min_dur = self._min_duration_for_complexity(curr["chord"])
            if duration > 0 and duration < min_dur:
                penalty = 5.0 if self._is_complex(curr["chord"]) else 2.0
                score -= penalty
                self.warnings.append(
                    f"⚠️ [手速@{self.bpm}BPM] {curr.get('time',0):.1f}s: {curr['chord']} "
                    f"停留 {duration:.1f}s < 最低 {min_dur:.1f}s"
                )

            # 2. 根音跳躍性 (Bass Leaps)
            root_prev, _ = parse_chord_name(prev.get("chord", ""))
            root_curr, _ = parse_chord_name(curr.get("chord", ""))
            
            if root_prev is not None and root_curr is not None:
                interval = min((root_curr - root_prev) % 12, (root_prev - root_curr) % 12)
                # 跳躍三全音(Tritone=6)對貝斯手來說很突兀(除非是Tritone Sub)
                if interval == 6:
                    # 檢查是不是 Tritone Sub
                    root_next = None
                    if i + 1 < len(chords):
                        root_next, _ = parse_chord_name(chords[i+1].get("chord", ""))
                        
                    if root_next is not None and (root_next - root_curr) % 12 != 7: # 本身沒解決
                        score -= 3.0
                        self.warnings.append(f"⚠️ [貝斯斷崖] {prev['chord']} -> {curr['chord']} 根音三全音跳躍，且無完美解決！")
        
        # 3. 過度複雜化懲罰 (如果是給新手的譜，卻塞滿 13b9)
        complex_count = sum(1 for c in chords if self._is_complex(c.get("chord", "")))
        complex_ratio = complex_count / len(chords)
        
        if self.target_level == 1 and complex_ratio > 0.1:
            score -= (complex_ratio * 30)
            self.warnings.append(f"⚠️ [難度越級] 等級 1 的樂譜卻出現 {complex_count} 個進階爵士延伸和弦，新手會崩潰！")

        return {
            "playability_score": max(0, round(score, 1)),
            "warnings": self.warnings,
            "bpm": self.bpm,
        }

    def _is_complex(self, chord_str):
        """判斷是否為「手指打結」等級的複雜和弦"""
        complex_markers = ["13", "b9", "#9", "#11", "b13", "alt", "dim7"]
        return any(m in chord_str for m in complex_markers)

    def _has_extension(self, chord_str):
        """有延伸音（7, 9, 11）但不到極度複雜"""
        return any(m in chord_str for m in ["7", "9", "11", "maj7", "m7"]) and not self._is_complex(chord_str)

def run_musician_qa(chords, level=2):
    qa = MusicianQA(target_level=level)
    return qa.evaluate_progression(chords)

if __name__ == "__main__":
    # Test
    test_prog = [
        {"time": 0.0, "end": 2.0, "chord": "Cmaj7"},
        {"time": 2.0, "end": 2.5, "chord": "Db13b9"},  # Too fast, tritone sub
        {"time": 2.5, "end": 4.0, "chord": "Cmaj9"},
    ]
    print(run_musician_qa(test_prog, level=1))
