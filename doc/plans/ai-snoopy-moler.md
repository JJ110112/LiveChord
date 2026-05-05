# AI 伴奏質感升級 — Phase 1–3 實作計畫

## Context

LiveChord 的 AI 伴奏引擎（`backend/ai/accompaniment_generator.py` + `dynamics_engine.py`）目前生成的 MIDI 聽起來死板：同一個和弦反覆出現時永遠一模一樣、velocity 幾乎是平線、所有 onset 都對齊在 0.0 / 0.25 / 0.5 / 0.75 的量化格子上。

剛完成的知識文件 [doc/for-notebooklm/2026-04-18-accompaniment-style-knowledge.md](c:\Users\hitea\Claude\LiveChord\doc\for-notebooklm\2026-04-18-accompaniment-style-knowledge.md) 診斷出 5 大死因（§2）並給出 6 大動感機制（§3）+ 8 個風格規格（§4）+ C 大調完整 MIDI 範例（§6）+ 自測清單（§8）。本計畫把它落成三個可獨立出貨、每一階段都**聽得出差異**的 phase。

**目標**：AI 伴奏從「固定 pattern」升級到「有動感的風格化 pattern」。事件 schema `{time, duration, pitch, velocity, hand, finger, chord_tone}` 保持向後相容，前端 waterfall / keyboard highlights 不需動。

**使用者已決定**：
- 做 Phase 1–3（跳過 Phase 4 多風格擴充、Phase 5 pedal 重測）
- Feature flag 預設 **on**
- 既有 43k 曲目的伴奏快取 **全庫清空** 讓新引擎一次生效

---

## Phase 1：Phrase-arc velocity + 消費 density_mult + style-aware humanize

**目標**：解決死因 2（velocity 常數）、死因 3（humanize 一刀切）、死因 4（density_mult 算了但沒用）。無 schema 改動，純算術層面的 velocity/timing 調整。聽覺上：chorus 明顯比 verse 大聲、beat 1 更 punchy、ballad 抖動小、jazz 抖動大。

### 檔案與修改位置

1. **[backend/ai/accompaniment_generator.py](c:\Users\hitea\Claude\LiveChord\backend\ai\accompaniment_generator.py)**
   - 檔頂新增常數 `ACC_ENGINE_VERSION = "v2"`
   - 在 `SECTION_PARAMS`（line 145–153）下方新增 helper `_phrase_arc_scale(chord_idx, n_chords, section_type) -> float`，回傳 0.85–1.10 的 cosine bell 乘數（chorus 峰值在後 1/3，verse 略平）
   - 在 `_build_left_hand`（line 384–525）與 `_build_rh_1plus3`（line 618–678）的簽章加入 `density_mult: float = 1.0` 參數
   - `_build_left_hand` 的 pattern loop（line 449–453）：
     - 在 `events.append` 前，若 `frac != 0.0` 且 `rng.random() > density_mult` → `continue`（只 drop 非 downbeat）
     - line 482 的 `velocity = int(base_velocity * vel_ratio)` 改為 `velocity = int(base_velocity * vel_ratio * _beat_weight(frac, style))`
     - 新增 helper `_beat_weight(frac, style) -> float`：Pop/Rock 在 beat 2/4（frac ≈ 0.25, 0.75）+8%，Ballad 在 beat 1/3 +10%
     - `rng` 用 `random.Random(seed=hash((chord_name, start_time)))` 保持決定性
   - `_build_rh_1plus3`（line 618–678）同樣套 density drop + beat_weight
   - `generate_accompaniment()`（line 877–941）：
     - line 911–912：`lh_velocity / rh_velocity` 再乘 `_phrase_arc_scale(i, len(chords), chord_section)`
     - line 922–924：把 `density_mult` 傳入 `_build_left_hand`
     - line 930–932：把 `density_mult` 傳入 `_build_rh_1plus3`
     - line 960–961：`_humanize(...)` 加 `style=current_style`（需從 loop 最後一輪抓 dominant style，或回傳 `right_events` 前記錄 `dominant_style = current_style`）

2. **[backend/ai/dynamics_engine.py](c:\Users\hitea\Claude\LiveChord\backend\ai\dynamics_engine.py)**
   - `HUMANIZE_TIMING`（line 477–482）下方新增 `STYLE_HUMANIZE` 字典：
     - `"Block" / "Arpeggio" / "Rhythm"` → timing σ ≈ 3–5ms, velocity jitter ±4
     - `"Walking" / "Shell" / "Stride"` → swing offsets（& of beat 延後 10–15ms），velocity jitter ±5
   - `humanize()`（line 488–536）簽章加 `style: str = None`
     - 開頭用 `STYLE_HUMANIZE.get(style, default)` 選 timing_offsets 與 vel_jitter_range
     - 不改寫既有的 `HUMANIZE_TIMING` 表，僅 style 有 override 時替換

3. **[backend/ai_api.py](c:\Users\hitea\Claude\LiveChord\backend\ai_api.py)**
   - line 447 cache filename：`f"{h}_{style}_{level}_{section_type}_{ACC_ENGINE_VERSION}.json"`（import ACC_ENGINE_VERSION 自 accompaniment_generator）
   - line 450–456 `nocache` 分支的 glob 保持 `{h}_*.json`，一次清掉所有版本

4. **[data/settings.json](c:\Users\hitea\Claude\LiveChord\data\settings.json)**
   - 加一個 key：`"accompaniment_v2_enabled": true`（預設 on）
   - 引擎開頭讀一次（建議在 `generate_accompaniment()` 入口、line 863 前後用 module-level helper 讀）；為 false → 跳過 phrase arc / density drop / beat weight / style humanize 全部新邏輯，行為等同改前。settings 讀取邏輯重用現有 `backend/settings.py` 的 `load_settings()`（若已存在）或 ad-hoc `json.load`。

### 快取處理

- 部署前：`data/accompaniments/*.json` + `V:\data\accompaniments\*.json` 全刪
- `ACC_ENGINE_VERSION = "v2"` 常數防止以後再混到 v1 cache

### Verification

- **程式驗證**：
  - 取一首庫內曲（建議 `C–Am–F–G` 類的 4 chord loop 或任一 pop ballad），比對新舊輸出的 velocity histogram：stdev 應從 ~5 上升到 ≥ 12。
  - 聽 intro / verse / chorus，event 數比例大致 0.5 : 0.9 : 1.3（density_mult 實際生效的指標）。
- **聽覺 A/B**：player 的 piano tab，同一首歌把 `settings.json` 的 flag 切 on/off，reload。打開 chorus：beat 1 應該更重、velocity 起伏可聞。
- **Playwright**：[http://192.168.50.6:8800/](http://192.168.50.6:8800/) 開一首歌、切到 piano tab、截圖 waterfall；檢查 `accData.left_hand.length` 與 `accData.right_hand.length` 的範圍正常（3 分鐘歌約 500–1500 events，不會爆量也不會空）。QA 截圖用 `qa-acc-phase1-*.png` prefix。
- **回歸**：隨機 5 首曲（含至少 1 首 jazz 標籤的），三個 style（Block/Arpeggio/Rhythm）各跑一次 `/api/accompaniment?nocache=1`，確認 event 欄位齊全無 NaN、無 `time < 0`、無 `duration <= 0`。

### Risk & Rollback

- **風險**：density_mult < 1.0 在 intro（0.5）可能掉掉過多音；`_beat_weight` 保證 downbeat 必留（`frac == 0.0` skip drop）。
- **Rollback**：`settings.json` `accompaniment_v2_enabled` 改 false，一次 request 就回到舊引擎；因為 ACC_ENGINE_VERSION 寫在 cache filename，舊 v1 cache 檔被清了就不會誤命中；若要保留新 cache 當作 bug 標本可以把 v1/v2 cache 並存。

---

## Phase 2：Micro-timing matrix + 跳出 0.25 量化格子

**目標**：解決死因 1（onset 硬對齊 0.25 格）。引入 swing 與切分（syncopation），初步加入 `Bossa Nova` 與 `Driving 8ths` 兩個新 style，`Swing Comping` 用 Charleston pattern 升級既有 `Shell`。聽覺上：Bossa 的 clave、Driving 8ths 的推進感、Swing 的 off-beat stab 都可辨識。

### 檔案與修改位置

1. **[backend/ai/accompaniment_generator.py](c:\Users\hitea\Claude\LiveChord\backend\ai\accompaniment_generator.py)**
   - `STYLE_DICT`（line 39–88）tuple 向後相容擴充：既有 `(frac, [idx], vel_ratio)` 3-tuple 繼續支援；新增 `(frac, [idx], vel_ratio, timing_offset_beats)` 4-tuple
   - consumer loop（line 449）：`for pi, pat in enumerate(pattern)` 改為 length check → `frac, indices, vel_ratio, timing_off = (pat + (0.0,))[:4]` 然後在 `event_time = start_time + frac * duration + timing_off * beat_dur`（beat_dur 需傳入或從外部算）
   - 新增 style entries：
     - `"Driving 8ths"`：8 個 onset (`[0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875]`)，每個 root+5+oct，velocity 曲線 downbeat 強、offbeat 弱
     - `"Bossa Nova"`：partido-alto RH 切分 `[(0.0, [0,1,2], 0.75, 0), (0.375, [0,1,2], 0.9, 0), (0.75, [0,1,2], 0.85, 0), (0.9375, [0,1,2], 0.7, 0)]`（參考知識文件 §6.7）
     - `"Swing Comping"`：Charleston `[(0.0, [1,3], 1.0, 0), (0.625, [1,3], 0.85, 0)]`（Shell voicing 的切分升級）
   - `GENRE_STYLE_MAP`（line 91–104）註冊：
     - `"bossa": ["Bossa Nova", "Shell", "Walking"]`
     - `"rock": ["Driving 8ths", "Block", "Rhythm"]`
     - `"jazz"` 首位改為 `"Swing Comping"`
   - `STYLE_CONFIG`（line 120–137）為 3 個新 style 加 `lh_level` / `rh_mode` / `lh_vel` / `rh_vel` 條目（參考 NotebookLM 文件 §4 對應段落）

2. **[backend/ai/dynamics_engine.py](c:\Users\hitea\Claude\LiveChord\backend\ai\dynamics_engine.py)**
   - 在 `STYLE_HUMANIZE`（Phase 1 已建）再加一層 `MICROTIMING_MATRIX[style][beat_idx] -> ms_offset`：
     - Swing 風格（`Swing Comping` / `Walking`）：`& of beat` 延後 15ms（66/33 swing ratio）
     - Bossa Nova：downbeat 略 anticipate（−5ms），off-beat 精確
     - Driving 8ths：全部 ±2ms 之內，保持推進
   - `humanize()`（line 488–536）在現有 beat-idx 查表後，若 style 命中 `MICROTIMING_MATRIX` → 覆寫 `base_offset`

### Verification

- **程式驗證**：
  - 強制 `style=Bossa Nova` 重算，檢查 `accData.right_hand` 中 `round(time_in_beat * 4) != time_in_beat * 4` 的比例 ≥ 25%
  - 強制 `style=Swing Comping`，抽相鄰兩個 8th onset，比對時距應接近 66:33
- **聽覺 A/B**：
  - Bossa 的 clave pattern 在 piano tab 上可被識別為 3-3-2 節奏
  - Swing Comping 的 Charleston 可聞 Beat 1 + off-Beat 2 的 stab 組合
- **前端回歸**：sub-0.25 onset 進到 waterfall，檢查 [player.js](c:\Users\hitea\Claude\LiveChord\frontend\js\player.js) 的 accData 渲染是否有假設整拍對齊。若 waterfall bar 位置用 `event.time` 直接 scale 應該沒事；若有任何 `event.time * 4` 之類的整數化 → 需要修
- **LH/RH collision filter**：`_filter_hand_collision`（既有於 accompaniment_generator.py，line 767 前後）有 ±0.05s 同時性判定；sub-0.25 onset 更易觸發，需驗證沒有誤殺

### Risk & Rollback

- **風險**：
  - Phase 2 的 4-tuple 改動牽涉所有 pattern；若 unpack 寫錯會讓既有 style 全部壞。必須先補一個 unit test：餵 3-tuple 進 `_build_left_hand` 輸出應與 Phase 1 完全相同
  - 前端 waterfall 若對 event time 做整數化會破圖
- **Rollback**：把新 style 從 `GENRE_STYLE_MAP` 移除 + `STYLE_DICT` 保留也無害（沒被選到就不會走）。`MICROTIMING_MATRIX` 整張表若 style 沒命中 fallback 到 Phase 1 行為。若 4-tuple unpack 壞了 → 恢復 3-tuple 迴圈是單行回滾。

---

## Phase 3：Voice-leading inversion picker + RH voicing variation

**目標**：解決死因 5（RH 永遠 block/gap-fill、同和弦每次一樣）。同一個 C 重複出現時，引擎選不同 inversion，避免「複製貼上」感。

### 檔案與修改位置

**[backend/ai/accompaniment_generator.py](c:\Users\hitea\Claude\LiveChord\backend\ai\accompaniment_generator.py)**

- `_build_rh_1plus3`（line 618–678）在 inversion 決定點（約 line 640–655，既有的 `voice_leading_optimize` 呼叫）前後：
  - 新增 helper `_pick_best_inversion(chord_notes, prev_pitches, range_lo=RH_LOW, range_hi=RH_HIGH) -> list[int]`
    - 列舉 3 個 inversion（root / 1st / 2nd）
    - score = `sum(abs(p - nearest_prev) for p in candidate) + root_doubling_penalty`
    - 回傳最低 score 的 pitch list
  - 取代既有的 naive voice-leading 呼叫
  - 同時維護 `_recent_chord_voicing_memo: Dict[str, int]`（module-level，按 chord_name → last_inversion_idx 記錄），連續遇到**同一個和弦名稱**時改選第 2 好的 inversion；換和弦重設或自然流動
- `_build_right_hand`（line 681–750）的 non-`1+3_once` 分支也套相同 helper
- 每個 RH event 加可選欄位 `"inversion": 0 | 1 | 2`（前端忽略；MIDI 匯出與 debug 用）
- 事件 schema 保持向後相容：新增欄位不影響既有前端

### Verification

- **程式驗證**：
  - 輸入合成和弦進行 `C-C-C-C`（4 個同和弦），統計 `set(tuple(sorted(e["pitch"] for e in group)) for group in per_chord)` 應有 ≥ 2 個不同 voicing
  - 正常進行 `C-Am-F-G`，檢查連續 chord 的 RH 上聲部（最高 2 音）位移 ≤ 2 個半音（voice leading 的量化指標）
- **聽覺 A/B**：挑一首 intro 有反覆同和弦的曲（如 Canon-based、或自製測試輸入），聽連續 bar 是否不再機械式重複
- **Playwright**：piano tab 截圖比對 Phase 2 vs Phase 3，同一首歌的重複和弦段應在 waterfall 上看出不同 inversion 的音高差異

### Risk & Rollback

- **風險**：`_pick_best_inversion` 可能把 RH 推到太高或太低，撞到 LH。既有的 `_filter_hand_collision` 會吃掉碰撞音，但頻繁觸發會讓 RH 音數減少 → 驗證時監控 `event count drop` 不超過 10%。
- **Rollback**：`_pick_best_inversion` helper 加一個 module-level 布林 `VOICING_VARIETY_ENABLED`（從 `settings.json` 讀 `accompaniment_voicing_variety_enabled`，預設 true）；false → fallback 到既有的 voice_leading_optimize 路徑。

---

## Cross-cutting

### 部署

- 每個 phase 改完 `backend/ai/*.py` 與 `backend/ai_api.py`：複製到 `V:\backend\ai\` 與 `V:\backend\ai_api.py`
- `data/settings.json` 改完複製到 `V:\data\settings.json`
- 依 [CLAUDE.md](c:\Users\hitea\Claude\LiveChord\CLAUDE.md) 的 Deploy Sync 規則用 `diff -q` 驗證
- Dual-instance (8800 + 8801) 都會在下次 `/api/accompaniment` 請求自動載新 code（FastAPI module reload 或 restart via `restart_dual.bat`）

### 全庫快取清空（Phase 1 啟用時執行一次）

```
# PC 側
rm data/accompaniments/*.json

# NUC 側（透過 V:\）
rm V:\data\accompaniments\*.json
```

清完後第一次訪問每首歌的伴奏會多花 0.5–2 秒重算，之後命中新 cache。ACC_ENGINE_VERSION 在 cache filename，未來再升級 v3 不會混檔。

### Feature flag 位置與優先序

- `data/settings.json`：
  - `"accompaniment_v2_enabled": true`（Phase 1）— 總開關，false 時 Phase 1/2/3 全停用
  - `"accompaniment_voicing_variety_enabled": true`（Phase 3 單獨）— 方便只關 voicing variety 調試
- 不用 env var（跟 [CLAUDE.md](c:\Users\hitea\Claude\LiveChord\CLAUDE.md) 現行風格對齊）
- 讀取：`generate_accompaniment()` 入口讀一次，用 function default 傳下去；避免每個 helper 都讀

### 前端不動

Phase 1–3 **不需改前端**。event schema 只新增可選欄位 (`inversion`)，既有欄位 `{time, duration, pitch, velocity, hand, finger, chord_tone}` 語意不變。waterfall、keyboard highlights、fingering display 都透過現有邏輯渲染。

若 Phase 2 的 sub-0.25 onset 在 [frontend/js/player.js](c:\Users\hitea\Claude\LiveChord\frontend\js\player.js) 被任何整拍量化（grep `Math.round(.*time.*4)` 或類似模式）就必須解除，但這屬於發現問題後的修補，不列入計畫。

---

## Critical files summary

| 檔案 | Phase | 改動性質 |
|---|---|---|
| [backend/ai/accompaniment_generator.py](c:\Users\hitea\Claude\LiveChord\backend\ai\accompaniment_generator.py) | 1, 2, 3 | 所有核心變動 |
| [backend/ai/dynamics_engine.py](c:\Users\hitea\Claude\LiveChord\backend\ai\dynamics_engine.py) | 1, 2 | humanize 加 style 與 micro-timing 矩陣 |
| [backend/ai_api.py](c:\Users\hitea\Claude\LiveChord\backend\ai_api.py) | 1 | cache filename 加 ACC_ENGINE_VERSION |
| [data/settings.json](c:\Users\hitea\Claude\LiveChord\data\settings.json) | 1, 3 | 新增 feature flag |
| [V:\backend\ai\*.py](V:\backend\ai\) + [V:\backend\ai_api.py](V:\backend\ai_api.py) | 1, 2, 3 | 部署同步（Deploy Sync） |
| [V:\data\settings.json](V:\data\settings.json) | 1, 3 | flag 同步 |
| `data/accompaniments/*.json` + `V:\data\accompaniments\*.json` | 1 | 一次性清空 |
| [doc/for-notebooklm/2026-04-18-accompaniment-style-knowledge.md](c:\Users\hitea\Claude\LiveChord\doc\for-notebooklm\2026-04-18-accompaniment-style-knowledge.md) | — | spec 參考，不改 |

---

## End-to-end verification

每個 phase 上線後按 [doc/QA.md](c:\Users\hitea\Claude\LiveChord\doc\QA.md) 的 UI QA 規則走一次：

1. `diff -q` 確認 `V:\backend\ai\accompaniment_generator.py` 與 dev repo 一致
2. `restart_dual.bat` 重啟，或等自然 reload
3. 瀏覽器開 [http://192.168.50.6:8800/](http://192.168.50.6:8800/)（Personal），進一首歌、piano tab
4. 聽 30 秒，對比知識文件 §8 的自測清單（chorus 明顯比 verse 大、同和弦不再複製貼上、swing 風格有 66/33 感）
5. 用 curl 打 `/api/accompaniment?path=...&nocache=1`，dump JSON，跑統計驗證（velocity stdev, off-beat %, voicing uniqueness）
6. Playwright 截圖存 `qa-acc-phaseN-*.png` 保存 QA artifact
7. 若該 phase 的驗證全過 → 部署到 8801 Beta（同檔案已複製到 V:\，無需額外動作）
8. 任一項失敗 → `settings.json` flag 改 false 即時回滾，舊引擎立刻生效

每個 phase 驗證通過後才動下一個 phase，不要平行開發。
