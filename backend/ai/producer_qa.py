"""
製作人/混音師視角 QA (Producer / Mix Engineer QA)
這是一個實驗性的聲學品質檢查模組，避免 Jazzify 後的和弦在頻譜上與歌曲底頻或是原主旋律打架 (Acoustic Clash)。
"""

from .preprocess import parse_chord_name

class ProducerQA:
    def __init__(self):
        self.warnings = []

    def evaluate_progression(self, original_chords, jazzified_chords, track_info=None):
        """
        track_info 可能包含原曲的頻譜密度或曲風資訊 (未來擴充)。
        目前透過邏輯模擬頻譜泥濘度 (Muddy / Masking)。
        """
        self.warnings = []
        score = 100.0

        if not original_chords or not jazzified_chords:
            return {"mix_score": score, "warnings": []}

        # 分析原曲的和弦複雜度基準
        orig_complexity = self._avg_complexity(original_chords)

        muddy_count = 0
        clash_count = 0
        for i in range(len(jazzified_chords)):
            c = jazzified_chords[i].get("chord", "")
            orig_c = original_chords[i].get("chord", "") if i < len(original_chords) else ""

            # 1. 頻率泥濘：原曲簡單（≤2字元）被膨脹為複雜和弦
            if len(orig_c) <= 2 and self._is_muddy_in_context(c, orig_complexity):
                muddy_count += 1
                score -= 2.0
                if muddy_count % 3 == 0:
                    self.warnings.append(
                        f"⚠️ [頻率泥濘] {orig_c}→{c} 原曲風格簡潔（複雜度{orig_complexity:.0f}），"
                        f"過度膨脹導致 200-500Hz 泥濘"
                    )

            # 2. 小調和弦被強塞大調延伸
            if "m" in orig_c and not "maj" in orig_c:
                if "maj" in c or ("9" in c and "m9" not in c):
                    clash_count += 1
                    score -= 1.5
                    if clash_count % 2 == 0:
                        self.warnings.append(f"⚠️ [調性衝突] {orig_c}→{c} 小調和弦被塞入大調延伸音")

        return {
            "mix_score": max(0, round(score, 1)),
            "warnings": self.warnings[:5],
        }

    def _avg_complexity(self, chords):
        """計算和弦序列的平均複雜度"""
        if not chords:
            return 0
        total = 0
        for c in chords:
            name = c.get("chord", "")
            if any(m in name for m in ["13", "b9", "#9", "alt"]):
                total += 4
            elif any(m in name for m in ["9", "11", "dim7"]):
                total += 3
            elif any(m in name for m in ["7", "maj7", "m7"]):
                total += 2
            elif any(m in name for m in ["m", "dim", "aug"]):
                total += 1
        return total / len(chords)

    def _is_muddy_in_context(self, chord_str, baseline_complexity):
        """根據原曲複雜度基準判斷是否泥濘"""
        # 原曲如果本身就複雜（>2），dim7 不算泥濘
        # 原曲簡單（<1），連 dim7 都算泥濘
        muddy_always = ["13", "b9", "b13", "alt"]
        muddy_in_simple = ["dim7", "#11", "aug7"]

        if any(m in chord_str for m in muddy_always):
            return True
        if baseline_complexity < 1.5 and any(m in chord_str for m in muddy_in_simple):
            return True
        return False

def run_producer_qa(original, jazzified):
    qa = ProducerQA()
    return qa.evaluate_progression(original, jazzified)
