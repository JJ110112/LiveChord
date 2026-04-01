"""
Jazzify 重配和聲引擎
將流行和弦進行轉化為爵士風格

Level 1: 加延伸音（7th）
Level 2: + II-V-I 插入 + 9th
Level 3: + 三全音代理 + 二次屬和弦 + 13th
"""

import copy
from .preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE, chord_to_degree, parse_chord_name
from . import jazz_rules

# 最小可插入持續時間（秒）
MIN_INSERT_DURATION = 1.2


class Reharmonizer:
    def __init__(self, level=1):
        """
        level: 1=extensions, 2=+ii-V+9th, 3=+tritone+secondary dom+13th
        """
        self.level = min(max(level, 1), 3)

    def jazzify(self, chords, key="C"):
        """主入口：重配和聲

        Args:
            chords: [{time, end, chord}, ...]
            key: 調性字串如 "C", "Gm"

        Returns:
            {
                "key": str,
                "level": int,
                "original_count": int,
                "jazzified_count": int,
                "chords": [{time, end, chord, inserted?}, ...],
                "changes": [{position, original, jazzified, rule}, ...]
            }
        """
        if not chords:
            return {"key": key, "level": self.level, "original_count": 0,
                    "jazzified_count": 0, "chords": [], "changes": []}

        # 解析 key
        key_semi = self._parse_key(key)
        is_minor = key.endswith("m") and len(key) > 1

        # 深拷貝避免改動原資料
        result = [copy.deepcopy(c) for c in chords]
        changes = []

        # Pass 1: Extensions
        for i, c in enumerate(result):
            original = c["chord"]
            new_chord = self._apply_extension(original, key_semi, is_minor)
            if new_chord != original:
                c["chord"] = new_chord
                changes.append({
                    "position": i, "original": original,
                    "jazzified": new_chord, "rule": "extension",
                })

        # Pass 2: II-V-I insertion (level >= 2)
        if self.level >= 2:
            result, new_changes = self._insert_ii_v(result, key_semi)
            changes.extend(new_changes)

        # Pass 3: Tritone substitution (level >= 3)
        if self.level >= 3:
            result, new_changes = self._apply_tritone(result, key_semi)
            changes.extend(new_changes)

        # Pass 4: Secondary dominants (level >= 3)
        if self.level >= 3:
            result, new_changes = self._insert_secondary_dom(result, key_semi)
            changes.extend(new_changes)

        # Pass 5: Pattern validation — 偵測並標記已識別的樂理結構
        patterns = self._detect_patterns(result, key)

        return {
            "key": key,
            "level": self.level,
            "original_count": len(chords),
            "jazzified_count": len(result),
            "chords": result,
            "changes": changes,
            "patterns": patterns,
        }

    def _detect_patterns(self, chords, key):
        """Pass 5: 偵測 Jazzify 後的和弦中已識別的樂理結構"""
        from .pattern_extractor import PatternExtractor
        extractor = PatternExtractor()
        chord_names = [c["chord"] for c in chords]
        return extractor.extract_patterns(chord_names, key)

    def _parse_key(self, key):
        """解析調性字串 → 半音數"""
        clean = key.rstrip("m").strip()
        return NOTE_TO_SEMI.get(clean, 0)

    def _apply_extension(self, chord_str, key_semi, is_minor):
        """Pass 1: 為和弦添加延伸音"""
        root_semi, quality = jazz_rules.parse_root_quality(chord_str)
        if root_semi is None:
            return chord_str

        degree = chord_to_degree(chord_str, key_semi)
        if not degree:
            return chord_str

        new_quality = jazz_rules.add_extension(degree, quality, self.level)
        if new_quality == quality:
            return chord_str

        return jazz_rules.root_name(root_semi) + new_quality

    def _insert_ii_v(self, chords, key_semi):
        """Pass 2: 在 dominant 和弦前插入 ii-V"""
        result = []
        changes = []
        skip_next = False

        for i, c in enumerate(chords):
            if skip_next:
                skip_next = False
                result.append(c)
                continue

            root_semi, quality = jazz_rules.parse_root_quality(c["chord"])
            if root_semi is None:
                result.append(c)
                continue

            degree = chord_to_degree(c["chord"], key_semi)
            func = jazz_rules.classify_function(degree) if degree else ""

            # 在 dominant 和弦前插入 ii
            if func == "dominant" and i > 0:
                prev = result[-1]
                prev_duration = prev.get("end", prev["time"] + 2) - prev["time"]

                if prev_duration >= MIN_INSERT_DURATION and not prev.get("inserted"):
                    # ii 根音 = V 根音 - 5 半音
                    ii_semi = (root_semi - 5) % 12

                    # 判斷目標是否為小調：檢查 V 解決到的下一個和弦
                    ii_quality = "m7"
                    if i + 1 < len(chords):
                        next_deg = chord_to_degree(chords[i + 1]["chord"], key_semi)
                        if next_deg and "m" in next_deg and not next_deg.startswith("I"):
                            ii_quality = "m7b5"  # 小調 ii-V: half-diminished
                    ii_name = jazz_rules.root_name(ii_semi) + ii_quality

                    # 分割前一個和弦的時間
                    split_time = prev["time"] + prev_duration * 0.6
                    orig_end = prev.get("end", prev["time"] + prev_duration)

                    prev["end"] = split_time

                    # 插入 ii 和弦
                    ii_chord = {
                        "time": split_time,
                        "end": c["time"],
                        "chord": ii_name,
                        "inserted": True,
                    }
                    result.append(ii_chord)
                    changes.append({
                        "position": len(result) - 1,
                        "original": "-",
                        "jazzified": ii_name,
                        "rule": "ii-V insertion",
                    })

            result.append(c)

        return result, changes

    def _apply_tritone(self, chords, key_semi):
        """Pass 3: 對部分 dominant 7th 和弦套用三全音代理"""
        changes = []
        dom_count = 0

        for i, c in enumerate(chords):
            root_semi, quality = jazz_rules.parse_root_quality(c["chord"])
            if root_semi is None:
                continue

            sub = jazz_rules.tritone_sub(root_semi, quality)
            if sub is None:
                continue

            dom_count += 1
            # 每隔一個 dominant 才替換（避免過度）
            if dom_count % 2 == 0:
                new_root, new_quality = sub
                original = c["chord"]
                c["chord"] = jazz_rules.root_name(new_root) + new_quality
                changes.append({
                    "position": i, "original": original,
                    "jazzified": c["chord"], "rule": "tritone sub",
                })

        return chords, changes

    def _insert_secondary_dom(self, chords, key_semi):
        """Pass 4: 在非主和弦前插入二次屬和弦"""
        result = []
        changes = []
        insert_budget = len(chords) // 8  # 每 8 個和弦最多插一個
        inserted = 0

        for i, c in enumerate(chords):
            root_semi, quality = jazz_rules.parse_root_quality(c["chord"])
            if root_semi is None:
                result.append(c)
                continue

            degree = chord_to_degree(c["chord"], key_semi)
            func = jazz_rules.classify_function(degree) if degree else ""

            # 只在 subdominant/tonic (非 I) 前插入
            if func in ("subdominant", "tonic") and degree and degree != "I":
                if i > 0 and inserted < insert_budget:
                    prev = result[-1]
                    prev_duration = prev.get("end", prev["time"] + 2) - prev["time"]

                    if prev_duration >= MIN_INSERT_DURATION and not prev.get("inserted"):
                        sec_root, sec_quality = jazz_rules.secondary_dominant(root_semi)
                        sec_name = jazz_rules.root_name(sec_root) + sec_quality

                        split_time = prev["time"] + prev_duration * 0.6
                        prev["end"] = split_time

                        sec_chord = {
                            "time": split_time,
                            "end": c["time"],
                            "chord": sec_name,
                            "inserted": True,
                        }
                        result.append(sec_chord)
                        changes.append({
                            "position": len(result) - 1,
                            "original": "-",
                            "jazzified": sec_name,
                            "rule": "secondary dominant",
                        })
                        inserted += 1

            result.append(c)

        return result, changes


# ---- CLI 測試 ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    # 測試：C-F-G-Am 在 C 大調
    test_chords = [
        {"time": 0, "end": 4, "chord": "C"},
        {"time": 4, "end": 8, "chord": "F"},
        {"time": 8, "end": 12, "chord": "G"},
        {"time": 12, "end": 16, "chord": "Am"},
        {"time": 16, "end": 20, "chord": "F"},
        {"time": 20, "end": 24, "chord": "G"},
        {"time": 24, "end": 28, "chord": "C"},
    ]

    for level in (1, 2, 3):
        rh = Reharmonizer(level=level)
        result = rh.jazzify(test_chords, key="C")
        print(f"\n=== Level {level} ({result['jazzified_count']} chords, {len(result['changes'])} changes) ===")
        for c in result["chords"]:
            ins = " [NEW]" if c.get("inserted") else ""
            print(f"  {c['time']:5.1f}-{c['end']:5.1f}  {c['chord']}{ins}")
        print(f"  Changes: {[ch['rule'] + ': ' + ch['jazzified'] for ch in result['changes']]}")
