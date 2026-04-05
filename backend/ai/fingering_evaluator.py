import math
from typing import List, Dict, Any

class FingeringEvaluator:
    """
    Evaluator AI: 驗證指法序列的物理合理性 (Ergonomics Critic)
    使用基於解剖學的規則來計算「手部疲勞/不可能動作」的 Cost。
    這個版本可以支援和弦 (Polyphony) 檢查 (垂直間距約束與水平線性約束)。
    """
    def __init__(self, hand="right"):
        self.hand = hand
        
    def evaluate_events(self, events: List[Dict]) -> Dict[str, Any]:
        """
        輸入完整的 events 列表 (每個元素需有 'time', 'pitch', 'finger')。
        將會分成 Pass 1: 垂直約束 (同時按下的音符)，與 Pass 2: 水平約束 (時間軸）。
        """
        if not events:
            return {"score": 0, "valid": False, "reason": "Empty events"}

        # 確保順序正確
        sorted_events = sorted(events, key=lambda e: (e['time'], e['pitch']))
        
        # 依照時間分組
        time_groups: Dict[float, List[Dict]] = {}
        for e in sorted_events:
            t = e.get('time', 0.0)
            if t not in time_groups:
                time_groups[t] = []
            time_groups[t].append(e)

        cost = 0.0
        warnings = []
        
        sorted_times = sorted(time_groups.keys())
        
        # ---------------------------------------------------------
        # Pass 1: Vertical Constraints (垂直約束)
        # ---------------------------------------------------------
        for t in sorted_times:
            group = time_groups[t]
            if len(group) <= 1:
                continue
                
            # 計算這個 Group 裡頭的物理限制
            # 必須根據音高排序
            group_sorted_by_pitch = sorted(group, key=lambda x: x['pitch'])
            min_pitch = group_sorted_by_pitch[0]['pitch']
            max_pitch = group_sorted_by_pitch[-1]['pitch']
            
            # 手指列表
            fingers_played = [e.get('finger', 1) for e in group_sorted_by_pitch]
            
            # 1. 致命最大跨距 (Span Constraint)
            # 大多數人超過 13 半音 (八度+小二度) 無法同時按下
            span = max_pitch - min_pitch
            if span > 13:
                cost += 100.0
                warnings.append(f"時間 {t}: 致命跨距 (跨 {span} 半音, 超出 13 限制)")

            # 2. 手指順序錯位 (Inversion Constraint)
            # 例如右手: 音越高，指法應該越大(或相等)
            # 例如左手: 音越高，指法應該越小(或相等)
            for i in range(len(fingers_played) - 1):
                f1 = fingers_played[i]
                f2 = fingers_played[i+1]
                p1 = group_sorted_by_pitch[i]['pitch']
                p2 = group_sorted_by_pitch[i+1]['pitch']
                
                # 如果音不一樣高，但是手指顛倒
                if p1 != p2:
                    if self.hand == "right" and f1 > f2:
                        cost += 80.0
                        warnings.append(f"時間 {t}: 右手手指錯位 (指 {f1} 按 {p1}, 指 {f2} 按 {p2})")
                    elif self.hand == "left" and f1 < f2:
                        cost += 80.0
                        warnings.append(f"時間 {t}: 左手手指錯位 (指 {f1} 按 {p1}, 指 {f2} 按 {p2})")

        # ---------------------------------------------------------
        # Pass 2: Horizontal Constraints (水平約束)
        # ---------------------------------------------------------
        for i in range(1, len(sorted_times)):
            t_prev = sorted_times[i-1]
            t_curr = sorted_times[i]
            delta_time = t_curr - t_prev
            
            group_prev = time_groups[t_prev]
            group_curr = time_groups[t_curr]
            
            # 同指跳躍 (Same Finger Jump) 懲罰
            for f in range(1, 6):
                notes_prev_f = [e for e in group_prev if e.get('finger', 1) == f]
                notes_curr_f = [e for e in group_curr if e.get('finger', 1) == f]
                
                if not notes_prev_f or not notes_curr_f:
                    continue
                    
                p_prev = notes_prev_f[0]['pitch']
                p_curr = notes_curr_f[0]['pitch']
                
                delta_p = p_curr - p_prev
                if delta_p != 0:
                    slide_cost = abs(delta_p) * 3.0
                    if f == 1:
                        slide_cost *= 0.5 
                    
                    time_penalty = 1.0
                    if delta_time < 0.2:
                        time_penalty = 6.0
                    elif delta_time < 0.4:
                        time_penalty = 2.5
                        
                    applied_cost = slide_cost * time_penalty
                    cost += applied_cost
                    if applied_cost > 15.0:
                        warnings.append(f"時間 {t_curr}: 同指不合理跳躍 (指{f}, 移{delta_p}半音, td={round(delta_time,2)})")

            # 測量整體重心與穿指/跨指 (Crossover / Thumb Under)
            p_prev_rep = max([e['pitch'] for e in group_prev]) if self.hand == "right" else min([e['pitch'] for e in group_prev])
            f_prev_rep = [e.get('finger', 1) for e in group_prev if e['pitch'] == p_prev_rep][0]
            
            p_curr_rep = min([e['pitch'] for e in group_curr]) if self.hand == "right" else max([e['pitch'] for e in group_curr])
            f_curr_rep = [e.get('finger', 1) for e in group_curr if e['pitch'] == p_curr_rep][0]
            
            delta_p_rep = p_curr_rep - p_prev_rep
            
            fp_diff = f_curr_rep - f_prev_rep
            if self.hand == "left":
                fp_diff = -fp_diff
                
            if fp_diff < 0:
                if delta_p_rep > 0 and f_curr_rep == 1:
                    cost += 2.0 # 合法大拇指穿指
                elif delta_p_rep < 0 and f_prev_rep == 1:
                    cost += 2.0 # 合法跨指
                else:
                    if abs(delta_p_rep) > 2:
                        punish = abs(delta_p_rep) * 5.0
                        cost += punish
                        if punish > 10.0:
                            warnings.append(f"時間 {t_curr}: 不合理交叉 (移 {delta_p_rep}半音)")

        # ---------------------------------------------------------
        # 最終評分運算
        # ---------------------------------------------------------
        max_cost = max(10, len(events) * 30)
        normalized_score = 100.0 - (cost / max_cost * 100.0)
        normalized_score = max(0.0, min(100.0, normalized_score))
        
        # Strict Rule-based Validation
        has_fatal_error = False
        fatal_keywords = ["致命跨距", "手指錯位", "不合理交叉", "不合理跳躍"]
        for w in warnings:
            for k in fatal_keywords:
                if k in w:
                    has_fatal_error = True
                    break
            if has_fatal_error:
                break
                
        is_valid = (normalized_score > 60.0) and not has_fatal_error
        
        # 去重複警報
        unique_warnings = list(dict.fromkeys(warnings))
                
        return {
            "score": round(normalized_score, 1),
            "valid": is_valid,
            "cost": cost,
            "warnings": unique_warnings
        }

    def evaluate(self, pitches: List[int], fingers: List[int]) -> Dict[str, Any]:
        """
        (Backward Compatibility)
        """
        if not pitches or not fingers or len(pitches) != len(fingers):
            return {"score": 0, "valid": False, "reason": "Length mismatch or empty"}

        events = []
        for i in range(len(pitches)):
            events.append({"time": float(i), "pitch": pitches[i], "finger": fingers[i]})
        
        return self.evaluate_events(events)

def evaluate_fingers(pitches: List[int], fingers: List[int], hand: str = "right") -> Dict:
    evaluator = FingeringEvaluator(hand=hand)
    return evaluator.evaluate(pitches, fingers)

def evaluate_events(events: List[Dict], hand: str = "right") -> Dict:
    evaluator = FingeringEvaluator(hand=hand)
    return evaluator.evaluate_events(events)

if __name__ == "__main__":
    import pprint
    evaluator_rh = FingeringEvaluator(hand="right")
    
    print("\n--- 1. Valid Arpeggio (RH) ---")
    ev1 = [
        {"time": 0.0, "pitch": 60, "finger": 1},
        {"time": 0.5, "pitch": 64, "finger": 2},
        {"time": 1.0, "pitch": 67, "finger": 3},
        {"time": 1.5, "pitch": 72, "finger": 5},
    ]
    pprint.pprint(evaluator_rh.evaluate_events(ev1))

    print("\n--- 2. Invalid Finger Inversion (RH) ---")
    ev2 = [
        {"time": 0.0, "pitch": 60, "finger": 5}, # C4
        {"time": 0.0, "pitch": 64, "finger": 1}, # E4
    ]
    pprint.pprint(evaluator_rh.evaluate_events(ev2))

    print("\n--- 3. Invalid Span > 13 (LH) ---")
    evaluator_lh = FingeringEvaluator(hand="left")
    ev3 = [
        {"time": 0.0, "pitch": 40, "finger": 5}, # E2
        {"time": 0.0, "pitch": 47, "finger": 3}, # B2
        {"time": 0.0, "pitch": 55, "finger": 1}, # G3 (Span 15)
    ]
    pprint.pprint(evaluator_lh.evaluate_events(ev3))
