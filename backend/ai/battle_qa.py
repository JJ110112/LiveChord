"""
QA Battle 統合模組 — Phase 11 P1-3

將所有 Generator + Evaluator 串接成一個統一的 QA Battle:
  - Musician QA (可演奏性)
  - Producer QA (混音衝突)
  - Melody Evaluator (旋律品質)
  - Accompaniment Evaluator (伴奏品質)
  - Fingering Evaluator (指法合理性)
  - Pedal Evaluator (踏板品質)
  - Dynamics Evaluator (力度表情)

最終產出: 綜合 verdict (pass/warn/fail) + 逐項分數 + 改進建議
"""

from typing import List, Dict, Any, Optional


# 分數門檻
PASS_THRESHOLD = 65
WARN_THRESHOLD = 45


def run_full_qa(
    chords: List[Dict],
    melody: List[Dict],
    accompaniment: Dict,
    bpm: int = 120,
    level: str = "L2",
    original_chords: Optional[List[Dict]] = None,
    jazzified_chords: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    執行完整 QA Battle。

    Args:
        chords: [{time, end, chord}, ...]
        melody: [{start, end, midi, confidence?}, ...]
        accompaniment: {left_hand: [...], right_hand: [...], pedal: [...]}
        bpm: 曲速
        level: L1/L2/L3
        original_chords: (可選) Jazzify 前的和弦
        jazzified_chords: (可選) Jazzify 後的和弦

    Returns:
        {
            "verdict": "pass" | "warn" | "fail",
            "overall_score": 0-100,
            "scores": {
                "melody": {...},
                "accompaniment": {...},
                "fingering": {...},
                "pedal": {...},
                "dynamics": {...},
                "playability": {...},
                "mix": {...},
            },
            "suggestions": [str, ...],
            "summary": str,
        }
    """
    scores = {}
    suggestions = []
    all_scores = []

    left_hand = accompaniment.get("left_hand", [])
    right_hand = accompaniment.get("right_hand", [])
    pedal_events = accompaniment.get("pedal", [])

    # --- 1. Melody Evaluation ---
    try:
        from .melody_evaluator import evaluate_melody
        mel = evaluate_melody(melody)
        scores["melody"] = {
            "score": mel["overall_score"],
            "details": {k: mel[k]["score"] for k in ["tessitura", "intervals", "phrase_arc", "rhythm"]},
        }
        all_scores.append(mel["overall_score"])

        if mel["intervals"]["score"] < 50:
            suggestions.append("旋律跳進比例偏高，考慮增加級進旋律以提升流暢度")
        if mel["phrase_arc"]["score"] < 50:
            suggestions.append("樂句弧線不明顯，建議在段落結構處加入呼吸點")
    except Exception as e:
        scores["melody"] = {"score": 50, "error": str(e)}
        all_scores.append(50)

    # --- 2. Accompaniment Evaluation ---
    try:
        from .accompaniment_evaluator import evaluate_accompaniment
        acc = evaluate_accompaniment(left_hand, right_hand, melody, bpm=bpm, chords=chords)
        scores["accompaniment"] = {
            "score": acc["overall_score"],
            "details": {k: acc[k]["score"] for k in ["collision", "voice_leading", "register", "density", "harmony"]},
        }
        all_scores.append(acc["overall_score"])

        if acc["collision"]["score"] < 50:
            suggestions.append(f"旋律碰撞率偏高 ({acc['collision']['collision_ratio']:.0%})，建議調整伴奏音域避開旋律")
        if acc["voice_leading"]["score"] < 50:
            suggestions.append("聲部連接不夠平滑，考慮使用更多共同音保持")
        if acc["density"]["score"] < 50:
            suggestions.append("伴奏密度可能過高，考慮在快速段降低音符數量")
    except Exception as e:
        scores["accompaniment"] = {"score": 50, "error": str(e)}
        all_scores.append(50)

    # --- 3. Fingering Evaluation ---
    try:
        from .fingering_evaluator import evaluate_events
        lh_eval = evaluate_events(left_hand, hand="left", bpm=bpm)
        rh_eval = evaluate_events(right_hand, hand="right", bpm=bpm)
        avg_fing = (lh_eval["score"] + rh_eval["score"]) / 2
        scores["fingering"] = {
            "score": round(avg_fing),
            "left_hand": lh_eval["score"],
            "right_hand": rh_eval["score"],
            "left_valid": lh_eval["valid"],
            "right_valid": rh_eval["valid"],
        }
        all_scores.append(avg_fing)

        if not lh_eval["valid"]:
            suggestions.append("左手指法有不合理動作，建議啟用安全降級或降低難度")
        if not rh_eval["valid"]:
            suggestions.append("右手指法有不合理動作，建議降低難度等級")
    except Exception as e:
        scores["fingering"] = {"score": 50, "error": str(e)}
        all_scores.append(50)

    # --- 4. Pedal Evaluation ---
    try:
        from .pedal_advisor import evaluate_pedal
        if pedal_events:
            ped = evaluate_pedal(pedal_events, chords)
            scores["pedal"] = {"score": round(ped["total_score"])}
            all_scores.append(ped["total_score"])
        else:
            scores["pedal"] = {"score": 70, "note": "no pedal data"}
            all_scores.append(70)
    except Exception as e:
        scores["pedal"] = {"score": 50, "error": str(e)}
        all_scores.append(50)

    # --- 5. Dynamics Evaluation ---
    try:
        from .dynamics_engine import evaluate_dynamics
        all_events = left_hand + right_hand
        if any(e.get("velocity") for e in all_events):
            dyn = evaluate_dynamics(all_events)
            scores["dynamics"] = {"score": round(dyn["total_score"])}
            all_scores.append(dyn["total_score"])
        else:
            scores["dynamics"] = {"score": 70, "note": "no dynamics data"}
            all_scores.append(70)
    except Exception as e:
        scores["dynamics"] = {"score": 50, "error": str(e)}
        all_scores.append(50)

    # --- 6. Musician QA (Playability) ---
    try:
        from .musician_qa import run_musician_qa
        level_num = {"L1": 1, "L2": 2, "L3": 3}.get(level, 2)
        play = run_musician_qa(chords, level=level_num)
        scores["playability"] = {
            "score": round(play["playability_score"]),
            "warnings": play["warnings"][:3],
        }
        all_scores.append(play["playability_score"])
    except Exception as e:
        scores["playability"] = {"score": 50, "error": str(e)}
        all_scores.append(50)

    # --- 7. Producer QA (Mix) ---
    if original_chords and jazzified_chords:
        try:
            from .producer_qa import run_producer_qa
            mix = run_producer_qa(original_chords, jazzified_chords)
            scores["mix"] = {
                "score": round(mix["mix_score"]),
                "warnings": mix["warnings"][:3],
            }
            all_scores.append(mix["mix_score"])
        except Exception as e:
            scores["mix"] = {"score": 50, "error": str(e)}
            all_scores.append(50)
    else:
        scores["mix"] = {"score": 100, "note": "no jazzify comparison"}

    # --- 綜合評分 ---
    overall = sum(all_scores) / len(all_scores) if all_scores else 50

    if overall >= PASS_THRESHOLD:
        verdict = "pass"
    elif overall >= WARN_THRESHOLD:
        verdict = "warn"
    else:
        verdict = "fail"

    # 生成摘要
    low_items = [(k, v["score"]) for k, v in scores.items()
                 if isinstance(v.get("score"), (int, float)) and v["score"] < WARN_THRESHOLD]
    if low_items:
        low_names = ", ".join(f"{k}({s})" for k, s in low_items)
        summary = f"QA {verdict.upper()}: 總分 {overall:.0f}/100。需要關注: {low_names}"
    else:
        summary = f"QA {verdict.upper()}: 總分 {overall:.0f}/100。所有維度通過基本門檻。"

    if not suggestions:
        suggestions.append("整體品質良好，可以正式使用")

    return {
        "verdict": verdict,
        "overall_score": round(overall),
        "scores": scores,
        "suggestions": suggestions[:5],
        "summary": summary,
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import json

    # Quick test
    chords = [
        {"time": 0, "end": 2, "chord": "Cmaj7"},
        {"time": 2, "end": 4, "chord": "Am7"},
        {"time": 4, "end": 6, "chord": "Dm7"},
        {"time": 6, "end": 8, "chord": "G7"},
    ]
    melody = [
        {"start": 0.5, "end": 1.5, "midi": 72, "note": "C5", "confidence": 0.9},
        {"start": 2.5, "end": 3.5, "midi": 69, "note": "A4", "confidence": 0.85},
        {"start": 4.5, "end": 5.5, "midi": 65, "note": "F4", "confidence": 0.88},
        {"start": 6.5, "end": 7.5, "midi": 62, "note": "D4", "confidence": 0.87},
    ]

    from .accompaniment_generator import generate_accompaniment
    acc = generate_accompaniment(chords, melody, bpm=120, style="Arpeggio", level="L2")

    result = run_full_qa(chords, melody, acc, bpm=120, level="L2")
    print(json.dumps(result, indent=2, ensure_ascii=False))
