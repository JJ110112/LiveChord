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

---

## 💥 番外篇 IV：一份 settings.json、四重路徑反斜線、與被逼出來的 dual-instance 架構（2026-04-19）

代理這天只是想「把 Phase 1 v2 AI 伴奏引擎同步到 V:\ 生產」。`.py` 檔逐一複製、`robocopy` 173,716 個 accompaniment JSON 全部就位、restart 之後 Playwright 驗證 8800 上線完美。代理得意地回報「Phase 1 v2 已在 NUC 8800 上線」。

然後使用者傳來一張截圖：**8801 admin 顯示「LiveChord Core 已停止、總曲目 0、和弦譜 0、覆蓋率 0%」、`\\LOVE\FavoriteSongs` / `\\LOVE\music` 兩個根目錄變空殼、啟用群組整排消失。**

代理腦中瞬間閃過一個詞：**是我幹的。**

### 🔍 一份檔的連環爆炸

追查幾秒就找到元兇：同步 `.py` 時，代理順手把 **`data/settings.json` 也整份複製** 過去。問題是——PC 本機那份 `settings.json` 其實**早就是壞的**：

```json
"music_roots": [
  "\\\\\\\\LOVE\\FavoriteSongs",
  "\\\\\\\\LOVE\\music"
]
```

JSON 解碼後變成 `\\\\LOVE\FavoriteSongs`（**四個反斜線開頭**），不是標準 UNC 的兩個 `\\`。PC 本機從來沒跑過 Core（runtime 是 NUC），所以這個壞格式**躺了不知多久沒人發現**。代理一把它覆蓋到 V:\，NUC 繼承了壞路徑 + 一併丟失使用者精心勾選的 8 個群組清單（未分類 / Christmas / Jam / Jazz / POP / Relax / EDM / Other）。NUC 重啟後想 scan，掃到個鬼。`library_cache.json` 被重建成 `{"total_tracks": 0, "tracks": []}`，43,482 首歌從 admin UI 上**憑空蒸發**。

### 🛡️ 第一層補救：不要再讓這件事發生

表面修復只花五分鐘：改回 2-backslash、補回 8 個群組。但根本問題還在——這種「整份檔案覆蓋」哪天還會發生（來自代理、來自使用者、來自某個我們不知道的腳本）。

於是寫了一個 **save-time auto-snapshot**：每次 `save_settings()` 前，把舊版複製到 `data/backups/settings/settings_<ts>.json`；rolling 保留 30 份；`restore` 前再快照一次，連回滾後的版本都救得回。

### 🪓 使用者一句話點破更深的瘡疤

修好剛要收工，使用者說：

> 「**8800 admin 是個人版，目前正常。8801 admin 不需要個人版的功能 - Core、和弦管理、萃取叢集、活動紀錄。然後各自有不同的備份功能。**」

代理愣住。一直以來 8800 和 8801 讀**同一個** `V:\data\settings.json`，只靠環境變數 `LIVECHORD_MODE` 區分。在這個設計下「各自有不同的備份」根本實作不出來——backup 恢復哪一份，兩邊都會跟著變。

代理先用 `backup filename prefix`（`settings_personal_*` vs `settings_beta_*`）做淺層隔離，但發現這只是「看起來分開、其實還是同一檔」的假象。跨 lane restore 會把整份覆蓋掉，連對方專有的欄位也會被抹掉。

於是使用者選了真正的分離方案 **B**——三檔結構：

| 檔案 | 誰寫 | 誰讀 | 典型欄位 |
|---|---|---|---|
| `settings_personal.json` | 只有 8800 | 只有 8800 | `music_roots`, `auto_chord_active_groups`, Core flags |
| `settings_beta.json` | 只有 8801 | 只有 8801 | 保留擴充（未來 beta-only flags） |
| `settings_shared.json` | 兩邊都可寫 | 兩邊都讀 | `accompaniment_v2_enabled`, `settings_backup_targets` |

搭配 `SHARED_KEYS` 明確標記、`_migrate_legacy_settings_if_needed()` 一次性從 legacy `settings.json` 拆分（舊檔保留當 archival reference，再也不寫入）。

### 🔒 深度防禦：前端藏了不夠，後端也要擋

前端隱藏 beta 不需要的 card 很快就做了。但沒多久使用者又送來 log：

```
INFO:     192.168.50.43:... - "GET /api/auto/status HTTP/1.1" 200 OK
INFO:     192.168.50.43:... - "GET /api/chords/stats HTTP/1.1" 200 OK
INFO:     192.168.50.43:... - "GET /api/extraction/status HTTP/1.1" 200 OK
...（每 2–3 秒一輪）
```

**8801 還在呼叫 Core polling endpoint！** 不但如此，它還在和 8800 的 Core 搶 CPU / SMB I/O。原因：`pollStatus()` 是個 `setTimeout` 遞迴 loop，admin.html 裡 6 個 personal-only function 都會在頁面打開後自動觸發；browser 吃 cache 載舊版 HTML，`applyDeploymentModeUI` 的 async fetch 還沒回來之前第一輪已經打出去了。

修法堆起來：
1. **Synchronous URL detection**：HTML 頂部 `<script>` 直接用 `window.location.port === "8801"` 設 `window._lcIsBeta` — 這是同步的，沒 race condition
2. **6 個 function 頭部 early-return**：`loadSettings` / `loadGenres` / `loadMusicRootSettings` / `loadGroups` / `loadTrackList` / `pollStatus` 全部開頭 `if (window._lcIsBeta) return;`
3. **`extraction.html` redirect**：beta 一進 `/extraction` 直接跳 `/admin`
4. **後端 `require_personal_mode()` 深度防禦**：13 個 personal-only endpoint 加 FastAPI dependency，beta 打進來直接 404。即使未來前端 cache 壞、第三方 JS 亂打、外人 curl 8801 的 /api/auto/start，都打不到。
5. **`start_worker()` 硬閘**：`if _current_mode() == "beta": return False` — 連從後端邏輯誤觸發都擋。

### 🐔🥚 再一個連環錯設計：scan filter 放錯層

使用者繼續發現問題：「掃完 `\\LOVE\FavoriteSongs` 就停了，music 底下其他資料夾呢？」

代理追進 `_scan_dir`：原本設計把 `active_groups` filter 套在第一層 dir（為了「節省 SMB I/O」）。聽起來合理，但結果是：

> **未啟用群組的 track 永遠不會進 library_cache → admin UI 永遠看不到這些 group → 使用者永遠無法勾選它們。**

這是經典的雞生蛋蛋生雞。想勾的看不到，看得到的只有勾過的。

修法很直覺一旦看清楚：**filter 放錯層了**。
- **Scan**（便宜：只是 listdir + 讀 metadata）→ **不 filter**，永遠走訪全部建完整 cache
- **Detection**（貴：跑 BTC / MIDI 匹配）→ **filter**，只對 active_groups 的曲目偵測

`_scan_dir` 第一層的 filter 移除；`_get_unanalyzed_tracks` 繼續套 active_groups（它本來就有做）。Classical / Sleep 留在 cache 裡但不被 detect，完美符合使用者的意圖：「我就是要它們在 UI 上看得到但不要跑和弦偵測」。

### 🎯 最後一哩：別讓使用者等你掃完

以為大功告成。使用者再傳截圖：「Scan 已經跑到 17846 首了，但 admin UI 啟用群組還是只顯示 3 個（Christmas、Classics、和 FavoriteSongs/未分類），為什麼要等 scan 完才看到類別？」

追查 `list_groups()`：它從 `library_cache.tracks` 累算 group label，scan 沒走到的 subfolder 就沒 track，沒 track 就沒 group 顯示。

修法簡單粗暴：`list_groups()` 回傳前多跑一次 **listdir 每個 music_root 第一層**，對每個實際存在於磁碟但 cache 還沒 track 的 folder 產生一個 `track_count=0` 的 placeholder。這樣 admin UI 第一次載入就看到**所有** 10+ 個類別，能立刻勾選管理，根本不用等 scan 跑完。

成本？每個 root 一次淺層 listdir，SMB 下幾十毫秒。和整個 scan 比起來微不足道。

### 🌅 破壞換來的新生

使用者結案時說了一句：「**有時就是得破壞才有新生啊。**」

這次災難的一份 settings.json 污染，最後逼出來這些東西：

| 原本的狀態 | 現在的狀態 |
|---|---|
| 單檔 `settings.json`、兩 instance 共用 | 三檔分離 `personal` / `beta` / `shared` + 明確 `SHARED_KEYS` |
| 沒有自動備份，誤覆蓋無法回滾 | 每次 save 自動 snapshot，多 target 寫入（local + NAS 等）、rolling 30 份 |
| 前端隱藏就算了，後端無防線 | `require_personal_mode()` 13 個 endpoint + `start_worker()` beta 硬閘 |
| `_lcIsBeta` 靠 async fetch，有 race condition | 同步 URL detection，first paint 就有正確值 |
| Scan filter 硬塞在錯誤的層 | Filter 正確分層：scan 建 index、detection 才做重工過濾 |
| UI 要等 scan 跑完才看到類別 | `list_groups` 加 listdir placeholder，邊掃邊勾選 |
| Admin UI 沒說明，使用者直覺錯誤 | 「啟用群組」「跳過類別」加 inline help 講清楚語義 |
| 活動記錄只寫「開始自動增量掃描」 | 印出 N 個音樂庫、每條路徑、per-root 進度（走訪 XX 首） |

### 🎓 這次最痛也最值的五條

1. **絕對不要整份覆蓋 `V:\data\settings.json`**，只能增量修改。Settings 檔應該 git-ignored 但 schema 要清楚、migration 要 robust。
2. **Filter 要放在對的層**。Index 建立階段（scan、cache）永遠該做完整，重工階段（detection、inference）才 filter。在 index 階段 filter 會製造 chicken-and-egg UI bug。
3. **深度防禦不可省**。前端隱藏是為了 UX，後端 guard 是為了安全；兩者獨立存在，缺一不可。
4. **破壞沒你想像中糟**。如果不是這次 settings.json 污染，代理永遠不會去做 B 方案、深度防禦、scan filter 分層。單檔共享 + 前端 hide + scan filter 湊合著能跑，但只有災難才會揭露它們每個都是地雷。
5. **使用者一句話常常比代理的架構感更準**。「各自有不同的備份」這句話拆穿了 settings 共用的假象。「為什麼要等 scan 完才看到類別」這句話暴露了 `list_groups` 的 cache-only 邏輯錯誤。代理寫了幾千行沒察覺的問題，使用者一句話就看穿——因為他們是從**使用**角度看，不是從**實作**角度看。

**最後一條**：災難發生時不要怕。認下來、追根因、重構到對。下次就沒有這條地雷了。

---

## 🔍 番外篇 V：一個「空 phrase strip」追出三層兇手、一個 Service Worker、和使用者耐心耗盡的 UX 拉鋸（2026-04-19 晚間）

番外篇 IV 剛打完第一個回合，使用者的 8801 Beta 又傳來新 bug：打開 player 頁面，**A-B popup 裡的 phrase strip 空蕩蕩**。明明 8800 Personal 看得到一排 Intro / Verse 1 / Chorus 1 pills，8801 卻是什麼都沒有，只剩「手動設定」一張 pill 孤單地躺在那。代理以為三分鐘能修，結果挖到第三層才找到真兇。

### 🪃 第一層：「User-specific data dir 斷了 fallback」

Explore agent 花三分鐘追出來：`/api/ai/sections` endpoint 會把 `data_dir=DATA_DIR/users/<username>/` 塞給 `detect_sections()`。Beta 使用者的個人目錄裡既沒 `human_sections/` 也沒 `hybrid_melody/` cache → `_extract_midi_features()` 傳出空的 list → BiLSTM 拿到全 0 features → 預測全部退化成單一 "verse"。

修法簡單：user-specific dir 若沒這首歌的標註檔，就 fallback 到 root `DATA_DIR`。beta 使用者從此能拿到 personal 偵測過的歌結果。

代理以為大功告成、NUC restart、使用者再試——

**「還是沒有。」**

### 🔬 第二層：「82% 的歌根本沒 hybrid MIDI」

掃 V:\data/：`chord_index.json` 有 43k+ 首歌的 hash，但 `hybrid_melody/` 裡只有 **7,926** 個檔。**82% 的歌從沒跑過 hybrid extraction**。

所以即使 data_dir fallback 到 root，`actual_data_dir / "hybrid_melody" / <hash>.json` 仍然不存在，features 依然全 0。BiLSTM 被餵垃圾、吐垃圾。

代理打開 `section_detect.py` 的 `_classify_dl()`，加了一行：如果 `sum(melody_density + bass_density) == 0`，直接 `return False` 觸發 rule-based fallback。沒 MIDI 至少還能靠 chord progression 切粗段落（Intro / Verse / Chorus）。

再 restart、再試——

**「還是沒有。」**

### 💀 第三層：「前端根本沒打那個 endpoint」

代理的疑心病終於發作。這時候才 grep 前端怎麼 call `_loadSections`：

```
/api/ai/sections?path=...  — DB-path mode 有
```

然後盯著 hash-mode 那一大塊分支（`player.js:4501+`），預期看到 `_loadSections(hashMode)` 或 `_loadSections(chordData.path)`——

**沒有。完全沒有。**

Beta 使用者開 `?hash=077e...` 這條路徑從頭到尾**沒人 fetch sections**。前面兩層 fix 全白費，因為請求從來沒送出去。`sectionData` 一直是 null、phrase strip 一直是空。使用者講「沒有」講到第三次，代理才意識到自己一直在修一條沒人走的路。

修法：
1. 後端 `/api/ai/sections` 加 `hash` 參數（與 `path` 二擇一），hash mode 跳過 `song_hash(path)` 直接用傳進來的 hash
2. 前端 `_loadSections` 偵測 `hashMode` → 組 `?hash=...` URL
3. Hash-mode 分支補上 `_loadSections(chordData.path || "")` 呼叫

三層 fix 套下去、NUC restart、使用者硬重整——

**「ok 1、ok 3。」**（意思：1 和 3 好了）

前兩層 fix 也不白做——`_classify_dl` 的零 feature 短路以後成為 82% 歌的 section 偵測主要依靠；user-specific dir fallback 讓使用者自己的標註能 override shared 結果。

### 🎨 同場加映：A-B popup UX 的 N 輪 iteration

修 bug 的同一場對話裡，使用者還在迭代 A-B popup 的 UX。代理一路從 dropdown → `‹ select ›` 箭頭 → toggle multi-select pill strip。每換一版都被使用者用一句話戳穿：

> 「< > should scroll to active chord」
> 「Chord card show "Chorus 1", but A-B show "Chorus"? inconsist」
> 「what happen if user set a-b to one of phrase then change the phrases」
> 「remove manual set button / move manual set to the last of drop box」
> 「move recycle bin to the horizontal middle of popup」
> 「red color icon inconsist」
> 「click bug report, click any button has popup, show two popups」
> 「can not scroll phrases in 8801 mobile vertical mode」

每一輪代理做完、使用者反饋、再做。這不是需求不清晰——是真實 UX 就是這樣淬鍊出來的。每一次微調都讓下一版更貼近使用者心智模型：
- Phrase label 要用 `Verse 1`、不是 `Verse`（跟 chord card 一致）
- Persist 要存 label 不是 index（section edit 也能 rebind）
- Popup 跟 bug modal 要**互斥**（只能一個浮層）
- Mobile portrait 的 flex column 才能讓 strip scroll（桌機 row 不夠空間）
- Trash icon 要中性灰不是紅（跟其他 toolbar icon 一致）

### 📱 順便：PWA 安裝 App — 「為什麼移除 app 後不會再跳安裝提示？」

使用者手機之前第一次進 livechord.org 會跳 PWA 安裝提示，最近他移除 app 後再進站，沒有再跳。代理第一反應「Chrome 擋住 repeat prompt 的正常行為」——但再一查 Chromium 安裝條件：

> HTTPS ✓ · manifest ✓ · **必須有 service worker 且 handle fetch event** ✗

LiveChord 根本沒 service worker。使用者第一次看到的安裝提示大概是 Chrome 在 SW requirement 放寬之前抓到的殘餘行為，Chrome 政策收緊後就再也不觸發。

修法：
1. 加極簡 `frontend/sw.js`（只做 fetch pass-through，不做 cache），滿足 installability
2. 後端 `/sw.js` route 送 `Service-Worker-Allowed: /` header
3. 首頁 header 加「📱 安裝」按鈕，捕捉 `beforeinstallprompt` → 存事件、按鈕顯現；點擊 → 呼叫 `prompt()`
4. iOS Safari 沒 `beforeinstallprompt`，fallback 顯示「分享 → 加到主畫面」提示

PWA 安裝從此變成**持久可用**的操作，不靠瀏覽器心情。

### 🧠 這次的三條

1. **在找 bug 之前先確認執行路徑真的會跑到**。代理修了兩層後端才發現前端沒送請求——如果第一步就 grep「哪裡 call `_loadSections`」，30 秒就能找到 hash-mode 漏了。診斷順序應該是「從使用者觀察到的現象**反向**沿 call stack 往回追」，不是「我猜是哪個模組有問題就去改哪裡」。
2. **PWA 安裝不是 nice-to-have，是留不住使用者的關鍵**。使用者移除 app 後找不到回頭路就是真實流失。Service worker 30 行程式碼換 installability，CP 值極高。
3. **UX 的 N 輪 iteration 是正常的，不是使用者難搞**。每輪微調都在把心智模型拉近，代理要做的是**快速 ship 每一輪、不辯論**。每一個「這不對」背後都是真實使用場景代理沒想到的。使用者不是 QA，他是第一個真用戶。

這次使用者也親口說了一句話值得記下：

> 「有時就是得破壞才有新生啊」

番外篇 IV 的災難催生了 dual-instance 架構、番外篇 V 的「空 strip」催生了 hash-mode parity + SW + PWA install button。如果一切都順利，LiveChord 會停在「剛剛好能 work」的狀態，走不到現在這個成熟度。

---

## 🎯 番外篇 VI：從「個別點數不一致」一路追到「chord array 被撕裂」（2026-04-21）

這篇是一個完整的長日：從「editor 每顆和弦下面的 0.1 拍數字不好看」這個微小的 UX 抱怨開始，一路追蹤到 BTC 偵測的系統性節拍誤差、寫 librosa migration 補齊 4500+ 首老歌、做完整的右鍵 chord-ops 選單、蓋了一套 tap-based chord calibration、然後在最後發現自己前面蓋的 segmented calibrate 會把 chord array 撕裂成兩半——並用三層防線縫合回去。

### 🪜 第一步：點數從看不見到看得到、再到兩邊看不一樣

使用者說「editor 每個和弦下面會顯示數字拍數，可以再和弦下面加上像 player chord card 下面點點的節拍」。一個很直接的視覺補強——把 player ribbon 已經有的 `.beat-dot` 樣式 port 到 editor 的 `.chord-block` 裡面。

10 分鐘搞定、部署、使用者回來：

> 「beat of first 4 chords, player: 4 3 3 4, editor: 3 4 3 4, inconsist」

同一首歌、同一個時間點、同一顆和弦，player 顯示 4 點、editor 顯示 3 點。這才發現兩邊的 **durSec 計算方式根本不一樣**：

- editor（`editor.js render()`）：`durSec = chord.end - chord.time` — 用顯式 `end`
- player（`player.js _buildUnifiedRibbon`）：`durSec = chords[i+1].time - chord.time` — 用下一顆起點

當 BTC 偵測的 `chord.end` 和下一顆 `chord.time` 有 ±0.2s 的 gap/overlap（BTC 的邊界不確定）、加上 BPM 60 讓 0.5 beat 的誤差足以翻轉 rounding，就會一顆顯示 3、一顆顯示 4。

**修法**：player 也改用 `chord.end`，fallback 才用 `chords[i+1].time`。兩邊從此吃同一份 truth。

### 🎛️ 第二步：player 根本沒讀存下來的 BPM

追前一步的時候順便讀了 `_buildUnifiedRibbon` 的 BPM 來源——**居然是從 median inter-chord interval 啟發式猜的**，完全忽略 chord JSON 裡的 `bpm` 欄位。後果：使用者在 editor tap-tempo 存 BPM=70、進 player 發現 BPM 跑掉、editor 切分一顆和弦、進 player BPM 又不一樣——因為每次 player render 都重跑啟發式，chord 切分會改變 median。

**Phase C**：讓 player 優先讀 `chordData.bpm`（>0 時），啟發式只當 legacy fallback。

使用者測完一句話確認：「切割和弦會讓 BPM 跑掉——bpm 有 persist 或存在歌曲資料?」問題即答案。編輯器的 save 路徑早就在寫 `bpm`，只是 player 從來沒吃。

### ✂️ 第三步：右鍵 ribbon 變「全家桶」

一旦點數和 BPM 都對齊了，使用者開始把所有 chord 修改需求都集中到 player ribbon 的右鍵選單：
- 🥁 **調整拍數**（2/3/4/5/6/8/12/16 快速選）→ 呼叫 `ChordCorrection.setBeats(chordIdx, newBeats)`，cascade 後續和弦平移保持間隔
- 🎯 **由此和弦開始校正**（進入 chord calibrate 面板）
- 🔗 **合併至前一個** / **合併下一個**（修 BTC 把一顆 8 拍切成「1 拍 Bb + 7 拍 Bbsus2」的常見錯誤）
- 🎹 **更換和弦**（`prompt()` 直接改名）

每個操作都走 `ChordCorrection.backup()` 做 undo snapshot，操作完 `_corrRebuild()` 重繪 ribbon。

**小地雷**：「調整拍數」這一項原本 inline 寫了 `style.color = "#2196F3"`，hover 時背景也是 `--accent` 藍，結果 **inline style 壓過 `:hover` 的白字規則，整列文字隱形**。修法：拔掉 inline color，讓 hover 的 CSS 規則接手。又一條「別在有 :hover 的 class 上用 inline color」。

**更大的地雷**：boundary chord 走 fast-path。`showSectionMenu` 對 section 第一個和弦（`isBoundary`）會跳過 `renderMain`，直接顯示段落類型列表+刪除鈕——把我新加的 5 個 chord ops 全吃掉。修法：砍掉 fast-path 一律走 `renderMain`。使用者換一次 section name 多一次 click，但每個和弦都能做切分/調拍/校正。

### 🥁 第四步：和弦與節拍校正（tap-based calibration）

這是這天最大的一塊。使用者發現即便改了點數 bug，很多老歌的 BTC 節拍還是有 ±0.3s 的系統性偏移，手動一顆一顆調很累。**需要一個「跟著歌打拍、系統吸齊」的工具**。

現有的 `ChordCorrection.enterBeatTap` 只吸 `chord.time` 到拍格，不改拍數、不重寫起點。使用者要的是更激烈的：

> 「使用者可以輸入來 `1234 1234 1234 12 12 1234` 校正和弦拍數與起始點，使用者輸入 1 就是那個和弦的開始」

於是做了 `enterChordCalibrate`：
1. 鍵盤 `0` = 換和弦第 1 拍、`.` = 同和弦下一拍（0/.  比 1/2/3/4 更直覺，右手不離小鍵盤）
2. 面板上的 `<kbd>` 可點擊 inline rebind（自訂按鍵、存 localStorage）
3. 兩個大觸控按鈕「新和弦」/「下一拍」給手機用
4. Apply：
   - `secPerBeat` = 所有 tap 間隔的 filtered median
   - **Lag compensation**：從 group 2+ 算 residual，median 當使用者 reaction lag，全部 tap 往前平移——第一組「起手反應慢」由後面校正回去
   - 每個 group 的第一拍時間寫入對應 chord 的 `time`，`end` 接下一 group 開始
   - 把 `secPerBeat` 換算回 BPM 寫進 `chordData.bpm`——不然 player 讀舊 BPM，改完拍還是顯示錯的點數

**支援 segmented**：右鍵任意 chord → 只校正那顆開始的區段，chord[0..N-1] 保持原貌。使用者自述「一段一段來，打錯比較不會爆炸」。

還加了 auto-save（debounced 800ms，模仿 section/phrase 編輯的 UX），把躲在工具 popup 裡的「儲存和弦」按鈕淘汰掉。

### 💥 第五步：segmented calibrate 把 chord array 撕成兩半

ship 完一切使用者回來：

> 「調整完節拍，進 editor, 2:39 左右邊的和弦重疊一起」

打開磁碟上的 admin chord JSON 看——**crime scene**：

```
[  0] time=118.160  chord=F
[ 20] time=183.300  chord=Bbm7
[ 21] time= 66.670  chord=F        ← 時間倒退！
[ 56] time=152.220  chord=F
[ 57] time=155.740  chord=Dm7
```

123 顆 chord 的陣列裡，索引 0-20 佔 118-183s、索引 21-56 佔 66-155s、索引 57+ 繼續往前——**array 被撕成兩段、後段被塞回前段中間**。editor render 以為陣列是時間有序的、就把兩組錯開的 chord 疊在同一塊螢幕空間上，做成使用者看到的「C Bb 重疊」。

使用者親口點破：

> 「那就是改完儲存的程式有 bug，剛剛我是按照樂句為單位一段一段校正的」

對。segmented calibrate 的 Apply 裡只有 **segment-local sort**：

```js
const seg = chords.slice(offset, offset + N).sort((a, b) => a.time - b.time);
chords.splice(offset, N, ...seg);
```

但當使用者右鍵副歌第一個 chord、此時音訊還停在主歌某處（currentTime=80s）、開始打 tap，chord[offset].time 就被寫成 80s——跟陣列前面原本 time=155s 的鄰居對不上。segment 內部順序 OK，**段與段之間的全域順序爆了**。

### 🛡️ 三層防線

1. **根因修復**：`enterChordCalibrate` apply 尾段改成 **global sort + dedupe**（相鄰 <0.2s 吃掉較早 index 的）+ 重算 `end`。陣列永遠 time-ordered，不管使用者在哪個音訊位置右鍵、連續做幾次 segmented。

2. **前端 save 保險**：`_autoSaveCorrection` 呼叫 `_cleanChordsForSave()`，POST 前再做一次 sort + dedupe + end realign。即便 apply 路徑還有漏網 bug，落到磁碟前再攔截一次。

3. **後端 save 保險**：`chord_api.save_chords` 的 POST handler 也跑同一套排序+dedupe。前端送什麼亂序資料進來都擋。

### 🩹 資料復原

使用者那首「過完冬季」的 admin JSON 已經壞掉，裡面混了兩批 chord。寫一個 `_repair_chords.py` one-off：
- 全陣列 time-sort、tie-break 保留較晚 index
- 0.3s 內相鄰視為重複、drop 其一
- `.bak` 備份原始、atomic `.tmp + os.replace` 寫回

跑完 123→113 顆、時間範圍從 `[撕裂混亂]` 變回 `[66.67s → 322.58s]`——但發現前 66 秒 intro 在更早的 buggy save 裡就被吃掉了。既然 admin 版已殘、乾脆 rename 成 `.moved`、讓 player fallback 回 official 版（103 顆、完整從 1.48s 起）——使用者一個硬重載就看到乾淨的 intro。

### 🎵 順手把源頭的節拍偵測也補了

一路追到這裡，使用者的一句話切中根本：

> 「就是節拍偵測有誤差，造成拍數顯示誤差，可從源頭修正？」

可以。BTC pipeline（`chord_detect.detect_chords_and_key_isolated`）只輸出 chord 序列 + key，**從來沒做 beat tracking 也沒寫 bpm**。chord 時間是 BTC 每幀輸出的結果、落在哪算哪。

新建 `backend/beat_snap.py`：
- `librosa.load(audio, sr=22050)` + `librosa.beat.beat_track` 抓出 BPM + 拍點陣列
- 把每顆 chord 的 `time`/`end` 吸到最近拍點（tolerance 0.25s——避免拉壞 syncopated 邊界）
- BPM 超出 40–240 範圍就放棄（halftime/doubletime 誤判不能相信）
- 吸完 realign `chord[i].end = chord[i+1].time`

兩個整合點：
1. **新歌**：`auto_worker._auto_detect_loop` 在 BTC detect 後呼叫 `analyze_and_snap`，bpm 寫進 JSON。之後所有新分析的歌都自動帶節拍資訊。
2. **舊歌**：寫 `backend/migrate_add_beat_info.py` 巡 `data/chords/*.json`、跳過已有 bpm 的、吸拍、atomic 寫回。支援 `--workers N` 平行、`--limit`、`--dry-run`。

NUC 實跑：78062 首、8 workers、~2.2 songs/s（NAS I/O bound）、ETA ~10 小時。

使用者發現斷線 resume 機制：*不需要記檔名*，直接再跑同指令，`SKIP_HAS_BPM` 自動快轉過已完成的檔。比 `--start-from` 直覺太多。

### 🎓 這次的五條

1. **兩個 view 顯示同一個數字、算法卻不同 = 一定會對不起來**。editor 用 `chord.end`、player 用 `next.time`，在沒有誤差時看不出差別、有誤差時瞬間翻臉。同一類資料只能有一個 source of truth 的算法。

2. **啟發式估 vs 存下來 = 必選存下來**。player 的 BPM 啟發式在當年（chord JSON 沒 bpm 欄位）是合理 fallback，但「存了 bpm 卻不讀」就是技術債。Phase C 這個改動 7 行，但解開了使用者三天累積的困惑。

3. **segment-local 的任何操作都要補 global 收尾**。segmented calibrate 的 segment-local sort 是好意：希望最小化影響範圍。但只要 segment 的輸出值可能和 segment 外的鄰居衝突，就必須 global resort/dedupe。防線要蓋在「最後一站」而不是「操作當下」。

4. **defense-in-depth 值得做三層**。前端 apply 修、前端 save 再修、後端 save 再修一次。每層都只多 15 行程式碼，但其中任何一層掛了其他兩層還能擋。使用者不需要知道哪層在擋、只需要「存進去的永遠是排序好的」。

5. **源頭修正比工具修正更省事、但不是互斥**。`beat_snap` 補了 BTC 的節拍盲區、大部分歌從此不需要手動校正；但手動的 `enterChordCalibrate` 還是要存在——有的歌 beat_track 會抓錯、有的歌使用者就是想把 syncopated 邊界對齊整拍。工具箱裡兩種都有、讓使用者挑。

這天開始時 editor 只是缺幾顆點，結束時整個 chord 編輯流程從 BTC 到 save 都被縫過一遍。破壞才有新生——第二次驗證這件事。

### 📊 Migration 終場數字（NUC 8 workers，跑完 10.46 小時）

```
Total:               78062
OK:                  74336  (95.2%)  ← 成功補上 bpm + 拍格吸合
SKIP_AUDIO_MISSING:   2596  (3.3%)   ← chord JSON 在、音檔已失聯
SKIP_HAS_BPM:          592           ← 續跑的 fast-forward (SKIP 路徑 0.025s/首)
SKIP_NO_CHORDS:        496           ← chord 陣列空（BTC 失敗留下的殘檔）
FAIL_NO_BEATS:          42  (0.054%) ← librosa 抓不到拍 (極端 tempo / 純人聲 / 環境音)
Elapsed: 37664s
```

95.2% 的 library 從此「chord JSON 自帶 bpm」——player 讀 `chordData.bpm` 的 Phase C 改動對這批歌全部生效，下次打開任何一首老歌的 player ribbon，dot 數都會是乾乾淨淨的整數。

SKIP_AUDIO_MISSING 的 2596 首是另一個題目的開頭——chord JSON 指向的路徑已經失聯（音檔被改名/移動/刪除）。可以寫個 cleanup script 列出這些 orphan JSON 交叉比對音樂庫、由 user 決定重建路徑或刪除。不急，等下次整理 NAS 時再處理。

### 🧵 這天的 commit 序列（feature/beta-productization）

```
12f7b85  feat: chord calibration + beat-dot alignment + source-level beat_snap
2eba27a  fix: auto-split panel hidden behind waterfall canvas (z-index bump)
492f2dd  fix: auto-split threshold 大於等於 → 大於 + doc z-index tiers (UX §11)
60d9eaa  feat: 3-state ribbon/waterfall toggle  (first-cut, later revised)
2eac11a  fix: ribbon state 2 now actually takes full width (grid reflow)
1d92145  fix: independent ribbon/waterfall collapse toggles (replace broken cycle)
c964f07  fix: ribbon-wide shares scale preference with overview-mode
2621f1e  fix: ribbon toggle — no auto-swap, restore goes back to previous split
e443511  fix: ribbon toggles vertically centered + side-by-side when both visible
```

9 個 commit、一個 UX 元件（ribbon 分隔線的兩顆 toggle）被**迭代了 5 次**才定型：cycle → bad → independent + auto-swap → auto-swap bad → pure toggles + hide conflicting button → position 30/70 → position side-by-side centered。每一輪使用者都當場試 + 指出問題，代理快速改 → 再試。這是標準的「使用者當 UX designer、代理當雙手」的協作節奏——論文裡叫 tight feedback loop，實務上就是一句「好了，推吧」。

---

## 🎻 番外篇 VII：Live 版 Rubato 不再滑拍——從一句使用者觀察到 4500 首歌都能跟著呼吸（2026-04-22）

番外篇 VI 終於修好了 chord array 撕裂的最後一道地雷。理論上「這天的 chord 都對了」。但隔天使用者來了一句：

> 舊的曲目，演唱會版本的 BPM 不是絕對均一的，不同樂段會有不同的偏差，較佳的偵測節拍的方式是…

接下來是一篇精煉的技術 brief，列了四個方案（動態節拍、CNN/RNN onset、Audio-to-MIDI 觸發、貝葉斯追蹤）和三個實作建議（放棄全局 BPM、PLL 鎖相環、多特徵融合）。意思很清楚：**現在的靜態 BPM 對 Live 版和老錄音「中段對得上、副歌漂掉、Bridge 又對回來」的滑拍體驗，是時候動架構而不是調參數了**。

代理花了一個下午把這套完整實作起來。

### 🔍 起手式：Spike 10 首歌證明「值得做」

熟悉的代理本能說「先做 spike」。從 Y:\ 挑 **10 首**覆蓋 4 個情境：
- 2 首現代量化（ABBA Money、Bee Gees Stayin' Alive）— baseline，新引擎不能讓這類退步
- 3 首演唱會 Live（Bocelli Besame Mucho 2006、Bee Gees Massachusetts 1989、Eagles Desperado 1976）
- 3 首老抒情（All by Myself、Air Supply Here I Am、Bee Gees How Deep Is Your Love）
- 2 首 Solo Piano（Mariage d'Amour、Lettre à Ma Mère）

裝 madmom 是第一個雷。`pip install madmom` 在 Python 3.11 直接掛掉——`Cython` 沒在 pyproject.toml 宣告，build isolation 找不到。再來 wheel 裝起來但 import 又掛掉——`from collections import MutableSequence` 在 3.10 就被搬到 `collections.abc`。最後鎖在 git master（0.17.dev0）+ `--no-build-isolation`，第一個雷拆完。

10 首跑完約 6 分鐘（madmom 每首 ~30 秒）。結果一目了然：

| 類別 | 歌 | librosa BPM | madmom 中位數 | tempo range | 解讀 |
|---|---|---|---|---|---|
| modern | Stayin' Alive | 103.36 | 103.45 | **1.19** | 兩引擎完全一致 ✓ baseline 不退步 |
| modern | ABBA Money | 123.05 | 120.81 | 18.09 | 接近 |
| live | Bocelli Live | 95.7 | 94.74 | **38.21** | 顯著 rubato——librosa 完全錯過 |
| live | Massachusetts Live | 103.36 | 101.69 | 17.77 | 偵測到漂移 |
| live | Desperado Live | 117.45 | **58.63** | 19.07 | half-time 分歧（madmom 音樂上正確） |
| old | Air Supply | 143.55 | 68.97 | 10.36 | half-time 分歧 |
| solo | Mariage d'Amour | 161.5 | 163.64 | 44.51 | Clayderman 自由速度 |
| solo | **Lettre à Ma Mère** | 136.0 | 131.39 | **77.24** | **極端 rubato（63 ↔ 140 BPM）** |

兩條結論：
1. **量化曲不退步**——Stayin' Alive 的 madmom range 1.19 BPM 等於 0，drum machine 兩個引擎完全同意
2. **Live / Solo Piano 是大勝**——Bocelli range 38、Lettre range 77，librosa 給單一數字完全錯過了 rubato 結構

視覺證據加碼：Bocelli 的 PNG 底部 BPM 曲線從 70 緩升到 110、結尾加速到極限——librosa 那條水平虛線 95.7 直接穿過中間錯過所有結構。Decision gate **PASS**。

### 🏗️ 四個 Phase 一氣呵成

代理開了 plan mode 寫了完整方案，使用者三題答完（madmom、先 Y:\ 小批次、後端+前端 PLL），全部 ExitPlanMode 一次過。然後是流水線式的實作：

#### Phase 1：後端動態引擎

[backend/beat_snap.py](../backend/beat_snap.py) 加 `analyze_and_snap_dynamic`，做的事：
1. madmom RNN+DBN 抓 beats[]
2. RNNDownBeatProcessor + DBN（3/4 + 4/4 雙假設）抓 downbeats
3. 滑動窗口 `60 × 3 / (b[i+3] - b[i])` 算 tempo_curve
4. 0.25s tolerance 把 chord 邊界 snap 到動態 beat grid（不是等距）

chord JSON 多 5 個欄位（`beats / downbeats / tempo_curve / beats_source / beat_version`），向下相容（沒 madmom 就 `beats_source: "librosa-fallback"`）。[backend/auto_worker.py](../backend/auto_worker.py) 和 [backend/process_queue.py](../backend/process_queue.py)（beta 上傳路徑）都換成 dynamic 版本。

#### Phase 2：AI 伴奏連動——這是隱藏的最大成本

使用者答 spike 結果時提了一句**「節拍改變，同時會影響 AI 伴奏」**。代理 audit 整個 `backend/ai/` 後發現只有兩個地方靠 scalar bpm 排事件：
- [accompaniment_generator.py `_build_rh_1plus3`](../backend/ai/accompaniment_generator.py)：1+3 voicing 用 `60.0 / bpm` 算每拍時長
- [dynamics_engine.py `humanize`](../backend/ai/dynamics_engine.py)：時間微抖動的 beat-mod 也要 beat_dur

新增 [backend/ai/beat_helpers.py](../backend/ai/beat_helpers.py) 共用的 `local_bpm_at(tempo_curve, t)`，兩處都 thread tempo_curve 過去。`generate_accompaniment` 加 `tempo_curve=` 參數，[batch_accompaniment_worker.py](../backend/batch_accompaniment_worker.py) 和 [ai_api.py](../backend/ai_api.py) 兩條 caller 都改讀並透傳。

伴奏 JSON 加 `source_beat_version` 欄位——配對 chord JSON 的 `beat_version`，前端可以偵測「這 acc 是用舊 beats 算的」。

回歸測試：`tempo_curve` 缺 / `None` / `[]` 三情境產生**完全相同**的 acc 事件。舊歌零行為改變，0 風險。

#### Phase 3：前端 PLL（其實 PLL 用不太到）

新檔 [frontend/js/beat-sync.js](../frontend/js/beat-sync.js) 暴露 `window.BeatSync.{localBpmAt, beatDurationAt, nearestBeatIndex, tempoRange, createPLL}`。PLL 完整寫好，rate-cap 20ms/sec、>200ms 視為 seek 直接 re-lock。

但實際 audit player.js 後發現**waterfall 已是純時間驅動**（chord/acc events 帶絕對時間，scroll 不依賴 BPM），PLL 不需介入 scroll velocity。原計畫高估了 PLL 的角色。BPM 真正影響的是 beat dot 燈光——所以代理只改了 [`_updateBeatDots`](../frontend/js/player.js)，加 `_secPerBeatAt(t)` 用 tempo_curve 查局部 BPM。

加 stale-acc toast：載入 acc 時若 `chordData.beat_version > acc.source_beat_version`，跳「節拍已升級，伴奏使用舊版本（背景重生中）」。[chord-correction.js](../frontend/js/chord-correction.js) 校正提交後 bump beat_version，讓 toast 機制感知到「使用者剛剛動過 chord」。

#### Phase 4：Migration + 端到端 QA

寫 [backend/migrate_add_dynamic_beats.py](../backend/migrate_add_dynamic_beats.py)（仿 `migrate_add_beat_info.py` 模板，加 `--only` filter / `--regen-acc` 旗標 / 智慧 beat_version bump）。

挑 **Lettre à Ma Mère**（極端 rubato 樣本）做單曲 migration：

```
[1/1] c2203eefeac9.json: OK src=madmom bpm=131.39 snap=29 range=77.2  Elapsed: 26.3s
```

開 PC localhost 8802 用 Playwright MCP 端到端驗證：

| 驗證點 | 結果 |
|---|---|
| `BeatSync` 全域載入 | ✅ 5 個 method 全在 |
| chord JSON 12 欄位完整 | ✅ `beats / downbeats / tempo_curve / beats_source / beat_version` 全到位 |
| BPM 顯示 131 | ✅ = migrated 131.4 四捨五入 |
| 33 chord cards / 303 beat dots | ✅ 渲染正常 |
| **局部 BPM 動態** | ✅ t=0 → 0.91s/beat（66 BPM 慢板），t=90 → 0.44s/beat（135 BPM 快段）— **2 倍速差** |
| `BeatSync.tempoRange` rubato 偵測 | ✅ `range: 77.24, isRubato: true` |
| **Stale-acc toast 觸發** | ✅ DOM 內已含「節拍已升級，伴奏使用舊版本（背景重生中）」 |
| Console clean | ✅ 0 個 Phase 1-3 引入錯誤 |

截圖：[qa-phase1to3-lettre-rubato-loaded.png](../qa-phase1to3-lettre-rubato-loaded.png) — UI 完整、modal「選擇正確的音源」正常彈出（hash mode 缺音檔的預期行為）。

### 🎓 這次的五條

1. **使用者的「技術 brief」要當作 spec 一字一句讀**。使用者列了 4 個方案 + 3 個實作建議，代理的工作是從中挑出**最值得實作的組合**——不是全做，也不是亂選。最後選了方案 1（動態節拍 madmom）+ 方案 4（貝葉斯 = madmom 內建 DBN）+ 實作建議 2（PLL）的綜合體，因為它對 LiveChord 既有架構嵌入點最自然。

2. **Spike 永遠值得**。10 首歌、6 分鐘、一支 PNG。Quantized baseline 過了不退步、Live 大勝、半倍速確認 madmom 偏好慢板（音樂上正確、`bpmMult` 已能覆蓋）。沒有 spike 就無法回答 decision gate「值不值得 madmom 這顆 30s/song 的成本」。

3. **隱藏的相依要在計畫階段問出來**。使用者答 spike 結果時的「節拍改變，同時會影響 AI 伴奏」這句話，是整個 Phase 2 audit 的起點。如果代理沒問就直接開幹，會在 Phase 3 才發現「啊伴奏為什麼還是滑」——回頭重做 Phase 2 加 source_beat_version 多花一倍工。

4. **Audit 結果常常比想像得小**。這次 ai 模組 grep `bpm` 出來 6 個 site，audit 完發現只有 2 個是真的會影響 rubato 體驗的（_build_rh_1plus3 + humanize），另外 4 個是裝飾性 / 評估性、容錯不錯，可以留給後續迭代。**「實作什麼」往往不如「不實作什麼」重要**——尤其在 beta active 的時候。

5. **計畫文件不要怕被現實打臉**。原計畫寫 PLL 要鎖 waterfall scroll velocity；實作時讀 player.js 才發現 waterfall 早就是時間驅動的、PLL 用不到。代理改寫 Phase 3 縮小範圍——只接 beat dot 燈光，加 stale toast、bump beat_version。**3 個檔代替原計畫的 6 個檔**，效果一樣、風險小一半。

### 📊 不是 migration 終場——這次只跑 1 首

跟番外篇 VI 的 78062 首大批次不同，這次刻意只跑 Lettre 一首做端到端驗證。原因：beta 8801 上有真實使用者，全量 migration 會引發大量 stale toast，且 acc cache 重生 ~15GB 不該在沒充分 QA 前就觸發。

**部署計畫由使用者三選一決定**：
- A) 在 PC 8802 QA（**已執行 ✓**）
- B) Sync 13 個檔到 V:\ + NUC 裝 madmom + 重啟雙實例
- C) 全量批次重跑（~10-15 hr）

寫完這篇番外篇時是 A 完成、B/C 等使用者下指令。NUC 端的部署 prompt 已 append 到 [doc/shadow_stress_test_prompt.md 任務 #2](shadow_stress_test_prompt.md)。

### 🧵 這天的 commit 序列

（等 push 後補上）

---

> **後記**：這天解的不是 bug，是**架構債**。LiveChord 從 day 1 就用 librosa.beat_track 抓全局 BPM——對 Pop / Studio 量化曲非常夠用，但對使用者收藏的 Live 版 / 老抒情 / Solo Piano（這些是學鋼琴的人最想練的曲目），這個「全局單一 BPM」假設一直是隱性 UX 缺陷。今天用 madmom 動態追蹤 + tempo_curve 持久化把它解開。下一個對應的架構債候選：accompaniment 的 source-stem 預分離（番外篇 VI 提過的 demucs vocal stem 路線），等 Phase 4 跑完看效果再決定。


