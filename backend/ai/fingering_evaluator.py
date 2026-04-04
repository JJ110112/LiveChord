import math
from typing import List, Dict, Any

class FingeringEvaluator:
    """
    Evaluator AI: 驗證指法序列的物理合理性 (Ergonomics Critic)
    使用基於解剖學的規則來計算「手部疲勞/不可能動作」的 Cost。
    """
    def __init__(self, hand="right"):
        self.hand = hand
        
    def evaluate(self, pitches: List[int], fingers: List[int]) -> Dict[str, Any]:
        """
        輸入 Pitch 序列與對應的 指法 序列，輸出評分報告。
        分數 0~100 (100 為最合理)，若遭遇物理不可能動作，給出極低分。
        """
        if not pitches or not fingers or len(pitches) != len(fingers):
            return {"score": 0, "valid": False, "reason": "Length mismatch or empty"}

        cost = 0.0
        warnings = []
        
        for i in range(1, len(pitches)):
            p_prev = pitches[i-1]
            p_curr = pitches[i]
            f_prev = fingers[i-1]
            f_curr = fingers[i]
            
            delta_p = p_curr - p_prev
            
            # 同一個音
            if delta_p == 0:
                if f_prev != f_curr:
                    # 同音換指(通常是刻意)，可接受但有微小 cost
                    cost += 1.0
                continue
                
            # 計算如果是不換指距(同手指跳躍)
            if f_prev == f_curr:
                # 拇指滑音稍微可接受，其他手指導致跳躍增加很高成本
                slide_cost = abs(delta_p) * 2.0
                if f_curr == 1:
                    slide_cost *= 0.5 
                cost += slide_cost
                if abs(delta_p) > 4:
                    warnings.append(f"同指大跳 (指{f_curr}, 移{delta_p}半音)")
                continue

            # 使用不同手指，測量展開寬度
            # 右手：高音數字 > 低音數字；拇指=1, 小指=5
            # 如果 pitch 變高，通常手指數字應該變大 (拇指1 -> 小指5)
            # 左手：高音數字 > 低音數字；小指=5, 拇指=1
            # 如果 pitch 變高，手指數字其實應該變小 (小指5 -> 拇指1)
            
            fp_diff = f_curr - f_prev
            if self.hand == "left":
                fp_diff = -fp_diff
                
            # 手指跨度預期
            # 例如右手 1 -> 2，理想是在 1~4 個半音間
            expected_min = 1
            expected_max = abs(fp_diff) * 4
            if fp_diff < 0:
                # 交叉 (Cross-over or Thumb-under)
                # 右手 3(中指) -> 1(拇指) 上行 (delta_p > 0)
                if delta_p > 0 and f_curr == 1:
                    cost += 2.0 # 穿指成本
                # 右手 1(拇指) -> 3(中指) 下行 (delta_p < 0)
                elif delta_p < 0 and f_prev == 1:
                    cost += 2.0 # 跨指成本
                else:
                    # 非正規交叉 (例如 4->5 下行)
                    cost += abs(delta_p) * 3.0
                    warnings.append(f"不可能交叉 (指{f_prev}->{f_curr}, 移{delta_p})")
            else:
                # 正常順位擴張
                if abs(delta_p) > 13:
                    cost += 50.0  # 致命跨度
                    warnings.append(f"致命跨距 (跨 {abs(delta_p)} 半音)")
                elif abs(delta_p) > expected_max + 2:
                    cost += (abs(delta_p) - expected_max) * 1.5

        # 最終評分
        max_cost = len(pitches) * 10
        normalized_score = 100.0 - (cost / max_cost * 100.0) if max_cost > 0 else 100.0
        normalized_score = max(0.0, min(100.0, normalized_score))
        
        # 嚴格驗證閥值
        is_valid = normalized_score > 60.0 and "致命跨距" not in "".join(warnings)
        
        return {
            "score": round(normalized_score, 1),
            "valid": is_valid,
            "cost": cost,
            "warnings": list(set(warnings))
        }

def evaluate_fingers(pitches: List[int], fingers: List[int], hand: str = "right") -> Dict:
    evaluator = FingeringEvaluator(hand=hand)
    return evaluator.evaluate(pitches, fingers)
