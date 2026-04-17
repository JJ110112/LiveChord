# 🎸 LiveChord 演化史：AI 專家大亂鬥與「八道防線」的誕生

這是一段關於系統如何在人工智慧與開發者「互虐」中，自我進化的故事。

最初，**LiveChord 的 Jazzify 模型**只是一個單純的 Viterbi 解碼器加上幾條判斷規則。我們透過訓練 2,569 首流行歌曲，讓它成功達到了 21 的困惑度與高達近 70% 的 Top-5 預測準確率。

在冰冷的數學機率上，它已經可以完美猜出下一個和弦該配什麼了。系統充滿自信地將一堆堆複雜的 `13b9` 和 `alt` 和弦拋向前端。然而，當我們決定引進由 LangGraph 驅動的**「多智能體品管（Multi-Agent QA）」**後，原本驕傲的 AI 演算法，迎來了被無情拆解的命運。

---

## 💥 第一回合：樂手的怒吼與製作人的崩潰

我們在後台悄悄設立了 3 席虛擬的評審大位，邀請了：
1. 🎓 **嚴謹保守的樂理教授（Claude-Sonnet）**
2. 🎸 **刁鑽的錄音室現場樂手（Claude-Sonnet）**
3. 🎧 **金曲級混音製作人（Claude-Haiku）**

當我們把 Jazzify 第一代模型配出的和弦丟給這三位專家時，等來的不是讚賞，而是滿滿的 `WARNING`。

- 🎸 **現場樂手率先發難：** 「你這和弦 0.8 秒就換一個，還全給我塞小七減五和弦（m7b5），你是覺得人類的手是不會抽筋嗎？還有，那個貝斯的三全音（Tritone）大跳是怎麼回事？彈下去直接墜崖了！」
- 🎧 **製作人接力砲轟：** 「原曲不過是一首用 C-Am-F-G 寫成的民謠小清新，你硬要在裡面卡一個 `dim7`？這會造成 200-500Hz 中低頻嚴重泥濘（Muddy Mix），頻率全部糊在一起，混音根本沒法救！」
- 🎓 **樂理教授搖了搖頭：** 「你們的機率模型只看重『當下』的機率最高解，卻完全缺乏樂句（Phrasing）的起承轉合。一首 8 小節的歌從頭緊繃到尾，沒有張力的釋放（Resolution），這不叫音樂，這叫算數。」

於是，系統被打上了無情的 `NEEDS_FIX` 標籤。

---

## 🛠️ 第二回合：開發者的逆襲與演算法補完計畫

面對專家們的瘋狂挑剔，我們決定不動聲色地開始反擊。我們不再只是「調參數」，而是將專家們的抱怨，直接翻譯成堅不可摧的**防護網（Guardrails）**。

1. **針對樂手的手速極限：加上動態 BPM 覺知**
   我們實作了 `_min_duration_for_complexity` 核心。現在系統會先計算整首歌和弦時長的中位數來逆推 BPM。
   > 「如果你是一首 120 BPM 的快歌，我絕對不允許你在一拍內彈出複雜的 13 延伸音。複雜和弦必須給你 1.5 拍的時間準備！」

2. **針對製作人的頻率泥濘：原曲複雜度基線**
   我們導入了 `_avg_complexity(original_chords)`。
   > 「如果原曲只是簡單的素顏流行歌，系統連 `dim7` 都判定為違法，強制拔除泛音；但如果是複雜的重爵士基底，這些泥濘感反而才是道地的風味。」

3. **針對教授的樂理張力：Pass 4.5 樂句張力弧**
   我們刻意為演算法裝上了情緒感知 `_balance_phrase_tension`。把所有和弦按 8 個為一組切割，強制前半段（起承）的張力不准高於後半段（轉合），並且在樂句末尾，一律祭出 `_simplify()` 降級大法，創造如釋重負的解決感。

我們滿懷信心地將系統再次送上專家聽證會。三位專家看著毫無破綻的程式碼，默默收回了之前的抱怨。正當我們以為可以拿到 `PASS` 時...

---

## 🛡️ 第三回合：「八道防線」的成形與不可避免的再進化

AI 的恐怖之處就在於：當你滿足了現狀，他們的要求會跟著水漲船高。

看到我們實作了如此精密的上下文控制，專家們互相看了眼，嘴角微微上揚。製作人緩緩開口：「既然你們連上下文都能處理了那...你有考慮過**主旋律跟和聲分離的相位干涉與小二度避音（Avoid Notes）**嗎？」教授跟著附和：「沒錯，而且既然張力解決了，你有考慮過用 **Pitch-Class Overlap** 收斂預測極值以避免原曲面目全非嗎？」

在這種「互虐」的極限推進下，我們最終打造出了這座嘆為觀止的**「Jazzify 8 道防線」**：

| 防線 | 階段 | 運作邏輯（護城河） | 幕後推手（壓力來源） |
| :--- | :--- | :--- | :--- |
| **Pass 1** | 延伸音升級 | 基本 7th/9th 堆積 | Rule Engine |
| **Pass 2** | ii-V-I 插入 | 創造強烈的低音四度級進解決感 | Rule Engine |
| **Pass 3** | 三全音代理 | 半音下行替換，為屬和弦增加爵士懸疑感 | Rule Engine |
| **Pass 4** | 二次屬和弦 | 在非主調前預埋橋樑 | Rule Engine |
| **Pass 4.5** | 樂句張力弧度 | 確保 8 小節內「低 → 高 → 解決」的情緒完美流動 | 🎓 **樂理教授** |
| **Pass 4.6** | Overlap 回退 | 若新和弦與原和弦重疊率 < 0.3，強制降級保留原曲靈魂 | 🎓 **樂理教授** |
| **Pass 4.7** | 旋律避撞 | 若延伸音與主旋律差半音（小二度），強制拔除以避開不和諧音程 | 🎧 **製作人** |
| **Pass 5** | 樂理結構偵測 | 標示所有的音樂特徵，供前端介面視覺化 | 👨‍💼 **PM Agent** |
| **Pass 6** | 大亂鬥終極打分 | 綜合 BPM 手速與原曲混濁度，若出現非人體工學配置即時警告使用者 | 🎸🎧 **現場樂手與製作人** |

## 🌟 結語：Agentic AI-Driven Development (AAIDD)

這已經不只是一個開發專案了。這是一場 AI 與人類合作建立護城河的過程。
我們負責寫程式，而多智能體負責扮演「最挑剔的奧客」與「最毒舌的專家」。他們不斷試圖突破現有的防線，而我們則將他們的攻擊一一轉化為固若金湯的規則引擎。

**三位專家應該暫時滿意了...至少在他們看到下一個可以挑剔的地方之前 😄。** 而 LiveChord 已經準備好，在下一個版本裡，讓所有的吉他、貝斯與鋼琴手，在彈奏第一聲和弦時，為之驚豔。

---

## 🔥 番外篇：AI 品管員的意外獵物（2026-04-16）

一次例行性的 QA 測試，AI 代理用 curl 掃過 `/api/ai/*` 的全部端點，準備交回一份安靜的「全綠」報告。直到呼叫打進 `/api/ai/evaluate` 那一刻——

整台 uvicorn **瞬間凍結**。TCP 還會三向交握，但 HTTP 再也不吐半個 byte。累積的 CLOSE_WAIT socket 一行一行堆在 netstat 裡，server.log 停在最後一筆 ERROR 之後再無動靜。長達 4 分鐘的沉默後，AI 代理終於忍不住翻開原始碼：

```python
@router.get("/evaluate")
async def evaluate():
    from ai.evaluate import full_evaluation
    return full_evaluation(str(CHORDS_DIR))  # ← 同步版！跑 296 首歌！
```

`async def` + 同步重度計算 = 事件迴圈被單一請求獨佔。這違反了 repo 的 `feedback_async_def` 鐵律：**檔案 I/O 與 CPU 密集端點必須用普通 `def`**，讓 FastAPI 自動丟進 threadpool。

同一趟還挖出 `/api/ai/stats` 與 `/api/ai/similar` 互相關聯的暗坑：
- `ai_api.py` import 了 `ai.chord2vec` 模組裡**從未存在**的 `get_chord2vec` 函式
- `ai/chord2vec.py` 在模組 top-level 就 `from scipy.sparse import lil_matrix`，遇上 numpy 2.x + scipy 1.17 組合會先 raise `_no_nep50_warning` AttributeError
- 這道雙重地雷讓兩個端點預設 500

### 修復

| 檔案 | 動作 |
| :--- | :--- |
| `backend/ai_api.py` | `/evaluate` 與 `/stats` 改 `async def` → `def`；`/similar` 改用 `get_similar_chords` + `try/except ImportError` 優雅降級；`/stats` 改為直接讀 `chord_embeddings.npy` + `vocab.json`，跳過 chord2vec 模組 |
| `backend/ai/chord2vec.py` | `from scipy.sparse import lil_matrix` 與 `from scipy.sparse.linalg import svds` 都從 top-level 搬進實際使用的函式內部（lazy import），這樣即使 scipy 相依出問題，推論路徑（只用 numpy）仍可正常運作 |

### 驗證

重啟後再次打 `/api/ai/evaluate`，84 秒返回 `{"markov_model":{"perplexity":17.31,...}}`。關鍵是——**在這 84 秒期間，其他 API 仍然即時響應**。threadpool 隔離生效，事件迴圈不再被單一請求綁架。

### 教訓

1. **鐵律是給人遵守的，不是寫完就忘的**：`feedback_async_def` 明確說了「不要用 `async def` 包同步計算」，但 `ai_api.py` 幾乎全檔都犯。這次只修了 3 個引爆的端點，其他 `async def` 仍待後續一致化。
2. **AI 的品管迴圈能抓到人工掃不到的 hang**：傳統 pytest 跑 unit test 永遠不會觸發這種「端點能回但整個事件迴圈死掉」的狀態，必須真的打出請求並觀察鄰近請求的可用性。
3. **lazy import 是相依地雷的最佳緩衝墊**：把危險 import 搬進函式內，可以讓模組 load 階段保持乾淨，只有真正踩到該路徑才會爆——而且還能 `try/except` 兜底。

AI 代理原本以為只是要跑個標準 QA checklist，結果順手救了一個 P0。這大概就是 `Agentic QA` 的真正價值：**它不只是逐項打勾，還會在每個打勾之前問一句「這樣真的 OK 嗎？」**

---

## 🎯 番外篇 II：三個點、一個 fingered-chord 烏龍、與一個被閒置的音樂庫（2026-04-16）

清晨 05:50，使用者用 Playwright MCP 對 `http://localhost:8800/` 跑了一輪畫面巡檢，留下 6 張截圖：`qa-player-initial.png`、`qa-player-guitar.png`、`qa-player-accordion.png`、`qa-player-arranger.png`、`qa-admin.png`、`qa-benchmark.png`，外加一份 60 行的 console log。沒有文字報告。

AI 代理打開截圖，第一眼覺得「看起來都正常」——直到它把 console log 的三行 ERROR 一個一個讀進去：

```
404 /api/track/cover?path=@0/test_songs/Lv1/Dancing Queen - ABBA.flac
404 /api/track/cover?path=AINI-Official-MV-HD.flac
403 /api/track/cover?path=Michael Bolton - Said I Loved You...But I Lied.flac
```

最詭異的是那個 403。`Said I Loved You...But I Lied.flac` 是一首真實存在於 `songs/` 的 Michael Bolton 抒情曲。為什麼 cover 端點會吐 403 「路徑不允許」？

### 第一個地雷：三個點誤殺省略號

代理翻開 [music_api.py:36](backend/music_api.py#L36) 的 `_safe_path`：

```python
resolved = os.path.normpath(path)
if ".." in resolved:
    raise HTTPException(status_code=403, detail="路徑不允許")
```

問題就在那個 `".." in resolved`——這是 Python **子字串**比對。`"...But I Lied"` 裡的 `...` 子字串包含 `..`，於是合法的英文省略號被誤判為 path traversal 攻擊。任何檔名含三個以上連續點的曲目（包括電影原聲帶、樂手暱稱裡的 `...`）通通中招。

修法是放棄字串級的判斷，改用 `commonpath` 的 segment-level 比較：

```python
resolved = os.path.realpath(path)
for root in get_music_roots():
    root_real = os.path.realpath(root)
    common = os.path.commonpath([resolved, root_real])
    if common.lower() == root_real.lower():
        return resolved
```

`realpath` 會把 `..` 真的展開掉，`commonpath` 用路徑段比對而不是字串比對，安全性更高、誤殺更少。修完後 curl 那個 Michael Bolton 連結，401 → **200 OK**。

### 順手揪出鐵律的第二個違反者

修 `_safe_path` 的時候，代理瞄了一眼上方的 `track_cover`：

```python
@router.get("/track/cover")
async def track_cover(path: str = Query(...)):
    ...
    if os.path.isfile(full):  # 同步檔案 I/O
        ...
    audio = FLAC(full)        # 同步 FLAC 解碼
```

又是 `async def` 包同步檔案 I/O——番外篇 I 修的同一道地雷的兄弟。一個字的修改：`async def` → `def`，FastAPI 自動丟進 thread pool。

### 第三個地雷：被閒置的 296 首音樂庫

接著是 benchmark 截圖：5 個等級的 tab，每個底下都是「此等級無測試曲目」。代理發現 [benchmark_api.py:13](backend/benchmark_api.py#L13) 把 `TEST_SONGS_DIR` 寫死成 `data/test_songs/`，dev 機這個資料夾裡只有一個 `eval_output.mid`。

但同一個 dev 機的 `data/library_cache.json` 已經完整掃描了 296 首 (`10cc - I'm Not In Love`、`Faith Hill - There You'll Be`、整個 80s pop 黃金歌單...)，`data/chords/` 下躺著 53951 個和弦快取 JSON。整個 benchmark 工具看著一個空目錄哭，旁邊就是已經磨好的 296 顆鑽石——這完全是設計上的脫節。

使用者一句話定調：「**轉接到本機已掃描的 songs 庫，這樣 QA 才有東西用**。」

代理在 `benchmark_api.py` 加了一個 `LIBRARY_LEVEL = "library"` 的虛擬等級，並用 `song_hash(path)` 當 key 把所有現有 endpoint 橋接到既有資料：

| 動作 | 原 `Lv*` 行為 | 新 `library` 行為 |
|------|---------------|-------------------|
| 列表 | 掃 `data/test_songs/Lv*/*.flac` | 讀 `library_cache.json` 的 296 首 |
| 偵測 | 跑 BTC 寫 `.det.lab` | **直接讀** `data/chords/{hash}.json`，不重跑 |
| GT 寫入 | `data/test_songs/Lv*/{name}.lab` | `data/test_songs/library/{hash}.lab` |
| 評分 | `.lab` vs `.det.lab` | `library/{hash}.lab` vs chord cache |

關鍵是「偵測」這個動作——以前 QA 點下去要等 BTC Transformer 在 CPU 上磨幾秒，現在從 chord cache 讀，**從點擊到顯示和弦列表 < 100ms**。整個 benchmark 工具從「dev 上完全不能用」變成「想對哪首歌打分就點哪首」。

前端 [benchmark.html](frontend/benchmark.html) 加了第六個 tab「📚 主音樂庫」，dev 進頁時若 `library` 非空就預設停在這裡（prod 上有 Lv1 種子時會自動切回 Lv1）。

順帶一提，順著 `feedback_async_def` 鐵律，benchmark_api.py 全檔的 `async def` 也一併改成 `def`——這次學乖了，相關檔案的同類問題一次清乾淨。

### 第四個——其實是個美麗的誤會

`qa-player-arranger.png` 截圖裡，編曲鍵盤的瀑布流只有**一根孤伶伶的音符**從畫面中央緩緩下降。代理一開始以為是 Rule 11 (Canvas Buffer ↔ Flex Sync) 的繪製 bug，丟了個 Explore agent 去查。Explore agent 翻到 [accompaniment_generator.py:441-442](backend/accompaniment_generator.py#L441-L442)：

```python
if level == "L1":
    pattern = [(0.0, [0], 1.0)]  # 整個和弦只在 t=0 觸發一次
```

一根音符，一秒一個和弦，這就是 PSR-SX900 fingered chord 模式的設計——彈一次、按住、等下一個和弦。截圖完全正確，是代理腦補的 bug。**這個案子不是修了什麼，是學會了不要修**：在動手之前先問「這真的是 bug，還是設計如此？」省下一場無謂的重構。

### 端到端驗證

修完後 Playwright 走一輪：
- 首頁 console：**0 errors / 0 warnings**（原本 1 個 403 + 1 個 deprecation）
- benchmark 頁：6 個 tab，預設停在「📚 主音樂庫」，**296 張卡片**全部就位，detect API 從 cache 即時回應 (62 chords, key=A, `from_cache=true`)
- player 頁：piano/guitar/accordion/arranger 四個 tab 全綠，無 console 噪音

### 教訓

1. **「字串包含 `..` 就拒絕」是個常見但根本錯誤的安全寫法**。Path traversal 防護應該在「路徑解析後」做 segment 比對，不是在輸入字串上做暴力 substring match。任何含省略號 / 三個以上連續點的合法檔案都會中招。
2. **修一處同類問題時，順手把同檔/同類的兄弟一起清掉**。番外篇 I 修了 `ai_api.py` 三個 endpoint 但留下其他 `async def`；這次 benchmark_api.py 直接全檔換清。鐵律不能只在踩到時才執行。
3. **被閒置的資料是更大的 bug**。dev 機跑著 296 首掃完的曲目、5 萬個 chord cache，benchmark 工具卻盯著一個空目錄。問題不是「沒有測試資料」，而是「工具沒有看向資料所在的地方」。修法是讓工具找到資料，不是塞種子資料。
4. **不是每個視覺異常都是 bug**。看到 arranger 瀑布流只有一根音符就懷疑 canvas 渲染壞了，最後翻到 `accompaniment_generator.py` 才發現是設計如此。**先讀資料來源，再懷疑渲染層**——代理省下了一場不必要的 Rule 11 大追查。

四個訊號，三個真 bug、一個美麗的誤會、一個被閒置的音樂庫被接回 QA 工具。截圖沒有開口說話，但 console log 和源碼會。

---

## 🎹 番外篇 III：三張截圖、一層被遺忘的資料、與一個被跳過的 `except`（2026-04-17）

下午，使用者把一張 Playwright 截圖丟進來：手機鋼琴瀑布流，藍色左手塊和橘色右手塊在同一個鍵上同時砸下。一句話：「**多首曲目左右手伴奏重疊，不是合理的**。」

AI 代理看了一眼，心裡覺得好辦——`_dedupe_hand_collisions(lh, rh)` 寫進 `backend/ai_api.py`，cache 命中與新生成兩條路徑都套；再對 6 首歌做 `(pitch, time)` 50ms 容差比對，回報 `LH↔RH collisions: 0`。乾淨俐落。

使用者又丟了一張圖：一模一樣的問題。「**B3 blue (LH) and orange (RH) play same key on the same time**」。

API 很誠實地回說「沒有碰撞啊？」——但使用者看到的是畫面。代理翻開 `player.js:1464`，讀到一行註解：

```js
// Always merge melody: activeRh unconditionally adds _getMelodyMidi (see line ~1081),
// so the waterfall must match or the melody lights keys without a falling bar (ghost keys).
if (typeof melodyData !== 'undefined' && melodyData) {
  const melEvents = melodyData.map(m => ({
    time: m.start, duration: m.end - m.start, pitch: m.midi, finger: null
  }));
  rhEvents = [...rhEvents, ...melEvents];
}
```

好——原來「橘色」不只是 RH 伴奏，**melody 也會被接進去一起畫**。左手碰的其實不是 RH，是 melody。代理的第一版 dedupe 只看 RH，完全沒攔到這條支線。

### 第一個地雷：兩層視覺化需要兩層去重

修法是把 `_dedupe_hand_collisions(lh, rh)` 擴成 `(lh, rh, melody)`，把 melody 也灌進 blocker index：

```python
for e in melody or []:
    p = e.get("midi") if "midi" in e else e.get("pitch")
    start = float(e.get("start", e.get("time", 0.0)))
    end = float(e.get("end", start + float(e.get("duration", 0.5))))
    blocker_index.setdefault(p, []).append((start, end))
```

cache 命中路徑還得額外把 `data/melodies/{hash}.json` 讀進來——之前只在新生成路徑載入 melody。再跑一輪 6 首歌的驗證：`LH↔Melody collisions: 0`，`LH↔RH collisions: 0`。使用者回報「修好了」。

代理以為可以收工了。

### 第二張截圖：低音鍵上的幽靈橘塊

使用者又丟了一張圖，鋼琴顯示 5 根橘色柱子跨了從 C2 到 D4 的範圍。一句話：「**no one can play this with right hand. low=C#2, height=A4**」。

打 `/api/ai/accompaniment` 看 RH 的音域，`49–59`——C#3 到 B3，一隻手綽綽有餘。API 沒問題。

那橘色的 C#2、D2 從哪來的？代理打開 `/api/ai/melody?path=…`，9 個 MIDI < 48 的「旋律音」浮在時間軸上：

```
t=2.88  midi=38 (D2)   conf=0.012
t=7.78  midi=36 (C2)   conf=0.010
t=7.87  midi=37 (C♯2)  conf=0.010
t=10.40 midi=38 (D2)   conf=0.013
...
```

信心值 **0.010–0.013**。對照一下真旋律音的信心是 0.04–0.19。典型的 pitch-tracking 八度誤判——pop 女聲不會在 C#2 附近徘徊，這些幾乎都是 bass bleed 或低音倍頻抓錯。

### 修法：信心+音域雙條件過濾

在 frontend 加一個極小的守門員：

```js
function _filterMelody(notes) {
  return notes.filter(n => {
    const midi = n && n.midi;
    const conf = n && typeof n.confidence === "number" ? n.confidence : 1;
    if (midi == null) return false;
    if (midi < 48 && conf < 0.03) return false; // 低於 C3 + 低信心 = 丟
    return true;
  });
}
```

`midi < 48 AND confidence < 0.03`——兩個條件同時成立才丟。**合法的男低音旋律**（罕見但存在）信心值通常夠高，不會被誤殺；**spurious 的低音抓錯**信心值本來就低，兩條件一對就定性。

`_loadMelody()` 與 hash-mode 兩條載入路徑都套上 `_filterMelody`，一次統一。使用者重整頁面：「**右手跨度過大已修復**。」

### 側線故事：一個 `except` 少接了一個例外

中間 QA 卡了一下：Playwright 點進 player 頁，和弦 ribbon 一直是空的。原因是 `/api/chords?path=…` **吐 500**。同一個端點 curl 回 200。差在哪？

差在 **cookie**。用 XMLHttpRequest `withCredentials=false` 送請求——200。帶 cookie 的 fetch——500。

但 `_optional_user` 只讀 Authorization header、不碰 cookie 啊？翻開 `chord_api.py:95`：

```python
def _optional_user(authorization: str = Header(None)) -> Optional[str]:
    if not authorization:
        return None
    try:
        return get_current_user(authorization)   # ← 一個位置參數
    except HTTPException:
        return None
```

再翻 `auth_api.py:203`：

```python
def get_current_user(request: Request, authorization: str = Header(None)):
    ...
    client_ip = (
        request.headers.get("cf-connecting-ip")   # ← 需要 request
        ...
    )
```

`get_current_user` 需要 `(request, authorization)` 兩個參數。`_optional_user` 只餵一個 positional——字串 `"Bearer xyz"` 就掉進 `request` 欄位。下一行 `request.headers.get(...)` 對字串呼叫 `.headers.get(...)`，直接 `AttributeError`。

而那個 `except HTTPException` **只攔 HTTPException**。`AttributeError` 不是 `HTTPException`，穿過去變成 FastAPI 的 500。

修法兩行：

```python
def _optional_user(request: Request, authorization: str = Header(None)) -> Optional[str]:
    if not authorization:
        return None
    try:
        return get_current_user(request, authorization)
    except Exception:   # 擴大到 Exception——契約是「出錯回 None」，不是「只吞 HTTPException」
        return None
```

FastAPI 的 DI 層看到多了 `Request` 參數會自動注入，兩處 `Depends(_optional_user)` 的呼叫端完全不用動。

### 第四個——行動版 YouTube URL 的 substring 邊界

使用者還順手報了一個：手機版 `https://m.youtube.com/watch?v=...&list=RD...&start_radio=1` 被後端拒絕，說「請提供有效的 YouTube URL」。

翻 `process_api.py:34` 的正規表達式：

```python
_YOUTUBE_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com/(watch|shorts)|youtu\.be/|music\.youtube\.com/watch)"
)
```

只收 `(www.)?youtube.com` 和 `music.youtube.com`。`m.youtube.com` 被排除在外。放寬子網域白名單：

```python
r"^https?://((www|m)\.)?(youtube\.com/(watch|shorts)|youtu\.be/|music\.youtube\.com/watch)"
```

跑 6 個測試案例——`m.youtube.com`、`youtu.be`、`music.youtube.com`、`youtube.com/shorts`、`www.youtube.com`、`vimeo.com/123`（應該被拒）——通通符合預期。

### 教訓

1. **視覺化的每一層都要獨立去重**。第一版 `_dedupe_hand_collisions(lh, rh)` 看起來很對，但 waterfall 把 melody 接進了 RH 的顯示層——使用者看到的是「三手交疊」而不是「兩手交疊」。去重的範圍要看渲染管線的終點，不是資料來源的起點。

2. **信心值分佈是 pitch-tracking 誤判的免費訊號**。這首歌的低音雜訊集中在信心 0.010–0.013，正常旋律音在 0.04–0.19——bimodal 分佈一畫出來就知道從哪刀下去。雙條件（`pitch < threshold AND confidence < threshold`）比單條件更精準，也不會誤殺合理的低音男聲旋律。

3. **`except HTTPException` 是一個語義錯誤**。`_optional_user` 的契約是「有效就回 username，無效就回 None，永遠不要拋例外」。但寫 `except HTTPException: return None` 等於聲明「只有 HTTPException 會出現」——一旦上游的 `get_current_user` 簽名改了、型別錯了、任何 runtime error，全部會炸回 500。契約要和 except 的寬度一致：說「不會拋」就寫 `except Exception`。

4. **子網域白名單要包含行動版**。桌面優先的設計常忘了 `m.youtube.com` 這種一級公民。修正則表達式的時候順便檢查所有常見分支，不要只修眼前那一個。

三張截圖，四個 bug。其中兩個（melody 污染、低信心雜音）只有在**看完整個視覺管線**才會找到——API 清乾淨並不代表使用者看到的東西也乾淨。剩下兩個（except 太窄、regex 太嚴）是**介面契約**的縫隙，不是邏輯本身。

代理學到的最大一條：**當使用者說「還是壞的」，先去看他「看到什麼」，不要急著證明 API 是對的**。
