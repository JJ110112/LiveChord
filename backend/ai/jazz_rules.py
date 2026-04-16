"""
爵士樂理規則引擎
Rule 1: Diatonic 7th extensions
Rule 2: 9th/13th extensions
Rule 3: II-V-I insertion
Rule 4: Tritone substitution
Rule 5: Secondary dominants
"""

import re
try:
    from .preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE
except ImportError:
    from preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE

# ---------------------------------------------------------------------------
# 和弦功能分類
# ---------------------------------------------------------------------------

TONIC_DEGREES = {"I", "IIIm", "VIm"}
SUBDOMINANT_DEGREES = {"II", "IIm", "IV"}
DOMINANT_DEGREES = {"V", "VII", "VIIdim"}


def classify_function(degree_root):
    """分類和弦功能：tonic / subdominant / dominant"""
    # 取級數的根部（去掉品質）
    root = re.match(r"^(bVII|#IV|bVI|bIII|bII|VII|VI|IV|III|II|V|I)", degree_root)
    if not root:
        return "ambiguous"
    r = root.group(1)
    if r in ("I", "III", "VI"):
        return "tonic"
    if r in ("II", "IV"):
        return "subdominant"
    if r in ("V", "VII"):
        return "dominant"
    return "ambiguous"


# ---------------------------------------------------------------------------
# Rule 1 & 2: Extensions
# ---------------------------------------------------------------------------

# (大調音階級數, 原品質) → 升級品質
EXTENSION_L1 = {
    # Level 1: 加 7th
    ("I", ""): "maj7",
    ("IIm", ""): "m7", ("IIm", "m"): "m7",
    ("IIIm", ""): "m7", ("IIIm", "m"): "m7",
    ("IV", ""): "maj7",
    ("V", ""): "7",
    ("VIm", ""): "m7", ("VIm", "m"): "m7",
    ("VIIdim", ""): "m7b5", ("VIIdim", "dim"): "m7b5",
    # 小調特殊
    ("Im", ""): "m7", ("Im", "m"): "m7",
    ("IVm", ""): "m7", ("IVm", "m"): "m7",
}

EXTENSION_L2 = {
    # Level 2: 加 9th
    ("I", "maj7"): "maj9",
    ("IIm", "m7"): "m9",
    ("IV", "maj7"): "maj9",
    ("V", "7"): "9",
    ("VIm", "m7"): "m9",
    ("Im", "m7"): "m9",
}

EXTENSION_L3 = {
    # Level 3: 加 13th / altered
    ("V", "9"): "13",
    ("V", "7"): "13",
    ("IIm", "m9"): "m11",
}


def _has_extension(quality):
    """檢查品質是否已有延伸音"""
    return bool(re.search(r"(7|9|11|13|maj7|maj9)", quality))


def add_extension(degree, quality, level=1):
    """為和弦添加爵士延伸音

    Args:
        degree: 級數如 "I", "IIm", "V"
        quality: 和弦品質如 "", "m", "7", "m7"
        level: 1=7th, 2=9th, 3=13th

    Returns:
        新的品質字串，或原品質（無法升級時）
    """
    # 已有更高延伸 → 不重複
    if _has_extension(quality) and level == 1:
        return quality

    # 取級數根部用於查表
    deg_root = re.match(r"^(bVII|#IV|bVI|bIII|bII|VII|VI|IV|III|II|V|I)(m?)", degree)
    if not deg_root:
        return quality
    deg_key = deg_root.group(0)

    # Level 1
    if level >= 1 and not _has_extension(quality):
        new_q = EXTENSION_L1.get((deg_key, quality))
        if new_q:
            quality = new_q

    # Level 2
    if level >= 2:
        new_q = EXTENSION_L2.get((deg_key, quality))
        if new_q:
            quality = new_q

    # Level 3
    if level >= 3:
        new_q = EXTENSION_L3.get((deg_key, quality))
        if new_q:
            quality = new_q

    return quality


# ---------------------------------------------------------------------------
# Rule 3: II-V-I Insertion
# ---------------------------------------------------------------------------

def build_ii_v(target_root_semi):
    """在目標和弦前建立 ii-V

    Args:
        target_root_semi: 目標和弦的根音半音數（作為局部的 I）

    Returns:
        (ii_root_semi, ii_quality, v_root_semi, v_quality)
    """
    # ii = 目標上方大二度 (root + 2) → m7
    ii_semi = (target_root_semi + 2) % 12  # 其實是目標下方小三度... 不對
    # 正確：ii of target = 目標上方純五度再下行，= target - 7 + 2 = target - 5
    # 更簡單：V of target = target 上方純五度 = target + 7
    # ii of V = V - 5 = target + 7 - 5 = target + 2
    v_semi = (target_root_semi + 7) % 12  # V7 of target
    ii_semi = (v_semi - 5) % 12  # ii of target (= V下方純四度)

    # 修正：ii-V-I 中，ii 根音 = I 上方大二度，V = I 上方純五度
    # 如果 target 是 C(0)：ii=D(2)m7, V=G(7)7
    ii_semi = (target_root_semi + 2) % 12
    v_semi = (target_root_semi + 7) % 12

    return ii_semi, "m7", v_semi, "7"


# ---------------------------------------------------------------------------
# Rule 4: Tritone Substitution
# ---------------------------------------------------------------------------

def tritone_sub(root_semi, quality):
    """三全音代理：Dom7 → 三全音距離的 Dom7

    Args:
        root_semi: 原根音半音
        quality: 原品質（必須是 dominant 系列才替換）

    Returns:
        (new_root_semi, new_quality) 或 None（不適用）
    """
    # 只對 dominant 7th 類和弦生效
    if not re.match(r"^(7|9|13|7b9|7#9|7#11|7b13)$", quality):
        return None

    new_root = (root_semi + 6) % 12  # tritone = 6 semitones
    return new_root, quality


# ---------------------------------------------------------------------------
# Rule 5: Secondary Dominants
# ---------------------------------------------------------------------------

def secondary_dominant(target_root_semi):
    """建立二次屬和弦 V7/X

    Args:
        target_root_semi: 目標和弦根音半音

    Returns:
        (sec_dom_root_semi, quality)
    """
    # V7 of target = target 上方純五度
    sec_root = (target_root_semi + 7) % 12
    return sec_root, "7"


# ---------------------------------------------------------------------------
# 工具函數
# ---------------------------------------------------------------------------

def root_name(semi):
    """半音 → 音名"""
    return SEMI_TO_NOTE[semi % 12]


def parse_root_quality(chord_str):
    """解析和弦 → (root_semi, quality) 或 (None, None)"""
    m = re.match(r"^([A-G][b#]?)(.*?)(?:/[A-G][b#]?)?$", chord_str)
    if not m:
        return None, None
    root = m.group(1)
    quality = m.group(2)
    if root not in NOTE_TO_SEMI:
        return None, None
    return NOTE_TO_SEMI[root], quality
