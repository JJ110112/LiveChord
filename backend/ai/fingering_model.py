"""
Generator AI: 指法生成模型
Phase 11a 重構: 委託給 viterbi_engine.generate_fingering，
對外 API 保持不變 (generate_fingers)。

原本的 FingeringGeneratorAI 類別保留為向後相容包裝。
"""
from typing import List

from .viterbi_engine import generate_fingering


class FingeringGeneratorAI:
    """
    Generator AI: 生成指法的模型。
    Phase 11a: 內部委託 viterbi_engine 統一框架。
    """
    def __init__(self, hand="right", bpm=100):
        self.hand = hand
        self.bpm = bpm

    def generate(self, pitches: List[int]) -> List[int]:
        return generate_fingering(pitches, hand=self.hand, bpm=self.bpm)


def generate_fingers(pitches: List[int], hand: str = "right", bpm: int = 100) -> List[int]:
    """公開 API: 生成最優指法序列。"""
    return generate_fingering(pitches, hand=hand, bpm=bpm)
