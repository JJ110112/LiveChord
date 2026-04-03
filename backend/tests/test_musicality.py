import sys
import os
import mido
from typing import List, Dict

# 強制 Windows終端機輸出 utf-8，避免 UnicodeEncodeError
sys.stdout.reconfigure(encoding='utf-8')

# 加入 backend 路徑以便引用
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from backend.ai.accompaniment_generator import generate_accompaniment


def evaluate_span(events: List[Dict], hand: str) -> int:
    """計算實體物理跨距（每 0.1s 取樣一次發聲中的音符）。"""
    if not events:
        return 0
    penalty = 0
    max_time = max(e["time"] + e["duration"] for e in events)
    
    for t_step in range(int(max_time * 10)):
        time_sec = t_step / 10.0
        active_pitches = []
        for e in events:
            if e["time"] <= time_sec <= (e["time"] + e["duration"]):
                active_pitches.append(e["pitch"])
        
        if active_pitches:
            span = max(active_pitches) - min(active_pitches)
            # 十度包含 = 16，單手超過 15 個半音 (Minor 10th) 非常痛苦
            if span > 15:
                penalty += 5
    return penalty


def evaluate_voice_crossing(lh_events: List[Dict], rh_events: List[Dict]) -> int:
    """左手超越右手音高的扣分。"""
    penalty = 0
    max_time = max([e["time"] + e["duration"] for e in lh_events] + [e["time"] + e["duration"] for e in rh_events] + [0])
    
    for t_step in range(int(max_time * 10)):
        time_sec = t_step / 10.0
        lh_active = [e["pitch"] for e in lh_events if e["time"] <= time_sec <= (e["time"] + e["duration"])]
        rh_active = [e["pitch"] for e in rh_events if e["time"] <= time_sec <= (e["time"] + e["duration"])]
        
        if lh_active and rh_active:
            # 如果左手的最高音，居然大於或等於右手的最低音
            if max(lh_active) >= min(rh_active):
                penalty += 1  # 輕微扣分，每次 0.1 秒 +1
    return penalty


def export_to_midi(lh_events: List[Dict], rh_events: List[Dict], bpm: int, out_path: str):
    """將伴奏事件轉換為標準 MIDI 檔案"""
    mid = mido.MidiFile()
    track_meta = mido.MidiTrack()
    mid.tracks.append(track_meta)
    
    # Meta track
    track_meta.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm)))
    
    ticks_per_sec = (bpm / 60.0) * mid.ticks_per_beat
    
    def process_track(track_events, name):
        track = mido.MidiTrack()
        track.name = name
        mid.tracks.append(track)
        
        # 轉換為開/關事件組合
        midi_events = []
        for e in track_events:
            abs_start = e["time"]
            abs_end = e["time"] + e["duration"]
            pitch = e["pitch"]
            vel = e["velocity"]
            midi_events.append({'tick': int(abs_start * ticks_per_sec), 'type': 'note_on', 'pitch': pitch, 'vel': vel})
            # off velocity 設為 0
            midi_events.append({'tick': int(abs_end * ticks_per_sec), 'type': 'note_off', 'pitch': pitch, 'vel': 0})
            
        # 依絕對時間排序
        midi_events.sort(key=lambda x: x['tick'])
        
        # 轉為 delta time
        last_tick = 0
        for evt in midi_events:
            delta = evt['tick'] - last_tick
            if delta < 0:
                delta = 0
            if evt['type'] == 'note_on':
                track.append(mido.Message('note_on', note=evt['pitch'], velocity=evt['vel'], time=delta))
            else:
                track.append(mido.Message('note_off', note=evt['pitch'], velocity=0, time=delta))
            last_tick = last_tick + delta
            
    if lh_events:
        process_track(lh_events, "Left Hand")
    if rh_events:
        process_track(rh_events, "Right Hand")
        
    mid.save(out_path)


def run_evaluation():
    print("=" * 60)
    print(" [Music] AI 伴奏樂理與物理限制裁判 (Musicality Evaluator)")
    print("=" * 60 + "\n")
    
    # Kiki's Delivery Service (魔女宅急便) 擷取: A -> D/A -> A -> F#m7 -> E7
    chords = [
        {"time": 0.0, "end": 2.0, "chord": "A"},
        {"time": 2.0, "end": 4.0, "chord": "D/A"},
        {"time": 4.0, "end": 6.0, "chord": "A"},
        {"time": 6.0, "end": 8.0, "chord": "F#m7"},
        {"time": 8.0, "end": 10.0, "chord": "E7"},
    ]
    
    bpm = 110
    style = "Arpeggio"
    level = "L3"
    
    print(f"測試曲目：魔女宅急便選段 (BPM: {bpm})")
    print(f"和弦進行：{[c['chord'] for c in chords]}")
    print(f"生成配置：風格={style}, 等級={level}\n")
    
    print(">> 正在呼叫神經伴奏引擎...")
    result = generate_accompaniment(
        chords=chords,
        melody=[],
        bpm=bpm,
        style=style,
        level=level
    )
    
    lh_events = result.get("left_hand", [])
    rh_events = result.get("right_hand", [])
    
    print(f"   [OK] 取得事件: 左手={len(lh_events)} 音, 右手={len(rh_events)} 音\n")
    
    # 開始計分
    score = 100
    print(">> 進入品管審查：")
    
    # 1. Span penalty
    lh_span_pen = evaluate_span(lh_events, "左手")
    rh_span_pen = evaluate_span(rh_events, "右手")
    if lh_span_pen > 0:
        print(f"   [警告] 左手偵測到反人體工學的逾矩跨距！扣分: {lh_span_pen}")
        score -= lh_span_pen
    else:
        print("   [通過] 左手跨距符合人類構造 (<= 10度)。")
        
    if rh_span_pen > 0:
        print(f"   [警告] 右手跨距偵測異常！扣分: {rh_span_pen}")
        score -= rh_span_pen
    else:
        print("   [通過] 右手跨距符合合理範圍。")
        
    # 2. Voice crossing penalty
    cross_pen = evaluate_voice_crossing(lh_events, rh_events)
    if cross_pen > 0:
        print(f"   [警告] 左右手出現奇怪的聲部交叉 (打結！)，扣分: {cross_pen}")
        score -= cross_pen
    else:
        print("   [通過] 左右聲部各自安好，無越界打架。")
        
    # 3. 輸出總結
    print(f"\n=> 最終樂理合規分數：{score} / 100")
    if score >= 80:
        print("=> 結論：[PASS] 伴奏符合樂理與物理邏輯，准予通過！")
    else:
        print("=> 結論：[FAIL] 評分過低，演算法產生了反人類的演奏型態！")
        
    # 輸出 MIDI
    out_dir = os.path.join(os.path.dirname(__file__), '../../data/test_songs')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'eval_output.mid')
    print(f"\n>> 正在輸出實體 MIDI 以便人耳驗證...")
    export_to_midi(lh_events, rh_events, bpm, out_file)
    print(f"   [OK] 檔案已儲存於: {os.path.abspath(out_file)}")

if __name__ == "__main__":
    run_evaluation()
