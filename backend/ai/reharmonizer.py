"""
Jazzify 重配和聲引擎
將流行和弦進行轉化為爵士風格

Level 1: 加延伸音（7th）
Level 2: + II-V-I 插入 + 9th
Level 3: + 三全音代理 + 二次屬和弦 + 13th
"""

import copy
try:
    from .preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE, chord_to_degree, parse_chord_name
    from . import jazz_rules
except ImportError:
    from preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE, chord_to_degree, parse_chord_name
    import jazz_rules

# 最小可插入持續時間（秒）。120 BPM 預設，慢歌會依 bpm 放寬避免把半小節
# 塞 ii-V。實際值由 _min_insert_duration() 動態產生。
MIN_INSERT_DURATION = 1.2


def _min_insert_duration(bpm):
    """回傳給定 BPM 下的最小可插入秒數（至少兩拍）。

    120 BPM -> 1.0s, 70 BPM -> 1.71s, 160 BPM -> 0.75s。下限 0.6s 避免
    非常快的曲子完全擋掉插入。
    """
    try:
        b = float(bpm) if bpm else 120.0
    except (TypeError, ValueError):
        b = 120.0
    b = max(40.0, min(240.0, b))
    beat_sec = 60.0 / b
    return max(0.6, beat_sec * 2)


class Reharmonizer:
    def __init__(self, level=1):
        """
        level: 1=extensions, 2=+ii-V+9th, 3=+tritone+secondary dom+13th
        """
        self.level = min(max(level, 1), 3)

    def jazzify(self, chords, key="C", melody_data=None, mode="rule-based", bpm=None, strand_flags=None):
        """主入口：重配和聲

        Args:
            chords: [{time, end, chord}, ...]
            melody_data: [{start, end, midi}, ...] 可選旋律資料
            key: 調性字串如 "C", "Gm"
            mode: "rule-based" 或是 "transformer"
            bpm: 歌曲 BPM（含 ballad halving 修正後）。用於動態縮放插入閾值，
                 慢歌下 ii-V / 二次屬插入的 prev_duration 下限會放寬。
              strand_flags: 指定啟用的和聲策略。空值時沿用既有 level 1/2/3 行為。

        Returns:
            {
                "key": str,
                "level": int,
                "strand_flags": [str, ...],
                "original_count": int,
                "jazzified_count": int,
                "chords": [{time, end, chord, inserted?, explain:{strand, source}}, ...],
                "changes": [{position, original, jazzified, rule, strand}, ...],
                "explain": [{step, position, strand, rule, from, to}, ...]
            }
        """
        normalized_strands, unknown_strands = self._normalize_strand_flags(strand_flags)
        not_implemented_strands = []
        custom_strands = bool(normalized_strands)

        enable_diatonic = ("diatonic" in normalized_strands) if custom_strands else True
        enable_ii_v = ("ii_v" in normalized_strands) if custom_strands else self.level >= 2
        enable_tritone = ("tritone_sub" in normalized_strands) if custom_strands else self.level >= 3
        enable_secondary = ("secondary_dominant" in normalized_strands) if custom_strands else self.level >= 3
        enable_dim_leading = ("diminished_leading" in normalized_strands) if custom_strands else False
        enable_modal_interchange = ("modal_interchange" in normalized_strands) if custom_strands else False
        enable_five_alternatives = ("five_alternatives" in normalized_strands) if custom_strands else False

        active_strands = []
        if enable_diatonic:
            active_strands.append("diatonic")
        if enable_ii_v:
            active_strands.append("ii_v")
        if enable_tritone:
            active_strands.append("tritone_sub")
        if enable_secondary:
            active_strands.append("secondary_dominant")
        if enable_dim_leading:
            active_strands.append("diminished_leading")
        if enable_modal_interchange:
            active_strands.append("modal_interchange")
        if enable_five_alternatives:
            active_strands.append("five_alternatives")

        if not chords:
            return {
                "key": key,
                "level": self.level,
                "strand_flags": active_strands,
                "unknown_strands": unknown_strands,
                "not_implemented_strands": not_implemented_strands,
                "original_count": 0,
                "jazzified_count": 0,
                "chords": [],
                "changes": [],
                "explain": [],
            }

        # 解析 key
        key_semi = self._parse_key(key)
        is_minor = key.endswith("m") and len(key) > 1
        min_insert = _min_insert_duration(bpm)
        self._min_insert = min_insert

        # 深拷貝避免改動原資料
        result = [copy.deepcopy(c) for c in chords]
        changes = []

        if mode == "transformer":
            return self._apply_transformer(
                chords,
                result,
                key_semi,
                melody_data,
                key,
                active_strands,
                unknown_strands,
                not_implemented_strands,
            )

        # Pass 1: Extensions
        if enable_diatonic:
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
        if enable_ii_v:
            result, new_changes = self._insert_ii_v(result, key_semi)
            changes.extend(new_changes)

        # Pass 3: Tritone substitution (level >= 3)
        if enable_tritone:
            result, new_changes = self._apply_tritone(result, key_semi)
            changes.extend(new_changes)

        # Pass 4: Secondary dominants (level >= 3)
        if enable_secondary:
            result, new_changes = self._insert_secondary_dom(result, key_semi)
            changes.extend(new_changes)

        # Pass 4.2: Diminished leading-tone insertions (Phase 1)
        if enable_dim_leading:
            result, new_changes = self._insert_diminished_leading(result, key_semi)
            changes.extend(new_changes)

        # Pass 4.3: Modal interchange substitutions
        if enable_modal_interchange:
            result, new_changes = self._apply_modal_interchange(result, key_semi)
            changes.extend(new_changes)

        # Pass 4.4: Five-chord alternatives for V->I motion
        if enable_five_alternatives:
            result, new_changes = self._apply_five_alternatives(result, key_semi)
            changes.extend(new_changes)

        # Pass 4.5: Phrase tension arc — 確保樂句有起承轉合
        if self.level >= 2:
            result = self._balance_phrase_tension(result, key_semi)

        # Pass 4.6: Pitch-Class Overlap 回退 — 跟原和弦差太多就降級
        result, overlap_fixes = self._overlap_downgrade(chords, result)
        changes.extend(overlap_fixes)

        # Pass 4.7: 旋律避撞 — 如果有旋律資料，避開小二度衝突
        result, melody_fixes = self._melody_avoid(result, key_semi, melody_data)
        changes.extend(melody_fixes)

        # Pass 5: Pattern validation — 偵測並標記已識別的樂理結構
        patterns = self._detect_patterns(result, key)

        # Pass 6: 大亂鬥 (Multi-Agent QA Battle)
        qa_reports = self._run_qa_battle(chords, result)
        changes = self._finalize_changes(changes)
        result, explain_chords = self._annotate_chord_explain(result, changes)
        explain = self._build_explain(changes)

        return {
            "key": key,
            "level": self.level,
            "strand_flags": active_strands,
            "unknown_strands": unknown_strands,
            "not_implemented_strands": not_implemented_strands,
            "original_count": len(chords),
            "jazzified_count": len(result),
            "chords": result,
            "changes": changes,
            "explain": explain,
            "explain_chords": explain_chords,
            "patterns": patterns,
            "qa": qa_reports,
        }

    def _run_qa_battle(self, original_chords, jazzified_chords):
        try:
            try:
                from .musician_qa import run_musician_qa
                from .producer_qa import run_producer_qa
            except ImportError:
                from musician_qa import run_musician_qa
                from producer_qa import run_producer_qa
            
            musician_report = run_musician_qa(jazzified_chords, level=self.level)
            producer_report = run_producer_qa(original_chords, jazzified_chords)
            
            # Combine the battle logs
            battle_logs = []
            if musician_report.get("warnings"):
                battle_logs.extend(["🎸 樂手抗議: " + w for w in musician_report["warnings"]])
            if producer_report.get("warnings"):
                battle_logs.extend(["🎧 製作人抓狂: " + w for w in producer_report["warnings"]])
                
            return {
                "musician_score": musician_report.get("playability_score", 100),
                "producer_score": producer_report.get("mix_score", 100),
                "battle_logs": battle_logs
            }
        except ImportError:
            # Fallback if modules aren't there
            return {"musician_score": 100, "producer_score": 100, "battle_logs": []}

    def _overlap_downgrade(self, original, jazzified):
        """Pass 4.6: 如果 jazz 和弦跟原和弦 pitch-class overlap < 0.3，降級回原和弦+7th

        避免 Jazzify 把和弦改得面目全非
        """
        try:
            from .evaluate import pitch_class_overlap
        except ImportError:
            from evaluate import pitch_class_overlap
        changes = []
        j = 0
        for i in range(len(original)):
            if j >= len(jazzified):
                break
            # 跳過插入的和弦
            while j < len(jazzified) and jazzified[j].get("inserted"):
                j += 1
            if j >= len(jazzified):
                break

            orig_name = original[i].get("chord", "")
            jazz_name = jazzified[j].get("chord", "")
            ov = pitch_class_overlap(orig_name, jazz_name)

            if ov < 0.3 and orig_name != jazz_name:
                # 降級：用原和弦 + 基本 7th
                root_semi, quality = jazz_rules.parse_root_quality(orig_name)
                if root_semi is not None:
                    degree = chord_to_degree(orig_name, 0)  # 用 C 做基準即可
                    safe = jazz_rules.add_extension(degree or "I", quality, level=1)
                    safe_chord = jazz_rules.root_name(root_semi) + safe
                    old = jazzified[j]["chord"]
                    jazzified[j]["chord"] = safe_chord
                    changes.append({
                        "position": j, "original": old,
                        "jazzified": safe_chord,
                        "rule": f"overlap downgrade ({ov:.0%}→safe)",
                    })
            j += 1

        return jazzified, changes

    def _melody_avoid(self, chords, key_semi, melody_data):
        """Pass 4.7: 避免 jazz 和弦的組成音跟旋律音形成小二度衝突 (avoid notes)

        小二度 = 1 半音差，是最刺耳的不協和音程
        如果和弦某個延伸音跟當前旋律音只差 1 半音 → 降級該和弦
        """
        if not melody_data:
            return chords, []

        changes = []

        # 建立和弦組成音查詢
        INTERVALS = {
            "": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10],
            "m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11],
            "9": [0, 4, 7, 10, 14], "m9": [0, 3, 7, 10, 14],
            "maj9": [0, 4, 7, 11, 14], "13": [0, 4, 7, 10, 14, 21],
            "m7b5": [0, 3, 6, 10], "dim7": [0, 3, 6, 9],
        }

        def _get_pcs(chord_str):
            root, quality = jazz_rules.parse_root_quality(chord_str)
            if root is None:
                return set()
            ivs = INTERVALS.get(quality, INTERVALS.get("", [0, 4, 7]))
            return {(root + iv) % 12 for iv in ivs}

        def _simplify(chord_str):
            """去掉最外層延伸音"""
            import re
            for ext in ["13", "11", "9"]:
                if ext in chord_str:
                    return re.sub(ext + r"[b#]?\d*", "7", chord_str, count=1)
            return chord_str

        for i, c in enumerate(chords):
            t = c["time"]
            # 找當前時間的旋律音
            melody_midi = -1
            for m in melody_data:
                if m["start"] <= t <= m["end"]:
                    melody_midi = m["midi"]
                    break
            if melody_midi < 0:
                continue

            melody_pc = melody_midi % 12
            chord_pcs = _get_pcs(c["chord"])

            # 檢查小二度衝突
            has_clash = False
            for pc in chord_pcs:
                if abs(pc - melody_pc) == 1 or abs(pc - melody_pc) == 11:
                    has_clash = True
                    break

            if has_clash:
                old = c["chord"]
                simplified = _simplify(old)
                # 再檢查簡化後是否還衝突
                if simplified != old:
                    new_pcs = _get_pcs(simplified)
                    still_clash = any(abs(pc - melody_pc) in (1, 11) for pc in new_pcs)
                    if not still_clash:
                        c["chord"] = simplified
                        changes.append({
                            "position": i, "original": old,
                            "jazzified": simplified,
                            "rule": f"melody avoid (note={melody_pc}→clash)",
                        })

        return chords, changes

    def _balance_phrase_tension(self, chords, key_semi):
        """Pass 4.5: 確保 8 小節樂句有「低→高→解決」的張力弧度

        原理：把和弦序列分成 ~8 個一組的樂句
        樂句前半應該張力漸增（不能直接放最複雜的和弦）
        樂句末尾應該有解決感（回到 tonic 或 dominant→tonic）
        如果張力分佈不合理，降級部分和弦的延伸音
        """
        if len(chords) < 8:
            return chords

        PHRASE_LEN = 8  # 大約 8 個和弦為一個樂句

        def _tension_score(chord_str):
            """和弦張力分數 0-5"""
            s = 0
            if any(m in chord_str for m in ["13", "b9", "#9", "alt"]): s += 4
            elif any(m in chord_str for m in ["9", "11", "dim"]): s += 3
            elif any(m in chord_str for m in ["7"]): s += 2
            elif any(m in chord_str for m in ["m"]): s += 1
            return s

        def _simplify(chord_str):
            """降級和弦：去掉最外層延伸"""
            import re
            for ext in ["13", "11", "9"]:
                if ext in chord_str and "maj" not in chord_str[:4]:
                    return re.sub(ext + r"[b#]?\d*", "7", chord_str, count=1)
            return chord_str

        for start in range(0, len(chords) - PHRASE_LEN + 1, PHRASE_LEN):
            phrase = chords[start:start + PHRASE_LEN]
            tensions = [_tension_score(c["chord"]) for c in phrase]

            # 檢查：前 1/4 不應比後 3/4 更緊張
            front_avg = sum(tensions[:2]) / 2 if tensions[:2] else 0
            back_avg = sum(tensions[2:6]) / max(len(tensions[2:6]), 1)

            if front_avg > back_avg + 1.5:
                # 前面太緊張，降級前兩個和弦
                for i in range(min(2, len(phrase))):
                    old = phrase[i]["chord"]
                    simplified = _simplify(old)
                    if simplified != old:
                        phrase[i]["chord"] = simplified

            # 最後一個和弦如果張力太高（>3），降級以提供解決感
            if tensions and tensions[-1] >= 4:
                old = phrase[-1]["chord"]
                simplified = _simplify(old)
                if simplified != old:
                    phrase[-1]["chord"] = simplified

        return chords

    def _apply_transformer(self, original_chords, result_chords, key_semi, melody_data, key_str,
                           active_strands, unknown_strands, not_implemented_strands):
        """使用神經網路進行 Jazzify"""
        changes = []
        transformer_error = None
        active_set = set(active_strands or [])
        enable_diatonic = "diatonic" in active_set
        enable_ii_v = "ii_v" in active_set
        enable_tritone = "tritone_sub" in active_set
        enable_secondary = "secondary_dominant" in active_set
        enable_dim_leading = "diminished_leading" in active_set
        enable_modal_interchange = "modal_interchange" in active_set
        enable_five_alternatives = "five_alternatives" in active_set
        try:
            import torch
            import json
            from pathlib import Path
            from .tokenizer import tokenize_song
            from .preprocess import transpose_chord
            from .transformer_reharmonizer import TransformerReharmonizer, greedy_decode
            
            models_dir = Path("V:/data/models")
            if not models_dir.exists():
                models_dir = Path(__file__).parent.parent.parent / "data" / "models"
                
            model_path = models_dir / "transformer_jazzify.pth"
            vocab_path = models_dir / "vocab.json"
            
            if not model_path.exists() or not vocab_path.exists():
                raise FileNotFoundError("Transformer model or vocab missing. Train it first!")
                
            with open(vocab_path, 'r', encoding='utf-8') as f:
                vocab = json.load(f)
            inv_vocab = {v: k for k, v in vocab.items()}
            
            device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            model = TransformerReharmonizer(vocab_size=len(vocab), d_model=256, nhead=8, num_encoder_layers=4, num_decoder_layers=4, dim_feedforward=512, dropout=0.1).to(device)
            model.load_state_dict(torch.load(model_path, map_location=device))
            model.eval()
            
            # Step 1: Tokenize to C Major
            tokens = tokenize_song(original_chords, key_str=key_str)
            if tokens:
                # Step 2: Convert to tensor
                src_indices = [vocab.get(t, vocab.get("<UNK>", 3)) for t in tokens]
                src_tensor = torch.tensor(src_indices, dtype=torch.long).unsqueeze(1).to(device)
                src_mask = torch.zeros((len(src_indices), len(src_indices)), dtype=torch.bool).to(device)
                
                sos_idx = vocab.get("<SOS>", 1)
                eos_idx = vocab.get("<EOS>", 2)
                
                # Step 3: Decode
                with torch.no_grad():
                    ys, probs = greedy_decode(model, src_tensor, src_mask, max_len=128, start_symbol=sos_idx, end_symbol=eos_idx, device=device)
                    
                out_indices = ys.squeeze(1).cpu().tolist()
                
                # Step 4: Reconstruct Timeline & Transpose back
                current_time = original_chords[0]["time"] if original_chords else 0.0
                new_chords = []
                
                i = 1 
                while i < len(out_indices):
                    idx = out_indices[i]
                    if idx == eos_idx: break
                    
                    token = inv_vocab.get(idx, "")
                    
                    dur_val = 2.0
                    if i + 1 < len(out_indices):
                        next_idx = out_indices[i+1]
                        next_token = inv_vocab.get(next_idx, "")
                        if next_token.startswith("DUR_"):
                            try:
                                dur_val = float(next_token.replace("DUR_", ""))
                            except ValueError:
                                pass
                            i += 1
                    
                    if not token.startswith("DUR_") and not token.startswith("<"):
                        # Transpose back FROM C Major to the original key
                        final_chord = transpose_chord(token, key_semi)
                        
                        new_chords.append({
                            "time": current_time,
                            "end": current_time + dur_val,
                            "chord": final_chord
                        })
                        current_time += dur_val
                        
                    i += 1
                    
                # Transformer decoder sequence length is bounded by max_len +
                # model-learned EOS; accumulated `current_time += dur_val` is
                # almost always SHORTER than the original song. Rescale the
                # time axis so the jazzified progression spans the full song
                # (otherwise the back half has no chord to follow).
                if new_chords and original_chords:
                    orig_start = original_chords[0].get("time", 0.0)
                    orig_end = max(c.get("end", c.get("time", 0.0)) for c in original_chords)
                    trans_start = new_chords[0].get("time", 0.0)
                    trans_end = new_chords[-1].get("end", new_chords[-1].get("time", 0.0))
                    orig_span = orig_end - orig_start
                    trans_span = trans_end - trans_start
                    if trans_span > 0 and orig_span > 0 and abs(orig_span - trans_span) > 1.0:
                        scale = orig_span / trans_span
                        for c in new_chords:
                            c["time"] = orig_start + (c.get("time", 0.0) - trans_start) * scale
                            if "end" in c:
                                c["end"] = orig_start + (c["end"] - trans_start) * scale

                if new_chords:
                    result_chords = new_chords
                    changes.append({
                        "position": 0, "original": "POP",
                        "jazzified": "JAZZ", "rule": "Transformer Seq2Seq Inference",
                    })
        except Exception as e:
            print(f"Transformer Error: {e}")
            import traceback
            traceback_str = traceback.format_exc()
            transformer_error = str(e)
            changes.append({
                "position": 0, "original": "-", "jazzified": "Error",
                "rule": f"TRANSFORMER FAILED: {str(e)} | Trace: {traceback_str}"
            })

        # Post-process transformer output through rule engine at self.level
        is_minor = key_str.endswith("m") and len(key_str) > 1

        if enable_diatonic:
            for i, c in enumerate(result_chords):
                original = c["chord"]
                new_chord = self._apply_extension(original, key_semi, is_minor)
                if new_chord != original:
                    c["chord"] = new_chord
                    changes.append({
                        "position": i, "original": original,
                        "jazzified": new_chord, "rule": "transformer+extension",
                    })

        if enable_ii_v:
            result_chords, new_changes = self._insert_ii_v(result_chords, key_semi)
            changes.extend(new_changes)

        if enable_tritone:
            result_chords, new_changes = self._apply_tritone(result_chords, key_semi)
            changes.extend(new_changes)

        if enable_secondary:
            result_chords, new_changes = self._insert_secondary_dom(result_chords, key_semi)
            changes.extend(new_changes)

        if enable_dim_leading:
            result_chords, new_changes = self._insert_diminished_leading(result_chords, key_semi)
            changes.extend(new_changes)

        if enable_modal_interchange:
            result_chords, new_changes = self._apply_modal_interchange(result_chords, key_semi)
            changes.extend(new_changes)

        if enable_five_alternatives:
            result_chords, new_changes = self._apply_five_alternatives(result_chords, key_semi)
            changes.extend(new_changes)

        if self.level >= 2:
            result_chords = self._balance_phrase_tension(result_chords, key_semi)

        result_chords, overlap_fixes = self._overlap_downgrade(original_chords, result_chords)
        changes.extend(overlap_fixes)

        # Viterbi 旋律保護層
        result_chords, melody_fixes = self._melody_avoid(result_chords, key_semi, melody_data)
        changes.extend(melody_fixes)

        patterns = self._detect_patterns(result_chords, key_str)
        qa_reports = self._run_qa_battle(original_chords, result_chords)
        changes = self._finalize_changes(changes)
        result_chords, explain_chords = self._annotate_chord_explain(result_chords, changes)
        explain = self._build_explain(changes)

        return {
            "key": key_str,
            "level": self.level,
            "strand_flags": list(active_strands or []),
            "unknown_strands": list(unknown_strands or []),
            "not_implemented_strands": list(not_implemented_strands or []),
            "original_count": len(original_chords),
            "jazzified_count": len(result_chords),
            "chords": result_chords,
            "changes": changes,
            "explain": explain,
            "explain_chords": explain_chords,
            "patterns": patterns,
            "qa": qa_reports,
            "error": transformer_error,
        }

    def _detect_patterns(self, chords, key):
        """Pass 5: 偵測 Jazzify 後的和弦中已識別的樂理結構"""
        try:
            from .pattern_extractor import PatternExtractor
        except ImportError:
            from pattern_extractor import PatternExtractor
        extractor = PatternExtractor()
        chord_names = [c["chord"] for c in chords]
        return extractor.extract_patterns(chord_names, key)

    def _parse_key(self, key):
        """解析調性字串 → 半音數"""
        clean = key.rstrip("m").strip()
        return NOTE_TO_SEMI.get(clean, 0)

    def _normalize_strand_flags(self, strand_flags):
        """Normalize user-provided strand names to canonical keys."""
        if not strand_flags:
            return [], []
        valid = {
            "diatonic",
            "secondary_dominant",
            "modal_interchange",
            "ii_v",
            "tritone_sub",
            "diminished_leading",
            "five_alternatives",
        }
        alias = {
            "secondary": "secondary_dominant",
            "secondary_dominants": "secondary_dominant",
            "ii-v": "ii_v",
            "iiv": "ii_v",
            "tritone": "tritone_sub",
            "modal": "modal_interchange",
            "borrow": "modal_interchange",
            "diminished": "diminished_leading",
            "dim": "diminished_leading",
            "five": "five_alternatives",
            "five-alternatives": "five_alternatives",
            "five_chord_alternatives": "five_alternatives",
        }
        normalized = []
        unknown = []
        for raw in strand_flags:
            if not isinstance(raw, str):
                continue
            key = raw.strip().lower()
            if not key:
                continue
            key = alias.get(key, key)
            if key in valid:
                if key not in normalized:
                    normalized.append(key)
            else:
                if key not in unknown:
                    unknown.append(key)
        return normalized, unknown

    def _rule_to_strand(self, rule):
        s = (rule or "").lower()
        if "ii-v" in s:
            return "ii_v"
        if "tritone" in s:
            return "tritone_sub"
        if "secondary dominant" in s:
            return "secondary_dominant"
        if "diminished leading" in s:
            return "diminished_leading"
        if "modal interchange" in s:
            return "modal_interchange"
        if "five alternative" in s:
            return "five_alternatives"
        return "diatonic"

    def _finalize_changes(self, changes):
        for c in changes:
            if "strand" not in c:
                c["strand"] = self._rule_to_strand(c.get("rule", ""))
        return changes

    def _build_explain(self, changes):
        explain = []
        for idx, c in enumerate(changes):
            explain.append({
                "step": idx + 1,
                "position": c.get("position", -1),
                "strand": c.get("strand", "diatonic"),
                "rule": c.get("rule", ""),
                "from": c.get("original", ""),
                "to": c.get("jazzified", ""),
            })
        return explain

    def _annotate_chord_explain(self, chords, changes):
        """Attach per-chord strand/source metadata for teaching UI labels."""
        if not chords:
            return chords, []

        pos_to_strand = {}
        for c in changes:
            try:
                pos = int(c.get("position", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= pos < len(chords):
                pos_to_strand[pos] = c.get("strand") or self._rule_to_strand(c.get("rule", ""))

        explain_chords = []
        for idx, chord in enumerate(chords):
            strand = pos_to_strand.get(idx, "diatonic")
            if idx in pos_to_strand:
                source = "changed"
            elif chord.get("inserted"):
                source = "inserted"
            else:
                source = "carried"

            chord["explain"] = {
                "strand": strand,
                "source": source,
            }
            explain_chords.append({
                "position": idx,
                "time": chord.get("time", 0),
                "chord": chord.get("chord", ""),
                "strand": strand,
                "source": source,
            })

        return chords, explain_chords

    def _insert_diminished_leading(self, chords, key_semi):
        """Pass 4.2: Insert leading-tone diminished 7th before target chords."""
        result = []
        changes = []
        insert_budget = max(1, len(chords) // 10)
        inserted = 0

        for i, c in enumerate(chords):
            root_semi, quality = jazz_rules.parse_root_quality(c.get("chord", ""))
            if root_semi is None:
                result.append(c)
                continue

            degree = chord_to_degree(c["chord"], key_semi)
            func = jazz_rules.classify_function(degree) if degree else ""
            quality_l = (quality or "").lower()

            if (
                i > 0 and
                inserted < insert_budget and
                func in ("tonic", "subdominant") and
                "dim" not in quality_l and
                "m7b5" not in quality_l
            ):
                prev = result[-1]
                prev_duration = prev.get("end", prev["time"] + 2) - prev["time"]
                if prev_duration >= getattr(self, "_min_insert", MIN_INSERT_DURATION) and not prev.get("inserted"):
                    dim_root = (root_semi - 1) % 12
                    dim_name = jazz_rules.root_name(dim_root) + "dim7"
                    if prev.get("chord") != dim_name and c.get("chord") != dim_name:
                        split_time = prev["time"] + prev_duration * 0.6
                        if split_time < c["time"]:
                            prev["end"] = split_time
                            dim_chord = {
                                "time": split_time,
                                "end": c["time"],
                                "chord": dim_name,
                                "inserted": True,
                            }
                            result.append(dim_chord)
                            changes.append({
                                "position": len(result) - 1,
                                "original": "-",
                                "jazzified": dim_name,
                                "rule": "diminished leading-tone",
                            })
                            inserted += 1

            result.append(c)

        return result, changes

    def _apply_modal_interchange(self, chords, key_semi):
        """Pass 4.3: Borrow selected colors from parallel minor modes."""
        result = [copy.deepcopy(c) for c in chords]
        changes = []
        budget = max(1, len(result) // 8)
        used = 0

        for i, c in enumerate(result):
            if used >= budget:
                break
            if c.get("inserted"):
                continue

            chord_name = c.get("chord", "")
            root_semi, quality = jazz_rules.parse_root_quality(chord_name)
            if root_semi is None:
                continue
            degree = chord_to_degree(chord_name, key_semi)
            if not degree:
                continue

            new_name = None
            quality_l = (quality or "").lower()

            # Classic modal interchange color: IV -> ivm7
            if degree == "IV" and "m" not in quality_l:
                new_name = jazz_rules.root_name(root_semi) + "m7"
            # Borrowed bIImaj7 from Phrygian flavor.
            elif degree.startswith("II") and not degree.startswith("IIm"):
                new_name = jazz_rules.root_name((root_semi - 1) % 12) + "maj7"
            # Borrowed bVImaj7 from Aeolian flavor.
            elif degree.startswith("VI") and "m" not in quality_l:
                new_name = jazz_rules.root_name((root_semi - 1) % 12) + "maj7"

            if not new_name or new_name == chord_name:
                continue

            old = c["chord"]
            c["chord"] = new_name
            changes.append({
                "position": i,
                "original": old,
                "jazzified": new_name,
                "rule": "modal interchange",
            })
            used += 1

        return result, changes

    def _apply_five_alternatives(self, chords, key_semi):
        """Pass 4.4: Replace V-function chords with classic alternatives before I."""
        result = [copy.deepcopy(c) for c in chords]
        changes = []
        budget = max(1, len(result) // 10)
        used = 0

        alt_cycle = [
            ("backdoor dominant", (key_semi + 10) % 12, "7"),
            ("tritone sub", (key_semi + 1) % 12, "7"),
            ("diminished family", (key_semi + 11) % 12, "dim7"),
            ("minor-half-diminished family", (key_semi + 2) % 12, "m7b5"),
            ("minor-half-diminished family", (key_semi + 5) % 12, "m7b5"),
        ]

        for i in range(len(result) - 1):
            if used >= budget:
                break

            cur = result[i]
            nxt = result[i + 1]
            if cur.get("inserted"):
                continue

            cur_root, _ = jazz_rules.parse_root_quality(cur.get("chord", ""))
            nxt_root, _ = jazz_rules.parse_root_quality(nxt.get("chord", ""))
            if cur_root is None or nxt_root is None:
                continue

            cur_deg = chord_to_degree(cur.get("chord", ""), key_semi)
            nxt_deg = chord_to_degree(nxt.get("chord", ""), key_semi)

            # Only apply when a dominant-function chord resolves to tonic.
            if not (cur_deg and nxt_deg and nxt_root == key_semi and nxt_deg == "I"):
                continue
            if jazz_rules.classify_function(cur_deg) != "dominant":
                continue

            tag, alt_root, alt_quality = alt_cycle[used % len(alt_cycle)]
            new_name = jazz_rules.root_name(alt_root) + alt_quality
            old = cur["chord"]
            if new_name == old:
                continue

            cur["chord"] = new_name
            changes.append({
                "position": i,
                "original": old,
                "jazzified": new_name,
                "rule": f"five alternatives ({tag})",
            })
            used += 1

        return result, changes

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

                if prev_duration >= getattr(self, "_min_insert", MIN_INSERT_DURATION) and not prev.get("inserted"):
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

                    if prev_duration >= getattr(self, "_min_insert", MIN_INSERT_DURATION) and not prev.get("inserted"):
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
