from typing import List
import math

class FingeringGeneratorAI:
    """
    Generator AI: 生成指法的模型。
    未來將可替換為 Seq2Seq RNN/Transformer 載入 PyTorch weights。
    目前實作升級版的機率搜索模型來取代原本粗糙的 Viterbi。
    """
    def __init__(self, hand="right"):
        self.hand = hand
        self.fingers = [1, 2, 3, 4, 5]
        
    def _get_transition_cost(self, f1: int, f2: int, delta_p: int) -> float:
        """
        Transition Model: Finger 1 to Finger 2
        """
        if delta_p == 0:
            return 1.0 if f1 != f2 else 0.0
            
        cost = 0.0
        # 同指跳躍極度懲罰
        if f1 == f2:
            return 10.0 + abs(delta_p)
            
        fp_diff = f2 - f1
        if self.hand == "left":
            fp_diff = -fp_diff
            
        # 交叉
        if fp_diff < 0:
            if delta_p > 0 and f2 == 1:
                cost = 2.0  # Thumb under
            elif delta_p < 0 and f1 == 1:
                cost = 2.0  # Cross over thumb
            else:
                cost = 20.0 # 不可能的交叉
        else:
            if delta_p < 0:
               cost = 20.0 # 錯誤的伸展方向 
            else:
                # 正常擴張
                expected = abs(fp_diff) * 3
                cost = abs(delta_p - expected) * 0.5
                
        return cost

    def generate(self, pitches: List[int]) -> List[int]:
        """
        預測序列 (Viterbi Decoding, 升級版)
        """
        if not pitches:
            return []
            
        if len(pitches) == 1:
            return [1] if self.hand == "right" else [5]
            
        dp = [{f: 0.0 for f in self.fingers}]
        pointers = [{f: f for f in self.fingers}]
        
        for i in range(1, len(pitches)):
            delta = pitches[i] - pitches[i-1]
            current_dp = {}
            current_ptr = {}
            
            for curr_f in self.fingers:
                min_cost = float('inf')
                best_prev_f = curr_f
                
                for prev_f in self.fingers:
                    trans_cost = self._get_transition_cost(prev_f, curr_f, delta)
                    cost = dp[i-1][prev_f] + trans_cost
                    if cost < min_cost:
                        min_cost = cost
                        best_prev_f = prev_f
                        
                current_dp[curr_f] = min_cost
                current_ptr[curr_f] = best_prev_f
                
            dp.append(current_dp)
            pointers.append(current_ptr)
            
        # Backtrack
        min_final_cost = float('inf')
        best_last_f = 1
        for f in self.fingers:
            if dp[-1][f] < min_final_cost:
                min_final_cost = dp[-1][f]
                best_last_f = f
                
        path = [best_last_f]
        curr_f = best_last_f
        for i in range(len(pitches)-1, 0, -1):
            curr_f = pointers[i][curr_f]
            path.append(curr_f)
            
        path.reverse()
        return path

def generate_fingers(pitches: List[int], hand: str = "right") -> List[int]:
    model = FingeringGeneratorAI(hand=hand)
    return model.generate(pitches)
