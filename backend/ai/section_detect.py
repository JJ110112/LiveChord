"""
段落偵測器 V3 (Section Detector)
整合三版精華：
- BPM 動態窗口（簡單歌 2 小節、流行歌 4 小節）
- Fingerprint + Jaccard 相似度比對
- 簡單歌：A1/A2/A3 編號、A' 變形偵測、ii-V 自動判 B 段
- 流行歌：密度+複雜度+重複模式 → 主歌/副歌/橋段
- 合併平滑化（Jaccard > 0.95 強制合併）
"""

import math
import re
from collections import Counter
import sys
from pathlib import Path

try:
    from .preprocess import chord_to_degree, NOTE_TO_SEMI, parse_chord_name
except ImportError:
    from preprocess import chord_to_degree, NOTE_TO_SEMI, parse_chord_name
try:
    import mido
except ImportError:
    mido = None


SECTION_TYPES = {
    "dialogue": {"emoji": "🗣️", "label": "對白", "color": "#795548"},
    "intro": {"emoji": "🎬", "label": "前奏", "color": "#888"},
    "verse": {"emoji": "🎤", "label": "主歌", "color": "#4caf50"},
    "pre_chorus": {"emoji": "⬆️", "label": "導歌", "color": "#ff9800"},
    "chorus": {"emoji": "🎵", "label": "副歌", "color": "#e94560"},
    "bridge": {"emoji": "🌉", "label": "橋段", "color": "#9c27b0"},
    "outro": {"emoji": "🔚", "label": "尾奏", "color": "#607d8b"},
    "instrumental": {"emoji": "🎸", "label": "間奏", "color": "#2196f3"},
    "A": {"emoji": "🄰", "label": "A段", "color": "#4caf50"},
    "A'": {"emoji": "🅰️", "label": "A'段", "color": "#8bc34a"},
    "B": {"emoji": "🅱️", "label": "B段", "color": "#ff9800"},
    "C": {"emoji": "🅲", "label": "C段", "color": "#e94560"},
}


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _chord_complexity(chord_str):
    _, quality = parse_chord_name(chord_str)
    if quality is None:
        return 0
    score = 0
    if "7" in quality: score += 1
    if "9" in quality: score += 2
    if "11" in quality: score += 3
    if "13" in quality: score += 4
    if "dim" in quality: score += 2
    if "aug" in quality: score += 2
    if len(quality) > 1 and ("#" in quality[1:] or "b" in quality[1:]): score += 1
    return score


def _estimate_bpm_and_window(chords, is_simple):
    """BPM 自動偵測 + 動態窗口"""
    durations = [c.get("end", c["time"] + 2) - c["time"] for c in chords if c.get("end")]
    durations = [d for d in durations if 0.1 < d < 10]

    if not durations:
        return 120, 8.0

    median_dur = sorted(durations)[len(durations) // 2]
    bpms = [60.0 / median_dur * mul for mul in (1, 2, 4)]
    best_bpm = min(bpms, key=lambda b: abs(b - 100))
    best_bpm = max(60, min(180, round(best_bpm)))

    beat_sec = 60.0 / best_bpm
    if is_simple:
        window_sec = beat_sec * 4 * 2   # 簡單歌：2 小節窗口（更細切割）
    else:
        window_sec = beat_sec * 4 * 4   # 流行歌：4 小節窗口

    return best_bpm, max(3, min(30, window_sec))


def jaccard_similarity(set1, set2):
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)


def _contains_ii_v(degrees):
    """偵測 ii-V 進行（subdominant→dominant 標誌）"""
    for i in range(len(degrees) - 1):
        if degrees[i].startswith("ii") and degrees[i + 1].startswith("V"):
            return True
    return False


def _extract_midi_features(song_hash, data_dir="W:/data"):
    """讀取 Melody 與 Bass MIDI 資訊，轉為絕對時間戳清單"""
    melody_events = []
    bass_events = []
    if not song_hash or not mido:
        return melody_events, bass_events

    base = Path(data_dir)
    m_path = base / "hybrid_melody" / f"{song_hash}.mid"
    b_path = base / "hybrid_bass" / f"{song_hash}.mid"

    def _parse_mid(path):
        events = []
        if path.exists() and path.stat().st_size > 0:
            try:
                mid = mido.MidiFile(path)
                current_time = 0.0
                for msg in mid:
                    current_time += msg.time
                    if msg.type == 'note_on' and msg.velocity > 0:
                        events.append({"time": current_time, "pitch": msg.note})
            except Exception:
                pass
        return events

    melody_events = _parse_mid(m_path)
    bass_events = _parse_mid(b_path)
    return melody_events, bass_events


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def detect_sections(chords, key="C", song_hash=None, data_dir="W:/data", mode="auto", fallback_data_dir=None):
    if not chords or len(chords) < 4:
        return {"sections": [], "analysis": {}}

    # RLHF Hook: 優先載入人類標記的 Ground Truth (只從指定的 user_dir 載入)
    if song_hash:
        try:
            import json
            human_path = Path(data_dir) / "human_sections" / f"{song_hash}.json"
            if human_path.exists():
                human_data = json.loads(human_path.read_text(encoding="utf-8"))
                raw_human = human_data.get("sections", [])
                
                # Merge adjacent identical sections (supports natural undo)
                final_sections = []
                for s in raw_human:
                    if final_sections and final_sections[-1]["type"] == s.get("type"):
                        final_sections[-1]["end"] = max(final_sections[-1]["end"], s.get("end", 0))
                    else:
                        final_sections.append(dict(s))
                
                # 重新回填前端所需的 label 與 color 顯示屬性
                for s in final_sections:
                    t = s.get("type", "verse")
                    if t in SECTION_TYPES:
                        s["label"] = SECTION_TYPES[t]["label"]
                        s["emoji"] = SECTION_TYPES[t]["emoji"]
                        s["color"] = SECTION_TYPES[t]["color"]
                    else:
                        s["label"] = t
                        s["color"] = "#888"
                        
                return {"sections": final_sections, "analysis": {"mode": "human-loop"}}
        except Exception as e:
            print(f"[RLHF] Failed to load human sections: {e}")

    key_semi = NOTE_TO_SEMI.get(key.rstrip("m"), 0)
    total_dur = chords[-1].get("end", chords[-1]["time"] + 4)

    unique_all = {c["chord"] for c in chords if c.get("chord") and c["chord"] != "N"}
    is_simple = len(unique_all) <= 5 and total_dur < 150

    # Models and feature files are in fallback_data_dir (which is the root data directory)
    actual_data_dir = fallback_data_dir if fallback_data_dir else data_dir
    melody_all, bass_all = _extract_midi_features(song_hash, actual_data_dir)

    bpm, WINDOW = _estimate_bpm_and_window(chords, is_simple)

    # 切割窗口
    n_windows = max(1, int(math.ceil(total_dur / WINDOW)))
    windows_data = []

    for i in range(n_windows):
        start = i * WINDOW
        end = min((i + 1) * WINDOW, total_dur)
        w_chords = [c for c in chords if start <= c["time"] < end]
        if not w_chords:
            continue

        raw_degrees = [chord_to_degree(c["chord"], key_semi) for c in w_chords]
        # 去延伸音數字，只保留級數骨幹（IIm, V, I 等）方便比對
        degrees = [re.sub(r'[0-9].*', '', d).replace("maj", "") for d in raw_degrees if d]

        unique = set(degrees)
        comp = sum(_chord_complexity(c["chord"]) for c in w_chords) / max(len(w_chords), 1)

        # 壓縮連續重複 → fingerprint
        condensed = [degrees[0]] if degrees else []
        for d in degrees[1:]:
            if d != condensed[-1]:
                condensed.append(d)

        # 擷取 MIDI 窗口特徵
        w_mel = [m for m in melody_all if start <= m["time"] < end]
        w_bass = [b for b in bass_all if start <= b["time"] < end]
        mel_density = len(w_mel)
        mel_pitch = sum(m["pitch"] for m in w_mel) / mel_density if mel_density > 0 else 0
        bass_density = len(w_bass)

        windows_data.append({
            "start": start, "end": end,
            "degrees": degrees,
            "unique": unique,
            "complexity": comp,
            "density": len(w_chords),
            "fingerprint": tuple(condensed),
            "type": "verse",
            "melody_density": mel_density,
            "melody_avg_pitch": mel_pitch,
            "bass_density": bass_density
        })

    if mode == "auto":
        # 優先嘗試深度學習模型 V5，失敗或未安裝則 fallback 到 Rule-based (V4/V3)
        actual_data_dir = fallback_data_dir if fallback_data_dir else data_dir
        if not _classify_dl(windows_data, actual_data_dir):
            _classify_rule_based(windows_data, chords)
    else:
        _classify_rule_based(windows_data, chords)

    # 合併相鄰同類型 + 高相似度強制合併
    merged = _merge_sections(windows_data)

    # 輸出
    sections = []
    for s in merged:
        base_type = re.sub(r'[0-9\']+$', '', s["type"])  # A1→A, A'→A
        info = SECTION_TYPES.get(base_type, SECTION_TYPES.get(s["type"], SECTION_TYPES["A"]))
        label = info["label"]
        # A1/A2 → A1段/A2段
        if s["type"] != base_type and any(c.isdigit() for c in s["type"]):
            label = s["type"] + "段"
        elif "'" in s["type"]:
            label = info.get("label", s["type"])

        sections.append({
            "type": s["type"],
            "start": round(s["start"], 1),
            "end": round(s["end"], 1),
            "emoji": info["emoji"],
            "label": label,
            "color": info["color"],
        })

    return {
        "sections": sections,
        "analysis": {
            "bpm": bpm,
            "window_sec": round(WINDOW, 1),
            "mode": "simple (A/B)" if is_simple else "pop (verse/chorus)",
            "unique_chords": len(unique_all),
            "total_duration": round(total_dur, 1),
        },
    }


# ---------------------------------------------------------------------------
# 簡單歌曲：A1/A2/A'/B/C
# ---------------------------------------------------------------------------

def _classify_simple(windows_data):
    """簡單歌：fingerprint 比對 + ii-V 偵測 + A 編號"""
    labels = ["A", "B", "C"]
    current_idx = 0
    known = []
    a_counter = 1

    for w in windows_data:
        if not w["fingerprint"]:
            w["type"] = "intro" if not known else "outro"
            continue

        # ii-V 進行 → 直接判 B 段
        if _contains_ii_v(w["degrees"]):
            if not any(k["label"] == "B" for k in known):
                known.append({"label": "B", "unique": w["unique"].copy(), "fingerprint": w["fingerprint"]})
                if current_idx < len(labels) and labels[current_idx] == "B":
                    current_idx += 1
            w["type"] = "B"
            continue

        # 比對已知段落
        best_sim = 0.0
        best_label = None
        best_k = None

        for k in known:
            if w["fingerprint"] == k["fingerprint"]:
                best_label = k["label"]
                best_k = k
                best_sim = 1.0
                break
            sim = jaccard_similarity(w["unique"], k["unique"])
            if sim > best_sim:
                best_sim = sim
                best_label = k["label"]
                best_k = k

        if best_label and best_sim >= 0.85:
            # 完全相同或極相似 → 同段落（不編號，合併時會連起來）
            w["type"] = best_label
        elif best_label and best_sim >= 0.7 and best_k and len(w["unique"] ^ best_k["unique"]) <= 1:
            # 差 1 個和弦 → A' 變形
            base = best_label.rstrip("'0123456789")
            w["type"] = base + "'"
        else:
            # 新段落
            new_l = labels[current_idx] if current_idx < len(labels) else "B"
            if current_idx < len(labels):
                current_idx += 1
            w["type"] = new_l
            known.append({"label": new_l, "unique": w["unique"].copy(), "fingerprint": w["fingerprint"]})


# ---------------------------------------------------------------------------
# 流行歌曲：verse/chorus/bridge
# ---------------------------------------------------------------------------

def _classify_pop(windows_data):
    """流行歌：密度+複雜度+重複模式+MIDI 音高能量"""
    if not windows_data:
        return

    avg_density = sum(w["density"] for w in windows_data) / len(windows_data)
    avg_complexity = sum(w["complexity"] for w in windows_data) / len(windows_data)
    
    has_midi = any(w["melody_density"] > 0 for w in windows_data)
    if has_midi:
        avg_mel_dens = sum(w["melody_density"] for w in windows_data) / len(windows_data)
        max_mel_pitch = max((w["melody_avg_pitch"] for w in windows_data), default=0)
    else:
        avg_mel_dens = 0
        max_mel_pitch = 0

    # 找最常重複的 fingerprint = chorus 候選
    fp_counts = Counter(w["fingerprint"] for w in windows_data if w["fingerprint"])
    chorus_fp = None
    valid = {fp: c for fp, c in fp_counts.items() if c >= 2 and set(fp)}
    if valid:
        chorus_fp = max(valid, key=lambda p: valid[p] * len(p))

    for i, w in enumerate(windows_data):
        is_first_window = (i == 0)
        is_last_window = (i >= len(windows_data) - 1)
        prev_bass = windows_data[i-1]["bass_density"] if i > 0 else 0

        # V4: 強力依賴旋律能量防呆 Intro / Outro
        if is_first_window:
            # 絕對不允許第一小節直接判定為副歌 (避免開頭直接進高潮導致突兀)
            if has_midi and w["melody_density"] > avg_mel_dens * 0.8:
                w["type"] = "verse"
            else:
                w["type"] = "intro"
            continue

        if has_midi and is_last_window and w["melody_density"] < avg_mel_dens * 0.2:
            w["type"] = "outro"
            continue

        # V3 降級/基礎條件
        chord_chorus = (w["density"] > avg_density * 1.3 or w["complexity"] > avg_complexity * 1.5)
        fp_chorus = (chorus_fp and w["fingerprint"] == chorus_fp) or (chorus_fp and jaccard_similarity(w["unique"], set(chorus_fp)) > 0.8)
        
        # V4 評分加成 (Melody 高音、高密度)
        score = 0
        if chord_chorus: score += 1
        if fp_chorus: score += 2
        
        if has_midi:
            if w["melody_avg_pitch"] > max_mel_pitch - 5 and w["melody_density"] > avg_mel_dens * 1.1:
                score += 2
            elif w["melody_density"] < avg_mel_dens * 0.3:
                score -= 2 # 旋律太少極不可能是副歌
            
            # 偵測 Bass 律動突增作為 Pre-Chorus 指標
            if w["bass_density"] > prev_bass * 1.5 and prev_bass > 0 and score < 2:
                w["type"] = "pre_chorus"
                continue

        # 最終判定
        if score >= 2:
            w["type"] = "chorus"
        elif (not has_midi and is_first_window and w["density"] <= 2) or (has_midi and w["melody_density"] < avg_mel_dens * 0.2):
            w["type"] = "intro" if i < len(windows_data)/2 else "outro"
        elif w["density"] > avg_density * 0.8 and w["complexity"] > avg_complexity:
            w["type"] = "pre_chorus"
        elif len(w["unique"]) <= 2 and w["density"] <= avg_density * 0.5:
            w["type"] = "instrumental"
        else:
            w["type"] = "verse"


# ---------------------------------------------------------------------------
# 深度學習 (V5): BiLSTM 時序模型
# ---------------------------------------------------------------------------

def _classify_dl(windows_data, data_dir):
    """使用 PyTorch BiLSTM 模型預測段落標籤。若模型尚未訓練則回傳 False 觸發 Fallback"""
    if not windows_data:
        return False
        
    try:
        import torch
        from pathlib import Path
        try:
            from .section_model import BiLSTMSectionTagger, ID_TO_SECTION, NUM_FEATURES
        except ImportError:
            from section_model import BiLSTMSectionTagger, ID_TO_SECTION, NUM_FEATURES

        model_path = Path(data_dir) / "models" / "section_detector.pth"
        if not model_path.exists():
            return False

        # Extract numerical features for each window
        feature_sequence = []
        for w in windows_data:
            feat = [
                float(w["density"]),
                float(w["complexity"]),
                float(len(w["unique"])),
                float(w["melody_density"]),
                float(w["melody_avg_pitch"]),
                float(w["bass_density"])
            ]
            feature_sequence.append(feat)

        x = torch.tensor(feature_sequence, dtype=torch.float32)
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        model = BiLSTMSectionTagger().to(device)
        model.load_state_dict(torch.load(model_path, map_location=device))
        model.eval()

        preds = model.predict(x.to(device)).tolist()
        
        # Post-Processing: 1-Window Spike Filter (Debouncing)
        # Prevents isolated 8s neural net misclassifications (e.g. Verse -> Chorus -> Verse)
        for _ in range(2): # Run twice to handle adjacent dual spikes if any edge cases exist, though 1 is usually enough
            for i in range(1, len(preds) - 1):
                if preds[i] != preds[i-1] and preds[i-1] == preds[i+1]:
                    preds[i] = preds[i-1]
                    
        for i, w in enumerate(windows_data):
            w["type"] = ID_TO_SECTION.get(preds[i], "verse")
            
        return True
    except Exception as e:
        print("[Section Detect V5] DL Inference Failed:", e)
        return False


# ---------------------------------------------------------------------------
# 合併 + 平滑化
# ---------------------------------------------------------------------------

def _merge_sections(windows_data):
    """合併相鄰同類型 + Jaccard > 0.95 強制合併"""
    if not windows_data:
        return []

    merged = [dict(windows_data[0])]
    for w in windows_data[1:]:
        last = merged[-1]
        sim = jaccard_similarity(w.get("unique", set()), last.get("unique", set()))

        # 同類型 或 極高相似度 → 合併
        if w["type"] == last["type"] or sim > 0.95:
            last["end"] = w["end"]
            last["density"] = last.get("density", 0) + w.get("density", 0)
            last["complexity"] = round((last.get("complexity", 0) + w.get("complexity", 0)) / 2, 1)
            u1 = last.get("unique", set())
            u2 = w.get("unique", set())
            if isinstance(u1, set) and isinstance(u2, set):
                last["unique"] = u1 | u2
        else:
            merged.append(dict(w))

    return merged


# ---------------------------------------------------------------------------
# CLI 測試
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, json, hashlib
    sys.stdout.reconfigure(encoding="utf-8")
    from pathlib import Path

    chords_dir = Path("W:/data/chords") if Path("W:/data/chords").is_dir() else Path("../data/chords")
    test_songs = ["小蜜蜂.flac", "Get It On.flac", "Dancing Queen.flac",
                  "For Once In My Life.flac", "Super Mario Bros. Theme Song.flac"]

    for song in test_songs:
        h = hashlib.md5(song.encode()).hexdigest()[:12]
        f = chords_dir / f"{h}.json"
        if not f.is_file():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        key = data.get("key", "C")
        result = detect_sections(data["chords"], key, song_hash=h, data_dir=str(Path("W:/data")))
        name = song.replace(".flac", "")
        a = result["analysis"]

        print(f"\n=== {name} (Key:{key}, BPM:{a['bpm']}, {a['mode']}, {a['unique_chords']}種和弦) ===")
        for s in result["sections"]:
            dur = s["end"] - s["start"]
            print(f"  {s['emoji']} {s['start']:5.0f}-{s['end']:5.0f}s ({dur:4.0f}s) {s['label']}")
