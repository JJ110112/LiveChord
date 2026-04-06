"""
Accompaniment Generation & Fingering Engine
伴奏生成與指法推導引擎 (Phase 10)

Pipeline:
  1. Skeleton Voicing: chord text ->MIDI pitches (via chord_table)
  2. Style Pattern Routing: map pitches to rhythmic pattern
  3. Conflict Resolution: avoid melody collision
  4. Viterbi Fingering: optimal finger assignment
"""

import sys
import os
import math
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from backend.chord_table import get_chord_notes, root_to_semitone, parse_chord

# ==============================================================================
# 壹、基礎常數
# ==============================================================================

OCTAVE = 12
# Left hand range: C2 (36) ~ B3 (59)
LH_LOW, LH_HIGH = 36, 59
# Right hand range: C4 (60) ~ C6 (84)
RH_LOW, RH_HIGH = 60, 84

# ==============================================================================
# 貳、Pattern Dictionary (樣式字典)
# ==============================================================================
# 格式: [(time_fraction, [pitch_indices], velocity_ratio)]
# time_fraction: 0.0~1.0 相對於和弦持續時間的位置
# pitch_indices: 引用 voicing 陣列的索引 ('R'=0, '3rd'=1, '5th'=2, '7th'=3)
# velocity_ratio: 相對力度 (1.0 = 基準)

STYLE_DICT = {
    # ── Pop Ballad 系列 (參考 Ron Drotos Pop Ballad Accompaniment) ──
    "Block": [
        # Lesson 1/4: 全部和弦音同時下鍵
        (0.0, [0, 1, 2], 1.0),
    ],
    "Arpeggio": [
        # Lesson 5/8: 分解伴奏 — root 錨定，上方音流動
        # L2 voicing [root,5th,oct]: R→5→8→5 (經典 pop ballad 搖擺)
        # L3 voicing [root,3rd,5th,...]: R→3→5→3
        (0.0,  [0],  1.0),     # Beat 1: 根音 (bass anchor)
        (0.25, [1],  0.7),     # Beat 2: 上方音
        (0.50, [2],  0.75),    # Beat 3: 最高音
        (0.75, [1],  0.65),    # Beat 4: 回落
    ],
    "Rhythm": [
        # Lesson 2/11: 附點節奏 .œ jœ ˙ (pop ballad 標誌性節奏)
        # 附點四分(1.5拍) + 八分(0.5拍) + 二分(2拍)
        (0.0,    [0, 2], 1.0),    # Beat 1: root+octave (附點四分音符)
        (0.375,  [0],    0.65),   # Beat 2+: root only (輕觸八分音符)
        (0.5,    [0, 2], 0.8),    # Beat 3: root+octave (二分音符，sustained)
    ],
    "Alberti": [
        # 古典分解: 低-高-中-高
        (0.0,  [0],  1.0),   # 低 (Root)
        (0.25, [2],  0.75),   # 高 (5th)
        (0.50, [1],  0.8),    # 中 (3rd)
        (0.75, [2],  0.75),   # 高 (5th)
    ],
    "Shell": [
        (0.0, [1, 3], 0.9),  # 3rd + 7th only
    ],
    "Walking": [
        (0.0,  [0],  1.0),    # Root
        (0.25, [1],  0.85),   # 3rd
        (0.50, [2],  0.85),   # 5th
        (0.75, [-1], 0.8),    # approach note (chromatic below next root)
    ],
    "Stride": [
        (0.0,  [0],       1.0),    # 低音 Root (低八度)
        (0.50, [1, 2, 3], 0.85),   # 中音 和弦 (正常八度)
    ],
    # ── 1+3 配置 (NiceChord 好和弦) ──
    # LH: 根音一個 (C2~C3)，換和弦時彈一次
    # RH: 三個和弦音 block (C4 附近)，每拍彈一次
    # 此 pattern 只用於 LH (根音全音符)，RH 由 _build_rh_1plus3 專門處理
    "1+3": [
        (0.0, [0], 0.9),     # LH: 根音 (全音符，只彈一次)
    ],
}

# 曲風適配表
GENRE_STYLE_MAP = {
    "pop":       ["Block", "Arpeggio", "Rhythm", "1+3"],
    "ballad":    ["Arpeggio", "Block", "1+3"],
    "jazz":      ["Shell", "Walking", "Stride", "1+3"],
    "bossa":     ["Shell", "Walking"],
    "classical": ["Alberti", "Arpeggio"],
    "rock":      ["Rhythm", "Block"],
    "r&b":       ["Rhythm", "Shell", "Arpeggio", "1+3"],
    "electronic": ["Block", "Rhythm"],
    "edm":       ["Block", "Rhythm"],
    "country":   ["Arpeggio", "Block", "1+3"],
    "folk":      ["Arpeggio", "Block", "1+3"],
    "latin":     ["Rhythm", "Walking"],
}

BPM_STYLE_MAP = [
    (80,  ["Arpeggio", "Shell"]),       # 慢歌
    (120, ["Block", "Arpeggio", "Rhythm"]),  # 中速
    (999, ["Rhythm", "Block"]),          # 快歌
]

# ==============================================================================
# Style → 固定 LH/RH 行為 (取代 L1/L2/L3 level 系統)
# ==============================================================================
# 每個 style 自帶一個合理的 voicing 複雜度，不需要使用者再選 level
# lh_level: 控制左手 voicing (L1=根音, L2=root+5th+oct, L3=full voicing)
# rh_mode:  控制右手 (fill_only/fill_harmony/fill_block/arpeggio/1+3)
# lh_vel:   左手基準力度
# rh_vel:   右手基準力度
STYLE_CONFIG = {
    "1+3":     {"lh_level": "L1", "rh_mode": "1+3",          "lh_vel": 55, "rh_vel": 85},
    "Block":   {"lh_level": "L2", "rh_mode": "fill_only",    "lh_vel": 60, "rh_vel": 90},
    "Arpeggio":{"lh_level": "L2", "rh_mode": "fill_only",    "lh_vel": 60, "rh_vel": 85},
    "Rhythm":  {"lh_level": "L2", "rh_mode": "fill_harmony", "lh_vel": 65, "rh_vel": 90},
    "Alberti": {"lh_level": "L2", "rh_mode": "fill_only",    "lh_vel": 60, "rh_vel": 85},
    "Shell":   {"lh_level": "L3", "rh_mode": "fill_harmony", "lh_vel": 65, "rh_vel": 85},
    "Walking": {"lh_level": "L3", "rh_mode": "fill_harmony", "lh_vel": 70, "rh_vel": 85},
    "Stride":  {"lh_level": "L3", "rh_mode": "fill_block",   "lh_vel": 70, "rh_vel": 90},
}

# ==============================================================================
# Phase 11b #3: Section-Aware 密度/力度控制
# ==============================================================================
# 段落類型 → (密度乘數, 力度乘數)
# 密度乘數: 控制 pattern 中實際彈奏的音符比例
# 力度乘數: 控制 velocity 基準
SECTION_PARAMS = {
    "intro":      (0.5, 0.6),
    "verse":      (0.7, 0.7),
    "pre_chorus": (0.9, 0.85),
    "chorus":     (1.0, 1.0),
    "bridge":     (0.6, 0.65),
    "outro":      (0.4, 0.5),
    "default":    (0.8, 0.8),
}

# ── Section → LH Pattern 自動切換 (style="Auto" 時啟用) ──
# 參考 Ron Drotos Pop Ballad Accompaniment 各 Lesson 的段落編排
SECTION_STYLE_MAP = {
    "intro":      "Block",      # Lesson 1: 稀疏全音符，建立氛圍
    "verse":      "Arpeggio",   # Lesson 5/8: 流動分解，襯托人聲
    "pre_chorus": "Arpeggio",   # 仍流動但密度/力度由 SECTION_PARAMS 提升
    "chorus":     "Rhythm",     # Lesson 11: 附點節奏，有力推進
    "bridge":     "Arpeggio",   # 對比回落
    "outro":      "Block",      # 收尾
    "default":    "Arpeggio",
}

# ── Section → RH 伴奏模式 (style="Auto" 時啟用) ──
# 核心原則: RH 伴奏閃避人聲，在人聲空白處 (gap) 補 fill
#   - intro/outro: 尚無人聲，RH 彈和弦琶音營造氛圍
#   - verse: 人聲主導，RH 只在 gap 補單音 fill (不搶戲)
#   - pre_chorus: 情緒推升，gap 處補稍豐富的 fill
#   - chorus: 最飽滿段落，gap 處補複音 block chord
#   - bridge: 對比回落，RH 彈和弦琶音
RH_SECTION_MODE = {
    "intro":      "arpeggio",       # 和弦琶音 (無人聲段)
    "verse":      "fill_only",      # gap 補單音 fill
    "pre_chorus": "fill_harmony",   # gap 補 1~2 音 fill
    "chorus":     "fill_block",     # gap 補複音 block chord
    "bridge":     "arpeggio",       # 琶音回落
    "outro":      "arpeggio",       # 琶音收尾
    "default":    "fill_only",
}

# RH 琶音 Pattern (用於 intro/bridge/outro — 右手範圍 C4~C6)
RH_ARPEGGIO_PATTERN = [
    (0.0,  [0], 0.8),    # Beat 1: Root
    (0.25, [1], 0.65),   # Beat 2: 3rd
    (0.50, [2], 0.7),    # Beat 3: 5th
    (0.75, [1], 0.6),    # Beat 4: 3rd (回落)
]


# ==============================================================================
# Phase 11b #3: Walking Bass 進階手法
# ==============================================================================
# approach_type: "chromatic", "diatonic", "enclosure"
def _walking_bass_approach(next_root_midi: int, key_semi: int = 0,
                           approach_type: str = "chromatic") -> int:
    """
    生成 Walking Bass 的 approach note (趨近音)。

    chromatic: 目標音 ±1 半音 (經典手法)
    diatonic:  調內音階的下方二度
    enclosure: 上下包圍 (先高半音, 再低半音, 解決到目標)
    """
    if approach_type == "diatonic":
        # 調內音階下方二度: 全音或半音
        scale = [0, 2, 4, 5, 7, 9, 11]
        target_pc = next_root_midi % 12
        # 找調內的下方音
        relative_pc = (target_pc - key_semi) % 12
        for i, s in enumerate(scale):
            if s == relative_pc:
                prev_scale_deg = scale[(i - 1) % len(scale)]
                return (key_semi + prev_scale_deg) % 12 + (next_root_midi // 12) * 12
        # fallback to chromatic
        return next_root_midi - 1
    elif approach_type == "enclosure":
        # 上方半音 (用於 beat 3, approach 到 beat 4 的目標)
        return next_root_midi + 1
    else:
        # chromatic below
        return next_root_midi - 1


# ==============================================================================
# 參、音高工具函數
# ==============================================================================

def note_names_to_midi(notes: List[str], base_octave: int) -> List[int]:
    """將音名陣列轉為 MIDI pitches，保證向上遞增。"""
    if not notes:
        return []
    pitches = []
    base = root_to_semitone(notes[0]) + (base_octave + 1) * 12
    for note in notes:
        st = root_to_semitone(note)
        pitch = st + (base_octave + 1) * 12
        while pitches and pitch <= pitches[-1]:
            pitch += 12
        if not pitches and pitch < base:
            pitch += 12
        pitches.append(pitch)
    return pitches


def voice_leading_optimize(pitches: List[int], prev_pitches: List[int],
                           low: int, high: int) -> List[int]:
    """
    Voice Leading 最佳化 (Phase 11b #3 強化版)。

    規則 (Piston, Harmony 5th ed.):
      1. 最短移動距離
      2. 共同音保持 (common tone retention)
      3. 避免平行五度/八度 (basic check)
    """
    if not prev_pitches or not pitches:
        return pitches

    # 共同音保持: 如果 pitch class 相同，優先保持同八度
    prev_pc_map = {}
    for p in prev_pitches:
        prev_pc_map[p % 12] = p

    result = []
    for p in pitches:
        pc = p % 12
        if pc in prev_pc_map:
            # 共同音: 保持在同一八度
            common = prev_pc_map[pc]
            if low <= common <= high:
                result.append(common)
                continue

        # 最短距離
        prev_center = sum(prev_pitches) / len(prev_pitches)
        best = p
        best_dist = abs(p - prev_center)
        for shift in [-12, 0, 12]:
            candidate = p + shift
            if low <= candidate <= high:
                dist = abs(candidate - prev_center)
                if dist < best_dist:
                    best_dist = dist
                    best = candidate
        result.append(best)

    result.sort()

    # 平行五度/八度簡易檢查 (如果有足夠聲部)
    # 只在修正後仍為和弦音時才套用，避免產生不屬於和弦的音
    chord_pcs = {p % 12 for p in pitches}
    if len(result) >= 2 and len(prev_pitches) >= 2:
        prev_sorted = sorted(prev_pitches)
        n = min(len(result), len(prev_sorted))
        for i in range(n - 1):
            for j in range(i + 1, n):
                iv_prev = abs(prev_sorted[j] - prev_sorted[i]) % 12
                iv_curr = abs(result[j] - result[i]) % 12
                mv_i = result[i] - prev_sorted[i]
                mv_j = result[j] - prev_sorted[j]
                # 同方向 + 平行五度
                if mv_i * mv_j > 0 and iv_prev == 7 and iv_curr == 7:
                    original = result[j]
                    if result[j] + 1 <= high and (result[j] + 1) % 12 in chord_pcs:
                        result[j] += 1
                    elif result[j] - 1 >= low and (result[j] - 1) % 12 in chord_pcs:
                        result[j] -= 1
                    # 若偏移後不是和弦音，寧可保留平行五度也不彈錯音
                    # (不做任何修正)

    return result


def expand_voicing(pitches: List[int], target_size: int, max_span: int = 13) -> List[int]:
    """擴展 voicing 到指定大小（八度疊加），並強制進行人手合理性檢查（聲部過濾與最大跨度限制）。"""
    if not pitches:
        return []
    result = list(pitches)
    base_pitch = min(pitches)
    
    # 嘗試往上疊加八度，並套用 Voice Filtering 與 Max Span Constraint
    while len(result) < target_size:
        next_pitch = result[-len(pitches)] + 12
        if next_pitch - base_pitch <= max_span:
            result.append(next_pitch)
        else:
            # 超過閾值：尋找該和絃的密集排列（Close Position）替代方案
            close_pitch = next_pitch
            while close_pitch - base_pitch > max_span:
                close_pitch -= 12
            
            if close_pitch >= base_pitch and close_pitch not in result:
                result.append(close_pitch)
                result.sort()
            else:
                # 無可用的 Close Position，強制拋棄重複音 (Doubling notes)
                break
                
    return result[:target_size]


def clamp_to_range(pitches: List[int], low: int, high: int) -> List[int]:
    """將音高限制在指定範圍內。"""
    result = []
    for p in pitches:
        while p < low:
            p += 12
        while p > high:
            p -= 12
        result.append(p)
    return sorted(set(result))


# ==============================================================================
# 肆、伴奏生成核心
# ==============================================================================

def suggest_style(genre: str = "", bpm: float = 120.0) -> List[str]:
    """根據曲風和 BPM 建議伴奏風格，回傳排序後的建議清單。"""
    genre_lower = genre.lower().strip()
    scores: Dict[str, float] = {}

    # Genre 匹配
    for key, styles in GENRE_STYLE_MAP.items():
        if key in genre_lower:
            for i, s in enumerate(styles):
                scores[s] = scores.get(s, 0) + (3 - i)  # 越前面分數越高

    # BPM 匹配
    for threshold, styles in BPM_STYLE_MAP:
        if bpm < threshold:
            for i, s in enumerate(styles):
                scores[s] = scores.get(s, 0) + (2 - i * 0.5)
            break

    # 無匹配時回傳預設
    if not scores:
        return ["Block", "Arpeggio", "Rhythm"]

    return sorted(scores, key=scores.get, reverse=True)[:3]


def _build_left_hand(chord_name: str, start_time: float, duration: float,
                     style: str, level: str, prev_lh: List[int],
                     next_root_midi: Optional[int],
                     melody: List[Dict], base_velocity: int = 70) -> Tuple[List[Dict], List[int]]:
    """生成單一和弦的左手伴奏事件。"""
    notes = get_chord_notes(chord_name)
    if not notes:
        return [], prev_lh

    root_name, quality, bass_name = parse_chord(chord_name)
    if bass_name:
        notes[0] = bass_name

    # Level 決定 voicing 複雜度
    # 參考 Ron Drotos《Pop Ballad Accompaniment》:
    #   L1 = Lesson 1 (whole note root)
    #   L2 = Lesson 11 (octave bass: root + 5th + octave)
    #   L3 = Lesson 12/14 (full voicing + passing tones)
    if level == "L1":
        # 只有根音 (C2)
        raw = note_names_to_midi(notes[:1], base_octave=2)
    elif level == "L2":
        # Pop Octave Bass: root(C2) + 5th + root octave(C3)
        root_st = root_to_semitone(notes[0]) + 36  # C2 root
        # 取五度音 (第 3 個組成音)
        fifth_name = notes[min(2, len(notes) - 1)]
        fifth_midi = root_to_semitone(fifth_name) + 36
        while fifth_midi <= root_st:
            fifth_midi += 12
        # 若五度超過八度範圍，拉回
        oct_st = root_st + 12  # Root octave (C3)
        if fifth_midi > oct_st:
            fifth_midi -= 12
            if fifth_midi <= root_st:
                fifth_midi = root_st + 7  # fallback: 純五度
        raw = sorted(set([root_st, fifth_midi, oct_st]))
    else:
        # L3: Open Voicing — root(C2) + 全部和弦音(C3)
        upper = note_names_to_midi(notes[1:], base_octave=3)
        root_st = root_to_semitone(notes[0]) + 36  # C2 root
        raw = [root_st] + upper

    # Clamp 到左手範圍
    pitches = clamp_to_range(raw, LH_LOW, LH_HIGH)
    if not pitches:
        pitches = clamp_to_range(raw, LH_LOW - 12, LH_HIGH + 12)
    if not pitches:
        return [], prev_lh

    # Voice Leading (L2 pop octave bass 不做 VL，避免 3 音 voicing 碰撞產生重複音)
    if level != "L2":
        pitches = voice_leading_optimize(pitches, prev_lh, LH_LOW, LH_HIGH)

    # 取得 pattern
    pattern = STYLE_DICT.get(style, STYLE_DICT["Block"])

    # L1 強制簡化 pattern
    if level == "L1" and style not in ("Block", "Shell"):
        pattern = [(0.0, [0], 1.0)]

    # 擴展 voicing 到 pattern 需要的最大索引
    max_idx = max(max(abs(i) for i in indices) for _, indices, _ in pattern) + 1
    voicing = expand_voicing(pitches, max(max_idx, len(pitches)))

    events = []
    for pi, (frac, indices, vel_ratio) in enumerate(pattern):
        event_time = start_time + frac * duration
        # Gap-based duration: 音符持續到下一個 pattern 位置 (×0.9 留呼吸空間)
        next_frac = pattern[pi + 1][0] if pi + 1 < len(pattern) else 1.0
        event_dur = (next_frac - frac) * duration * 0.9

        for idx in indices:
            # Walking Bass approach note: -1 表示下一和弦根音的半音下方
            if idx == -1:
                if next_root_midi is not None:
                    pitch = next_root_midi - 1  # 半音趨近
                    while pitch < LH_LOW:
                        pitch += 12
                    while pitch > LH_HIGH:
                        pitch -= 12
                else:
                    continue
            elif len(voicing) > 0:
                # 自動尋找密集排列替代：若原本的廣跨度被拋棄，透過取餘數讓琶音在合理範圍內盤旋
                pitch = voicing[idx % len(voicing)]
            else:
                continue

            # Stride: 第一拍降八度
            if style == "Stride" and frac < 0.25:
                if pitch - 12 >= LH_LOW:
                    pitch -= 12

            # 旋律防撞
            if _check_melody_conflict(pitch, event_time, event_dur, melody):
                if pitch - 12 >= LH_LOW:
                    pitch -= 12

            velocity = int(base_velocity * vel_ratio)
            events.append({
                "time": round(event_time, 3),
                "duration": round(event_dur, 3),
                "pitch": int(pitch),
                "velocity": velocity,
                "hand": "left",
            })

    # ── L3 Passing Tone (Lesson 12/14): 在最後半拍加經過音趨近下一根音 ──
    if level == "L3" and next_root_midi is not None and style in ("Arpeggio", "Rhythm", "Block"):
        current_root = pitches[0] if pitches else None
        if current_root is not None:
            # 找最近的 next root 在 LH 範圍內的八度位置
            target_pc = next_root_midi % 12
            base_oct = (current_root // 12) * 12
            candidates = [target_pc + base_oct + off for off in (-12, 0, 12)]
            candidates = [c for c in candidates if LH_LOW <= c <= LH_HIGH]
            if candidates:
                target = min(candidates, key=lambda x: abs(x - current_root))
                # 從下方二度趨近 (ascending approach — pop ballad 常用)
                passing = target - 2
                if passing == current_root:
                    passing = target - 1  # 避免重複根音
                # 若超出範圍，改從上方趨近
                if not (LH_LOW <= passing <= LH_HIGH):
                    passing = target + 2
                    if passing == current_root:
                        passing = target + 1
                # 同根音不加經過音
                if target == current_root:
                    passing = None
                if passing is not None and LH_LOW <= passing <= LH_HIGH:
                    pt_time = start_time + duration * 0.875  # 最後 1/8 拍
                    pt_dur = duration * 0.125 * 0.9
                    events.append({
                        "time": round(pt_time, 3),
                        "duration": round(pt_dur, 3),
                        "pitch": int(passing),
                        "velocity": int(base_velocity * 0.6),
                        "hand": "left",
                    })

    return events, pitches


def _find_gaps(melody_segment: List[Dict], chord_start: float,
               chord_end: float, min_gap: float = 0.3) -> List[Tuple[float, float]]:
    """
    找出人聲空白區間 (gaps)。

    在和弦時間範圍內，找出旋律音符之間 >= min_gap 的沉默段。
    回傳: [(gap_start, gap_end), ...]
    """
    # 收集落在此和弦內的旋律區段
    occupied = []
    for m in melody_segment:
        ms = m.get("start", m.get("time", 0))
        me = m.get("end", ms + m.get("duration", 0.5))
        # 裁切到和弦邊界
        s = max(ms, chord_start)
        e = min(me, chord_end)
        if s < e:
            occupied.append((s, e))

    # 按起始排序、合併重疊
    occupied.sort()
    merged = []
    for s, e in occupied:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # 從和弦邊界找空白
    gaps = []
    cursor = chord_start
    for s, e in merged:
        if s - cursor >= min_gap:
            gaps.append((cursor, s))
        cursor = e
    if chord_end - cursor >= min_gap:
        gaps.append((cursor, chord_end))

    return gaps


def _build_fill_notes(chord_notes: List[str], gap_start: float, gap_dur: float,
                      level: str, base_velocity: int,
                      intense: bool = False) -> List[Dict]:
    """
    在人聲空白處生成 fill 音符。

    原則:
      - 不彈根音 (root)，用 3rd / 5th / 7th
      - 一般段落: 單音 fill (1~2 個音)
      - 激情段落 (intense=True): 複音 block chord fill
    """
    # 取非根音的和弦音 (跳過 index 0 = root)
    upper_notes = chord_notes[1:] if len(chord_notes) > 1 else chord_notes
    raw = note_names_to_midi(upper_notes, base_octave=4)
    pitches = clamp_to_range(raw, RH_LOW, RH_HIGH)
    if not pitches:
        return []

    events = []

    if intense:
        # ── 激情段落: 複音 block chord fill ──
        fill_dur = min(gap_dur * 0.8, gap_dur - 0.05)
        for p in pitches[:4]:
            events.append({
                "time": round(gap_start, 3),
                "duration": round(fill_dur, 3),
                "pitch": int(p),
                "velocity": int(base_velocity * 0.75),
                "hand": "right",
                "chord_tone": True,
            })
    else:
        # ── 一般段落: 1~2 個單音 fill ──
        n_notes = 2 if gap_dur > 0.8 and level != "L1" else 1
        note_dur = gap_dur / n_notes * 0.85
        for i in range(min(n_notes, len(pitches))):
            events.append({
                "time": round(gap_start + i * (gap_dur / n_notes), 3),
                "duration": round(note_dur, 3),
                "pitch": int(pitches[i % len(pitches)]),
                "velocity": int(base_velocity * 0.6),
                "hand": "right",
                "chord_tone": True,
            })

    return events


def _build_rh_1plus3(chord_name: str, start_time: float, duration: float,
                     bpm: float, prev_rh_pitches: List[int],
                     base_velocity: int = 85) -> Tuple[List[Dict], List[int]]:
    """
    1+3 配置: 右手每拍彈三個和弦音 block chord (C4 附近)。

    Voice Leading: 選擇離前一組 voicing 最近的轉位。
    參考: NiceChord 好和弦 https://nicechord.com/post/1-plus-3-voicing/
    """
    chord_notes = get_chord_notes(chord_name)
    if not chord_notes:
        return [], prev_rh_pitches

    # 取最多 3 個和弦音 (root, 3rd, 5th 或含 7th 時取 3rd, 5th, 7th)
    if len(chord_notes) >= 4:
        # 有 7th: 用 3rd, 5th, 7th (跳過 root — LH 已經彈了)
        picked = chord_notes[1:4]
    else:
        picked = chord_notes[:3]

    # 轉為 MIDI (C4 附近 = octave 4)
    raw = note_names_to_midi(picked, base_octave=3)
    # 限制在 C3(48) ~ G5(79) — 「中央 C 附近」
    pitches = clamp_to_range(raw, 48, 79)
    if not pitches:
        pitches = clamp_to_range(raw, 48, 84)
    if not pitches:
        return [], prev_rh_pitches

    # Voice Leading: 跟前一組 voicing 最近
    if prev_rh_pitches:
        pitches = voice_leading_optimize(pitches, prev_rh_pitches, 48, 79)

    # 確保恰好 3 個音
    pitches = sorted(set(pitches))[:3]
    if len(pitches) < 3:
        pitches = expand_voicing(pitches, 3, max_span=12)[:3]

    # 計算每拍的時間
    beat_dur = 60.0 / bpm
    n_beats = max(1, int(round(duration / beat_dur)))

    events = []
    for b in range(n_beats):
        beat_time = start_time + b * beat_dur
        if beat_time >= start_time + duration - 0.05:
            break
        note_dur = beat_dur * 0.85  # 留呼吸空間
        vel_ratio = 1.0 if b == 0 else 0.75  # 第一拍稍重
        for p in pitches:
            events.append({
                "time": round(beat_time, 3),
                "duration": round(note_dur, 3),
                "pitch": int(p),
                "velocity": int(base_velocity * vel_ratio),
                "hand": "right",
                "chord_tone": True,
            })

    return events, pitches


def _build_right_hand(chord_name: str, start_time: float, duration: float,
                      level: str, melody_segment: List[Dict],
                      base_velocity: int = 90,
                      rh_mode: str = "melody_only") -> List[Dict]:
    """
    生成單一和弦的右手事件。

    核心原則: RH 伴奏閃避人聲，在空白處補 fill。
      - "arpeggio":       無人聲段 → 和弦琶音 (intro/bridge/outro)
      - "fill_only":      人聲唱時閃開，空白處補單音 fill (verse)
      - "fill_harmony":   同 fill_only + 空白處 fill 稍豐富 (pre_chorus)
      - "fill_block":     空白處補複音 block chord (chorus)

    rh_mode 舊名對照 (保持 API 相容):
      melody_only    → fill_only
      melody_harmony → fill_harmony
      block_melody   → fill_block
    """
    # 舊名相容
    mode_alias = {
        "melody_only": "fill_only",
        "melody_harmony": "fill_harmony",
        "block_melody": "fill_block",
    }
    rh_mode = mode_alias.get(rh_mode, rh_mode)

    events = []
    chord_notes = get_chord_notes(chord_name)
    chord_pitches_pc = set()
    for n in chord_notes:
        chord_pitches_pc.add(root_to_semitone(n) % 12)

    chord_end = start_time + duration

    # ── arpeggio 模式: 右手彈和弦琶音 (intro/bridge/outro 無人聲段) ──
    if rh_mode == "arpeggio":
        raw = note_names_to_midi(chord_notes, base_octave=4)
        pitches = clamp_to_range(raw, RH_LOW, RH_HIGH)
        if not pitches:
            pitches = [RH_LOW]
        voicing = expand_voicing(pitches, 4)
        for pi, (frac, indices, vel_ratio) in enumerate(RH_ARPEGGIO_PATTERN):
            evt_time = start_time + frac * duration
            next_frac = RH_ARPEGGIO_PATTERN[pi + 1][0] if pi + 1 < len(RH_ARPEGGIO_PATTERN) else 1.0
            evt_dur = (next_frac - frac) * duration * 0.9
            for idx in indices:
                pitch = voicing[idx % len(voicing)]
                events.append({
                    "time": round(evt_time, 3),
                    "duration": round(evt_dur, 3),
                    "pitch": int(pitch),
                    "velocity": int(base_velocity * vel_ratio),
                    "hand": "right",
                    "chord_tone": True,
                })
        return events

    # ── fill 模式: 找人聲空白，在 gap 補音 ──
    gaps = _find_gaps(melody_segment, start_time, chord_end)
    intense = (rh_mode == "fill_block")

    for gap_start, gap_end in gaps:
        gap_dur = gap_end - gap_start
        fill_events = _build_fill_notes(
            chord_notes, gap_start, gap_dur,
            level, base_velocity, intense=intense,
        )
        events.extend(fill_events)

    return events


def _check_melody_conflict(pitch: int, time: float, dur: float,
                           melody: List[Dict]) -> bool:
    """檢查伴奏音是否與旋律音太近（< 4 半音）。"""
    for m in melody:
        m_start = m.get("start", m.get("time", 0))
        m_end = m.get("end", m_start + m.get("duration", 0.5))
        m_midi = m.get("midi", 0)
        # 時間重疊檢查
        if m_start < time + dur and m_end > time:
            if abs(m_midi - pitch) < 4:
                return True
    return False


def _filter_hand_collision(lh: List[Dict], rh: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    左右手碰撞過濾: LH 音高不得 >= 同時段 RH 最低音。

    規則: 同一時間窗口 (±0.05s) 內，如果 LH 某音 >= RH 最低音，
    將該 LH 音下移一個八度；若仍超出範圍則移除。
    """
    if not lh or not rh:
        return lh, rh

    # 建立 RH 時間→最低音 mapping
    rh_floor: Dict[float, int] = {}
    for e in rh:
        t = round(e["time"], 2)
        p = e["pitch"]
        if t not in rh_floor or p < rh_floor[t]:
            rh_floor[t] = p

    filtered_lh = []
    for e in lh:
        t = round(e["time"], 2)
        p = e["pitch"]

        # 找最近的 RH 時間窗
        rh_min = None
        for rt, rp in rh_floor.items():
            if abs(rt - t) <= 0.05:
                if rh_min is None or rp < rh_min:
                    rh_min = rp

        if rh_min is not None and p >= rh_min:
            # 下移八度
            new_p = p - 12
            if new_p >= LH_LOW:
                e = {**e, "pitch": new_p}
                filtered_lh.append(e)
            # 否則丟棄 (超出左手範圍)
        else:
            filtered_lh.append(e)

    return filtered_lh, rh


def _filter_lh_span(events: List[Dict], max_span: int = 13) -> List[Dict]:
    """過濾左手音符，限制最大跨距。"""
    time_groups: Dict[float, List[Dict]] = {}
    for e in events:
        t = e["time"]
        if t not in time_groups:
            time_groups[t] = []
        time_groups[t].append(e)

    filtered = []
    for t, group in time_groups.items():
        if not group: continue
        group.sort(key=lambda x: x["pitch"])
        min_pitch = group[0]["pitch"]
        for e in group:
            if e["pitch"] - min_pitch <= max_span:
                filtered.append(e)
    return filtered


def generate_accompaniment(chords: List[Dict],
                           melody: List[Dict] = None,
                           bpm: float = 120.0,
                           style: str = "Block",
                           level: str = "L1",
                           genre: str = "",
                           section_type: str = "default",
                           sections: List[Dict] = None,
                           humanize: float = 1.0) -> Dict[str, Any]:
    """
    主入口：根據和弦序列、旋律、風格與難度，生成左右手 MIDI 伴奏。

    Args:
        chords: [{"time": 0, "end": 4.5, "chord": "Cmaj7"}, ...]
        melody: [{"start": 0.5, "end": 1.0, "midi": 72}, ...]
        bpm: 歌曲 BPM
        style: Block/Arpeggio/Rhythm/Alberti/Shell/Walking/Stride/1+3/Auto
        level: L1(初階)/L2(中階)/L3(進階)
        genre: 曲風字串（用於建議）
        section_type: intro/verse/chorus/bridge/outro/default (legacy)
        sections: [{"type":"verse","start":0,"end":30}, ...] 段落列表
        humanize: 人性化強度 0.0=機械精準, 1.0=正常, 2.0=誇張

    Returns:
        {
          "left_hand": [...events with finger...],
          "right_hand": [...events with finger...],
          "suggested_styles": [...],
          "style": "...",
          "level": "...",
          "section_type": "..."
        }
    """
    melody = melody or []
    sections = sections or []
    auto_mode = (style == "Auto")
    if style not in STYLE_DICT and not auto_mode:
        style = "Block"

    # level 參數保留向後相容，但實際由 STYLE_CONFIG 決定
    # (前端不再傳 level，後端忽略)

    left_events = []
    right_events = []
    prev_lh: List[int] = []
    prev_rh_1plus3: List[int] = []

    for i, chord_evt in enumerate(chords):
        start = chord_evt.get("time", 0)
        end = chord_evt.get("end", start + 2.0)
        duration = end - start
        chord_name = chord_evt.get("chord", "C")

        if duration < 0.1:
            continue

        # ── Per-chord section lookup ──
        chord_section = "default"
        if sections:
            for sec in sections:
                if sec.get("start", 0) <= start < sec.get("end", float("inf")):
                    chord_section = sec.get("type", "default")
                    break

        # ── Auto mode: 段落決定 style ──
        if auto_mode:
            current_style = SECTION_STYLE_MAP.get(chord_section, "Arpeggio")
        else:
            current_style = style

        # 從 STYLE_CONFIG 取得此 style 的固定參數
        cfg = STYLE_CONFIG.get(current_style, STYLE_CONFIG["Block"])
        lh_level = cfg["lh_level"]
        rh_mode = cfg["rh_mode"]
        base_lh_vel = cfg["lh_vel"]
        base_rh_vel = cfg["rh_vel"]

        # Section-aware 力度調變
        density_mult, velocity_mult = SECTION_PARAMS.get(
            chord_section, SECTION_PARAMS["default"]
        )
        lh_velocity = int(base_lh_vel * velocity_mult)
        rh_velocity = int(base_rh_vel * velocity_mult)

        # 計算下一和弦根音 (Walking Bass approach note)
        next_root_midi = None
        if i + 1 < len(chords):
            next_notes = get_chord_notes(chords[i + 1].get("chord", "C"))
            if next_notes:
                next_root_midi = root_to_semitone(next_notes[0]) + 48  # C3 base

        # 左手
        lh, prev_lh = _build_left_hand(
            chord_name, start, duration, current_style, lh_level,
            prev_lh, next_root_midi, melody, lh_velocity
        )
        left_events.extend(lh)

        # ── 右手 ──
        if rh_mode == "1+3":
            rh, prev_rh_1plus3 = _build_rh_1plus3(
                chord_name, start, duration, bpm,
                prev_rh_1plus3, rh_velocity
            )
            right_events.extend(rh)
        else:
            # Auto mode: 段落覆蓋 rh_mode
            actual_rh_mode = RH_SECTION_MODE.get(chord_section, rh_mode) if auto_mode else rh_mode
            rh = _build_right_hand(chord_name, start, duration, lh_level, melody,
                                   rh_velocity, rh_mode=actual_rh_mode)
            right_events.extend(rh)

    # 左手跨度限制過濾
    left_events = _filter_lh_span(left_events, max_span=13)

    # 左右手碰撞過濾: LH 最高音不得 >= RH 最低音 (同時間)
    left_events, right_events = _filter_hand_collision(left_events, right_events)

    # 排序
    left_events.sort(key=lambda e: (e["time"], e["pitch"]))
    right_events.sort(key=lambda e: (e["time"], e["pitch"]))

    # Viterbi 指法推導
    _assign_fingering(left_events, hand="left")
    _assign_fingering(right_events, hand="right")

    # Humanization: timing 微偏移 + velocity 抖動
    if humanize > 0:
        from .dynamics_engine import humanize as _humanize
        _humanize(left_events, bpm=bpm, amount=humanize, seed=42)
        _humanize(right_events, bpm=bpm, amount=humanize, seed=123)

    return {
        "left_hand": left_events,
        "right_hand": right_events,
        "suggested_styles": suggest_style(genre, bpm),
        "style": style,
        "level": level,
        "section_type": section_type,
    }


from .fingering_model import generate_fingers
from .fingering_evaluator import evaluate_fingers

# ==============================================================================
# 伍、AI指法生成與驗證雙軌架構 (Generator-Evaluator Architecture)
# ==============================================================================

def _assign_fingering(events: List[Dict], hand: str = "right"):
    """使用雙軌AI 架構對事件序列注入 finger 欄位（就地修改）。"""
    if not events:
        return

    # 提取按時間點群組
    time_groups: Dict[float, List[Dict]] = {}
    for e in events:
        t = e["time"]
        if t not in time_groups:
            time_groups[t] = []
        time_groups[t].append(e)

    sorted_times = sorted(time_groups.keys())
    if not sorted_times:
        return

    # 取每組的代表音 (右手取最高，左手取最低) 作為旋律/Bass線
    rep_pitches = []
    for t in sorted_times:
        group = time_groups[t]
        rep = max(e["pitch"] for e in group) if hand == "right" else min(e["pitch"] for e in group)
        rep_pitches.append(rep)

    # 1. 呼叫 Generator AI 產生初版指法
    rep_fingers = generate_fingers(rep_pitches, hand=hand)

    # 2. 將指法寫回所有 events (和絃散開邏輯)
    for i, t in enumerate(sorted_times):
        group = time_groups[t]
        base_finger = rep_fingers[i] if i < len(rep_fingers) else (1 if hand == "right" else 5)
        
        group_sorted = sorted(group, key=lambda e: e["pitch"])
        if len(group_sorted) == 1:
            group_sorted[0]["finger"] = base_finger
        else:
            # Chord block
            if hand == "right":
                # 右手：最低音必定是拇指(1)，然後往上分配
                fingers_to_assign = {
                    2: [1, 5],
                    3: [1, 3, 5],
                    4: [1, 2, 3, 5],
                    5: [1, 2, 3, 4, 5]
                }.get(len(group_sorted), [1] * len(group_sorted))
                for j, e in enumerate(group_sorted):
                    if j < len(fingers_to_assign):
                        e["finger"] = fingers_to_assign[j]
            else:
                # 左手：最低音必定是小指(5)，一直到拇指(1)
                fingers_to_assign = {
                    2: [5, 1],
                    3: [5, 3, 1],
                    4: [5, 4, 2, 1],
                    5: [5, 4, 3, 2, 1]
                }.get(len(group_sorted), [5] * len(group_sorted))
                for j, e in enumerate(group_sorted):
                    if j < len(fingers_to_assign):
                        e["finger"] = fingers_to_assign[j]

    # 3. Evaluator QA — 不再全面降級，只記錄警告
    # Phase 11: Generator 的指法品質已大幅提升 (Parncutt model)，
    # 不需要因為少數邊界案例而把整首歌的指法全部覆蓋。
    # 只修正致命跨距 (span > 13)，其他保留 Generator 的結果。
    for t in sorted_times:
        group = time_groups[t]
        if len(group) <= 1:
            continue
        group_sorted = sorted(group, key=lambda e: e["pitch"])
        span = group_sorted[-1]["pitch"] - group_sorted[0]["pitch"]
        if span > 13:
            # 致命跨距: 拉回八度內
            root_p = group_sorted[-1]["pitch"] if hand == "right" else group_sorted[0]["pitch"]
            for e in group_sorted:
                while abs(e["pitch"] - root_p) > 12:
                    if e["pitch"] > root_p: e["pitch"] -= 12
                    else: e["pitch"] += 12


def calculate_physical_cost(delta_p: int, f_prev: int, f_curr: int,
                            hand: str = "right") -> float:
    """計算從前一個手指切換到現有手指的物理移動成本。
    Phase 11a: 委託給 viterbi_engine.fingering_transition_cost。"""
    from .viterbi_engine import fingering_transition_cost
    ctx = {"delta_p": delta_p, "hand": hand, "curr_midi": 60, "bpm": 100}
    return fingering_transition_cost(f_prev, f_curr, ctx)


def viterbi_fingering(pitches: List[int], hand: str = "right") -> List[int]:
    """Viterbi 指法序列生成。
    Phase 11a: 委託給 viterbi_engine.generate_fingering。"""
    from .viterbi_engine import generate_fingering
    return generate_fingering(pitches, hand=hand)


# ==============================================================================
# 陸、測試
# ==============================================================================

if __name__ == "__main__":
    print("=== 1. Style Suggestion ===")
    print("Pop, 100bpm:", suggest_style("pop", 100))
    print("Jazz, 140bpm:", suggest_style("jazz", 140))
    print("Classical, 70bpm:", suggest_style("classical", 70))
    print("Unknown, 120bpm:", suggest_style("", 120))

    print("\n=== 2. Accompaniment Generation ===")
    test_chords = [
        {"time": 0.0, "end": 2.0, "chord": "Cmaj7"},
        {"time": 2.0, "end": 4.0, "chord": "Am7"},
        {"time": 4.0, "end": 6.0, "chord": "Dm7"},
        {"time": 6.0, "end": 8.0, "chord": "G7"},
    ]
    test_melody = [
        {"start": 0.5, "end": 1.5, "midi": 72},  # C5
        {"start": 1.5, "end": 2.5, "midi": 71},  # B4
        {"start": 2.5, "end": 3.5, "midi": 69},  # A4
        {"start": 3.5, "end": 4.5, "midi": 67},  # G4
        {"start": 4.5, "end": 5.5, "midi": 65},  # F4
        {"start": 5.5, "end": 6.5, "midi": 64},  # E4
        {"start": 6.5, "end": 7.5, "midi": 62},  # D4
        {"start": 7.5, "end": 8.0, "midi": 60},  # C4
    ]

    for lvl in ("L1", "L2", "L3"):
        for sty in ("Arpeggio", "Shell", "Walking"):
            result = generate_accompaniment(
                test_chords, test_melody,
                bpm=120, style=sty, level=lvl, genre="jazz"
            )
            lh_count = len(result["left_hand"])
            rh_count = len(result["right_hand"])
            print(f"  {sty:10s} {lvl}: LH={lh_count:2d} events, RH={rh_count:2d} events")

    print("\n=== 3. Arpeggio L2 Detail ===")
    result = generate_accompaniment(
        test_chords, test_melody,
        bpm=120, style="Arpeggio", level="L2", genre="pop"
    )
    print(f"  Suggested styles: {result['suggested_styles']}")
    print("  Left hand (first 8):")
    for e in result["left_hand"][:8]:
        print(f"    t={e['time']:5.2f} p={e['pitch']:3d} v={e['velocity']:2d} f={e['finger']}")
    print("  Right hand (first 8):")
    for e in result["right_hand"][:8]:
        ct = "CT" if e.get("chord_tone") else "PT"
        print(f"    t={e['time']:5.2f} p={e['pitch']:3d} v={e['velocity']:2d} f={e['finger']} {ct}")

    print("\n=== 4. Viterbi Fingering ===")
    scale = [60, 62, 64, 65, 67, 69, 71, 72]
    print(f"  C scale up:   {scale} ->{viterbi_fingering(scale, 'right')}")
    scale_down = [72, 71, 69, 67, 65, 64, 62, 60]
    print(f"  C scale down: {scale_down} ->{viterbi_fingering(scale_down, 'right')}")
    lh_scale = [48, 50, 52, 53, 55, 57, 59, 60]
    print(f"  LH scale up:  {lh_scale} ->{viterbi_fingering(lh_scale, 'left')}")

    print("\n=== All tests passed ===")
