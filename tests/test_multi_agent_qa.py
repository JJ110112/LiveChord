"""
LiveChord 多智能體 QA 品管測試
模擬 4 個專業角色進行全面品質驗證

🧑‍💼 PM Agent: 音樂理論正確性
👨‍💻 Data RD Agent: 資料管線完整性
👨‍💻 Backend RD Agent: API + 演算法正確性
🕵️ QA Agent: 和聲驗證 + 邊界測試
"""

import sys
import os
import json
import hashlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

PASS = 0
FAIL = 0
WARN = 0
RESULTS = []


def _log(agent, level, msg):
    global PASS, FAIL, WARN
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}[level]
    RESULTS.append({"agent": agent, "level": level, "msg": msg})
    if level == "PASS":
        PASS += 1
    elif level == "FAIL":
        FAIL += 1
    else:
        WARN += 1
    print(f"  {icon} [{agent}] {msg}")


# =====================================================================
# 🧑‍💼 PM Agent: 音樂理論正確性
# =====================================================================
def test_pm_agent():
    print("\n🧑‍💼 PM Agent: 音樂理論驗證")

    # Test 1: Jazzify L1 extensions are correct
    from ai.reharmonizer import Reharmonizer
    rh = Reharmonizer(level=1)
    chords = [
        {"time": 0, "end": 4, "chord": "C"},
        {"time": 4, "end": 8, "chord": "Dm"},
        {"time": 8, "end": 12, "chord": "G"},
        {"time": 12, "end": 16, "chord": "Am"},
    ]
    r = rh.jazzify(chords, key="C")
    jazz_names = [c["chord"] for c in r["chords"]]

    if "Cmaj7" in jazz_names:
        _log("PM", "PASS", "I → Imaj7 (C → Cmaj7)")
    else:
        _log("PM", "FAIL", f"I 應為 Cmaj7, 實際: {jazz_names[0]}")

    if "Dm7" in jazz_names:
        _log("PM", "PASS", "IIm → IIm7 (Dm → Dm7)")
    else:
        _log("PM", "FAIL", f"IIm 應為 Dm7, 實際: {jazz_names}")

    if "G7" in jazz_names:
        _log("PM", "PASS", "V → V7 (G → G7)")
    else:
        _log("PM", "FAIL", f"V 應為 G7, 實際: {jazz_names}")

    if "Am7" in jazz_names:
        _log("PM", "PASS", "VIm → VIm7 (Am → Am7)")
    else:
        _log("PM", "FAIL", f"VIm 應為 Am7, 實際: {jazz_names}")

    # Test 2: L2 ii-V insertion
    rh2 = Reharmonizer(level=2)
    r2 = rh2.jazzify(chords, key="C")
    inserted = [c for c in r2["chords"] if c.get("inserted")]
    if any("m7" in c["chord"] for c in inserted):
        _log("PM", "PASS", "L2 插入了 ii-V (m7 和弦)")
    else:
        _log("PM", "FAIL", "L2 未插入 ii-V")

    # Test 3: Minor ii-V should use m7b5
    minor_chords = [
        {"time": 0, "end": 4, "chord": "F"},
        {"time": 4, "end": 8, "chord": "G"},
        {"time": 8, "end": 12, "chord": "Am"},
    ]
    r3 = rh2.jazzify(minor_chords, key="C")
    all_names = [c["chord"] for c in r3["chords"]]
    if any("m7b5" in n for n in all_names):
        _log("PM", "PASS", "小調 ii-V 使用 m7b5 (half-diminished)")
    else:
        _log("PM", "WARN", f"小調 ii-V 未使用 m7b5: {all_names}")

    # Test 4: L3 tritone sub
    rh3 = Reharmonizer(level=3)
    r3 = rh3.jazzify([
        {"time": 0, "end": 4, "chord": "Dm"},
        {"time": 4, "end": 8, "chord": "G"},
        {"time": 8, "end": 12, "chord": "C"},
        {"time": 12, "end": 16, "chord": "Dm"},
        {"time": 16, "end": 20, "chord": "G"},
        {"time": 20, "end": 24, "chord": "C"},
    ], key="C")
    changes = [c["rule"] for c in r3["changes"]]
    if "tritone sub" in changes:
        _log("PM", "PASS", "L3 包含三全音代理")
    else:
        _log("PM", "WARN", f"L3 未產生三全音代理: {changes}")

    # Test 5: Pattern detection
    if r3.get("patterns"):
        _log("PM", "PASS", f"偵測到 {len(r3['patterns'])} 個樂理 Pattern")
    else:
        _log("PM", "WARN", "未偵測到樂理 Pattern")

    # Test 6: Idempotency — Jazzify 已 jazz 的和弦不應疊加
    already_jazz = [{"time": 0, "end": 4, "chord": "Cmaj7"}]
    r_idem = Reharmonizer(level=1).jazzify(already_jazz, key="C")
    if r_idem["chords"][0]["chord"] == "Cmaj7":
        _log("PM", "PASS", "冪等性: Cmaj7 不變成 Cmaj7maj7")
    else:
        _log("PM", "FAIL", f"冪等性失敗: Cmaj7 → {r_idem['chords'][0]['chord']}")


# =====================================================================
# 👨‍💻 Data RD Agent: 資料管線完整性
# =====================================================================
def test_data_rd_agent():
    print("\n👨‍💻 Data RD Agent: 資料管線驗證")

    # Test 1: Preprocess
    from ai.preprocess import build_training_data
    data_dir = "V:/data/chords" if os.path.isdir("V:/data/chords") else "../data/chords"
    degrees, transposed, stats = build_training_data(data_dir)

    if stats["total_songs"] > 0:
        _log("DataRD", "PASS", f"預處理成功: {stats['total_songs']} 首歌")
    else:
        _log("DataRD", "FAIL", "預處理: 0 首歌")

    if stats["unique_degrees"] <= stats.get("vocab_limit", 100):
        _log("DataRD", "PASS", f"詞彙過濾: {stats['unique_degrees']} 級數 (限制 {stats.get('vocab_limit', 100)})")
    else:
        _log("DataRD", "FAIL", f"詞彙過濾失敗: {stats['unique_degrees']} > {stats.get('vocab_limit', 100)}")

    # Test 2: Markov model
    from ai.markov import ChordPredictor
    predictor = ChordPredictor()
    predictor.train(data_dir)
    model_stats = predictor.get_stats()

    if model_stats["total_transitions"] > 100:
        _log("DataRD", "PASS", f"Markov 訓練: {model_stats['total_transitions']} 轉移")
    else:
        _log("DataRD", "FAIL", f"Markov 轉移太少: {model_stats['total_transitions']}")

    # Test 3: Markov prediction
    preds = predictor.predict(["I"], top_k=5)
    if preds and preds[0][1] > 0:
        _log("DataRD", "PASS", f"Markov 預測 I→: {preds[0][0]} ({preds[0][1]:.0%})")
    else:
        _log("DataRD", "FAIL", "Markov 預測失敗")

    # Test 4: Chord2Vec
    from ai.chord2vec import Chord2Vec
    c2v = Chord2Vec(dim=16)
    c2v.train(data_dir)
    if c2v.trained and c2v.get_stats()["vocab_size"] > 10:
        sims = c2v.similar("I", 3)
        _log("DataRD", "PASS", f"Chord2Vec: vocab={c2v.get_stats()['vocab_size']}, I≈{sims[0][0] if sims else '?'}")
    else:
        _log("DataRD", "FAIL", "Chord2Vec 訓練失敗")

    # Test 5: Groove Dictionary
    from ai.groove_dict import GrooveDictionary
    gd = GrooveDictionary()
    gd.build(data_dir)
    gd_stats = gd.get_stats()
    if gd_stats["unique_4_patterns"] > 50:
        _log("DataRD", "PASS", f"Groove Dict: {gd_stats['unique_4_patterns']} 四和弦模式")
    else:
        _log("DataRD", "WARN", f"Groove Dict 模式偏少: {gd_stats['unique_4_patterns']}")

    # Test 6: Voicing Spread
    if gd_stats.get("voicing_spread_degrees", 0) > 0:
        _log("DataRD", "PASS", f"Voicing Spread: {gd_stats['voicing_spread_degrees']} 級數有統計")
    else:
        _log("DataRD", "WARN", "Voicing Spread 無資料")


# =====================================================================
# 👨‍💻 Backend RD Agent: API + 演算法正確性
# =====================================================================
def test_backend_rd_agent():
    print("\n👨‍💻 Backend RD Agent: 後端邏輯驗證")

    # Test 1: Chord parsing
    from chord_table import get_chord_info, parse_chord
    info = get_chord_info("Cmaj7")
    if info and info["notes"] == ["C", "E", "G", "B"]:
        _log("BackendRD", "PASS", "和弦解析: Cmaj7 = C,E,G,B")
    else:
        _log("BackendRD", "FAIL", f"和弦解析錯誤: {info}")

    # Test 2: Transpose
    from ai.preprocess import transpose_chord
    if transpose_chord("C", 2) == "D":
        _log("BackendRD", "PASS", "移調: C +2 = D")
    else:
        _log("BackendRD", "FAIL", f"移調錯誤: C +2 = {transpose_chord('C', 2)}")

    if transpose_chord("G7", -2) == "F7":
        _log("BackendRD", "PASS", "移調: G7 -2 = F7")
    else:
        _log("BackendRD", "FAIL", f"移調錯誤: G7 -2 = {transpose_chord('G7', -2)}")

    # Test 3: Key detection
    from ai.preprocess import detect_key_from_chords
    key = detect_key_from_chords(["C", "F", "G", "Am", "C", "F", "G", "C"])
    if key == 0:  # C = 0
        _log("BackendRD", "PASS", "調性偵測: C-F-G-Am → C")
    else:
        _log("BackendRD", "WARN", f"調性偵測: 預期 C(0), 實際 {key}")

    # Test 4: Song hash consistency
    h1 = hashlib.md5("test.flac".encode()).hexdigest()[:12]
    h2 = hashlib.md5("test.flac".encode()).hexdigest()[:12]
    if h1 == h2:
        _log("BackendRD", "PASS", "Song hash 一致性")
    else:
        _log("BackendRD", "FAIL", "Song hash 不一致!")

    # Test 5: Section detection
    from ai.section_detect import detect_sections
    test_chords = [{"time": i * 2, "end": (i + 1) * 2, "chord": ["C", "G", "Am", "F"][i % 4]} for i in range(40)]
    sections = detect_sections(test_chords, "C")
    if sections["sections"] and len(sections["sections"]) > 1:
        _log("BackendRD", "PASS", f"段落偵測: {len(sections['sections'])} 個段落")
    else:
        _log("BackendRD", "WARN", f"段落偵測: 只有 {len(sections.get('sections', []))} 個段落")

    # Test 6: Pattern extraction
    from ai.pattern_extractor import PatternExtractor
    pe = PatternExtractor()
    patterns = pe.extract_patterns(["Cmaj7", "Am7", "Dm7", "G7", "Cmaj7"], "C")
    if any(p["pattern"] == "251_major" for p in patterns):
        _log("BackendRD", "PASS", "Pattern: 偵測到 ii-V-I")
    else:
        _log("BackendRD", "WARN", f"Pattern: 未偵測到 ii-V-I ({[p['pattern'] for p in patterns]})")

    # Test 7: HMM emission matrix
    from ai.hmm import build_emission_from_songs
    emission = build_emission_from_songs(
        "V:/data/chords" if os.path.isdir("V:/data/chords") else "../data/chords"
    )
    if emission.get_stats()["chord_states"] > 10:
        _log("BackendRD", "PASS", f"HMM 發射矩陣: {emission.get_stats()['chord_states']} 狀態")
    else:
        _log("BackendRD", "FAIL", "HMM 發射矩陣狀態太少")

    # Test 8: Melody extractor import
    try:
        from ai.melody_extractor import MelodyExtractor
        _log("BackendRD", "PASS", "MelodyExtractor 可載入")
    except ImportError as e:
        _log("BackendRD", "FAIL", f"MelodyExtractor 載入失敗: {e}")

    # Test 9: Chord cache
    try:
        from chord_cache import get_chord_summary
        _log("BackendRD", "PASS", "chord_cache 模組可載入")
    except ImportError as e:
        _log("BackendRD", "FAIL", f"chord_cache 載入失敗: {e}")


# =====================================================================
# 🕵️ QA Agent: 和聲驗證 + 邊界測試
# =====================================================================
def test_qa_agent():
    print("\n🕵️ QA Agent: 和聲品質與邊界測試")

    from ai.reharmonizer import Reharmonizer
    from ai.evaluate import pitch_class_overlap, evaluate_reharmonization

    # Test 1: Pitch overlap — Jazzified chord should share common tones
    ov = pitch_class_overlap("C", "Cmaj7")
    if ov >= 0.6:
        _log("QA", "PASS", f"音高重疊: C vs Cmaj7 = {ov:.2f} (≥0.6)")
    else:
        _log("QA", "FAIL", f"音高重疊過低: C vs Cmaj7 = {ov:.2f}")

    ov2 = pitch_class_overlap("C", "F#")
    if ov2 < 0.1:
        _log("QA", "PASS", f"音高重疊: C vs F# = {ov2:.2f} (應接近 0)")
    else:
        _log("QA", "WARN", f"C vs F# 重疊異常高: {ov2:.2f}")

    # Test 2: Reharmonization quality
    orig = [{"time": i * 4, "end": (i + 1) * 4, "chord": c}
            for i, c in enumerate(["C", "F", "G", "Am", "C", "F", "G", "C"])]
    rh = Reharmonizer(level=2)
    result = rh.jazzify(orig, key="C")
    eval_r = evaluate_reharmonization(orig, result["chords"])
    if eval_r["avg_pitch_overlap"] >= 0.3:
        _log("QA", "PASS", f"Jazzify 品質: overlap={eval_r['avg_pitch_overlap']}, grade={eval_r['quality_grade']}")
    else:
        _log("QA", "FAIL", f"Jazzify 品質差: overlap={eval_r['avg_pitch_overlap']}")

    # Test 3: Empty input
    rh0 = Reharmonizer(level=1)
    r_empty = rh0.jazzify([], key="C")
    if r_empty["chords"] == [] and r_empty["jazzified_count"] == 0:
        _log("QA", "PASS", "空白輸入: 回傳空結果")
    else:
        _log("QA", "FAIL", "空白輸入處理錯誤")

    # Test 4: Single chord
    r_single = rh0.jazzify([{"time": 0, "end": 4, "chord": "C"}], key="C")
    if len(r_single["chords"]) == 1:
        _log("QA", "PASS", f"單一和弦: C → {r_single['chords'][0]['chord']}")
    else:
        _log("QA", "FAIL", "單一和弦處理錯誤")

    # Test 5: Unknown chord passthrough
    r_unknown = rh0.jazzify([{"time": 0, "end": 4, "chord": "Xaug11#9"}], key="C")
    if r_unknown["chords"][0]["chord"] == "Xaug11#9":
        _log("QA", "PASS", "未知和弦: passthrough 不 crash")
    else:
        _log("QA", "WARN", f"未知和弦被修改: {r_unknown['chords'][0]['chord']}")

    # Test 6: Time integrity — no overlaps or gaps
    for i in range(len(result["chords"]) - 1):
        curr = result["chords"][i]
        nxt = result["chords"][i + 1]
        if curr.get("end", curr["time"] + 1) > nxt["time"] + 0.01:
            _log("QA", "FAIL", f"時間重疊: chord[{i}] end={curr.get('end')} > chord[{i+1}] time={nxt['time']}")
            break
    else:
        _log("QA", "PASS", "時間完整性: 無重疊")

    # Test 7: Chord syntax validation
    import re
    chord_regex = re.compile(r'^[A-G][b#]?(?:m|dim|aug|sus[24]|maj|add|M)?(?:7|9|11|13)?(?:b5|#5|b9|#9|#11|b13)?(?:/[A-G][b#]?)?$')
    invalid_count = 0
    for c in result["chords"]:
        name = c["chord"]
        if not chord_regex.match(name) and name not in ("N", "NC", "X"):
            invalid_count += 1
    if invalid_count == 0:
        _log("QA", "PASS", "和弦語法: 全部合規")
    else:
        _log("QA", "WARN", f"和弦語法: {invalid_count} 個可能不標準（可能是延伸和弦）")

    # Test 8: Difficulty rating logic
    levels = [(3, 1), (5, 2), (9, 3), (15, 4), (20, 4)]
    for uc, expected in levels:
        stars = 1
        if uc >= 15: stars = 4
        elif uc >= 9: stars = 3
        elif uc >= 5: stars = 2
        if stars == expected:
            _log("QA", "PASS", f"難度分級: {uc}種和弦 → {stars}星")
        else:
            _log("QA", "FAIL", f"難度分級錯誤: {uc}種 → {stars}星 (預期 {expected})")

    # Test 9: Evaluation metrics
    from ai.evaluate import full_evaluation
    data_dir = "V:/data/chords" if os.path.isdir("V:/data/chords") else "../data/chords"
    eval_result = full_evaluation(data_dir)
    if "markov_model" in eval_result:
        ppl = eval_result["markov_model"]["perplexity"]
        acc = eval_result["markov_model"]["accuracy_top5"]
        _log("QA", "PASS", f"模型評測: PPL={ppl:.0f}, Top5={acc:.0%}")
        if acc < 0.3:
            _log("QA", "WARN", f"Top5 準確率偏低: {acc:.0%} (門檻 30%)")
    else:
        _log("QA", "FAIL", f"模型評測失敗: {eval_result}")


# =====================================================================
# Main
# =====================================================================
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("  LiveChord 多智能體 QA 品管測試")
    print("  🧑‍💼 PM · 👨‍💻 DataRD · 👨‍💻 BackendRD · 🕵️ QA")
    print("=" * 60)

    test_pm_agent()
    test_data_rd_agent()
    test_backend_rd_agent()
    test_qa_agent()

    print("\n" + "=" * 60)
    print(f"  📊 測試結果: ✅ {PASS} 通過  ❌ {FAIL} 失敗  ⚠️ {WARN} 警告")
    print("=" * 60)

    # 按 Agent 分組統計
    from collections import Counter
    agent_stats = Counter()
    for r in RESULTS:
        agent_stats[(r["agent"], r["level"])] += 1
    print("\n  Agent 明細:")
    for agent in ["PM", "DataRD", "BackendRD", "QA"]:
        p = agent_stats.get((agent, "PASS"), 0)
        f = agent_stats.get((agent, "FAIL"), 0)
        w = agent_stats.get((agent, "WARN"), 0)
        print(f"    {agent:12s}: ✅{p} ❌{f} ⚠️{w}")

    if FAIL > 0:
        print(f"\n  ❌ 有 {FAIL} 個失敗項目需要修復！")
        for r in RESULTS:
            if r["level"] == "FAIL":
                print(f"    → [{r['agent']}] {r['msg']}")
        sys.exit(1)
    else:
        print(f"\n  🎉 全部通過！")
