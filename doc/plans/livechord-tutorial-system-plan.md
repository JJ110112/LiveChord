# LiveChord 使用教學系統規劃書

> Issue: LiveChord-r7k  
> 範圍：首頁與 player 的互動式使用教學規劃。本文是設計與實作規格，不直接修改產品行為。  
> 主要參考：[doc/UX_CONVENTION.md](../UX_CONVENTION.md)、[frontend/index.html](../../frontend/index.html)、[frontend/player.html](../../frontend/player.html)

## 1. 目標

LiveChord 的第一次使用門檻主要有兩段：使用者要先知道如何開始一首歌，接著要理解 player 內左右兩大區的閱讀方式。本教學系統的目標是用短步驟、可跳過、可重播的互動式 coach mark，讓使用者在實際介面上完成理解。

核心目標：

| 目標 | 說明 |
|---|---|
| 建立第一個動作 | 使用者知道 `+ Pick local audio` 可以選本機音檔進行分析。 |
| 建立低風險試用路徑 | 使用者知道可以點 demo song 直接進 player，不需要先上傳。 |
| 讀懂 player 主結構 | 使用者先理解左側和弦區，再理解右側樂器練習區。 |
| 避免打斷工作流 | 教學可略過、可關閉、可由齒輪重新開啟。 |
| 符合既有 UX convention | 使用既有 `.lc-panel` / `.tb-popup` surface、z-index tier、行動裝置 hit target 與 viewport clamp 規則。 |

非目標：

| 項目 | 說明 |
|---|---|
| 不取代 Help Center | `Help` 仍放完整文章；教學只教首次操作與主要區域。 |
| 不強迫使用者上傳音檔 | 第一步只說明按鈕用途，不自動打開 file picker。 |
| 不把所有工具都教完 | 初版只覆蓋常用、重要、面積大的區域；進階工具留給後續分流教學。 |

## 2. 啟動時機

| 入口 | 觸發條件 | 行為 |
|---|---|---|
| 首次進入首頁 | `localStorage.livechord_tutorial_v1_done` 不存在，且首頁主要內容已 render | 自動從首頁 Step 1 開始。若 `#betaBrowseLocalBtn` 尚未可見，先等待 dashboard render；若仍不可見，改從 demo Step 開始。 |
| 首次進入 player | `livechord_tutorial_v1_done` 不存在，且 player 已載入 chord data 或 demo data | 自動從 player Step 3 開始。避免在 detect/loading overlay 顯示時啟動。 |
| 首頁齒輪 | 新增 `使用教學` menu item | 從首頁 Step 1 重新開始。 |
| Player 齒輪 | 新增 `使用教學` menu item | 從 player Step 3 重新開始；若使用者在 player 點擊，避免跳回首頁造成中斷。 |
| 首頁 Step 2 進入 demo | 點擊教學 popover 的「開啟範例並繼續」 | 導向 `/player?hash=9d19747b402b`，並以 `sessionStorage.livechord_tutorial_resume=player-core` 接續 Step 3。 |

狀態鍵：

| Key | Storage | 用途 |
|---|---|---|
| `livechord_tutorial_v1_done` | `localStorage` | 完成或略過後寫入 `true`，避免每次自動跳出。 |
| `livechord_tutorial_v1_dismissed_at` | `localStorage` | 記錄略過時間，供分析與日後版本策略使用。 |
| `livechord_tutorial_resume` | `sessionStorage` | 首頁導到 player 後接續步驟。 |
| `livechord_tutorial_force` | `sessionStorage` | 由齒輪手動重播時短暫設置，忽略 done 狀態。 |

版本策略：教學內容大改時增加版本，例如 `livechord_tutorial_v2_done`，不覆蓋 v1 記錄。

## 3. UX 原則

| 原則 | 設計規則 |
|---|---|
| 大到小 | 先導覽首頁入口與 player 左右主區，再導覽 toolbar 裡的細項。 |
| 常用到不常用 | 上傳、demo、和弦閱讀、樂器視圖優先；速度、loop、transpose、tools 後置。 |
| 重要到次重要 | 先回答「我要按哪裡開始？」與「player 怎麼看？」再介紹練習與進階控制。 |
| 可中斷 | 每一步都有 `略過`，最後一步是 `完成`；ESC 關閉並視為 dismissed。 |
| 不偷做動作 | 教學不自動打開 file picker、不自動播放音樂；唯一可自動導頁的是使用者明確按下「開啟範例並繼續」。 |
| 不遮擋目標 | Popover 不能蓋住被教學的按鈕或核心畫面；需要時自動翻到上方、下方、左側或右側。 |
| 行動版等價 | 390px portrait 使用 bottom coach panel；landscape 保留 anchored popover，但仍需 clamp 在 viewport 內。 |

## 4. 教學元件規格

教學 popover 使用既有 Type C floating panel 規則，以 `.lc-panel` 作為 surface，不新增第五種 popup surface。需要的新增 class 只負責定位與內容：

| 元件 | 建議 class / id | 規格 |
|---|---|---|
| Root | `#lcTutorialRoot` | 動態建立一次，關閉時 hidden，不重複 append。 |
| 高亮框 | `.lc-tutorial-spotlight` | `position: fixed`、只畫 border/ring、`pointer-events: none`。 |
| Popover | `.lc-tutorial-popover.lc-panel` | 使用 `.lc-panel` 的背景、blur、border、shadow；z-index 使用 tier 6 的 9560。 |
| 標題 | `.lc-title` | 一句話命名當前區域。 |
| 內文 | `.lc-subtitle` 或 `.lc-tutorial-body` | 短句，最多兩行；避免教科書式說明。 |
| 進度 | `.lc-tutorial-progress` | 顯示 `1/8` 這種進度，不用大型 stepper。 |
| 操作列 | `.lc-modal-actions` | 使用 `上一步`、`下一步`、`略過`、`完成`，按鈕沿用 `.tb-popup-btn`。 |

Z-index：

| Layer | 值 | 原因 |
|---|---:|---|
| Spotlight | 9550 | tier 6 floating widget，高於 toolbar 與 modal，但不攔截點擊。 |
| Popover | 9560 | 高於 spotlight。 |
| Optional mobile bottom panel | 9560 | 同 popover。 |

不使用 full-screen dim backdrop。原因是此教學是 coach mark，不是 modal decision；使用者應可看見整個 player 結構，且不應破壞既有 toolbar escape path。

## 5. 導覽流程

初版流程以「首頁 2 步 + player 6 步」為主。若使用者從 player 齒輪重播，直接從 Step 3 開始。

| Step | Page | Target | 目的 | Popover 文字草案 | Primary action |
|---:|---|---|---|---|---|
| 1 | Home | `#betaBrowseLocalBtn` | 說明本機音檔入口 | `從這裡選本機音檔。LiveChord 會分析音檔，產生和弦、節拍與練習視圖。` | 下一步 |
| 2 | Home | `.demo-card[data-hash="9d19747b402b"]` | 說明 demo song 入口 | `想先試用可以點這首 사랑의 빈도 (Love Frequency)。它已經分析完成，可以直接進入播放頁。` | 開啟範例並繼續 |
| 3 | Player | `#chordRibbonPanel` | 先理解左側和弦區 | `左側是和弦時間軸。這裡顯示目前與接下來的和弦名稱、組成音、tab 或數字音符，播放時會跟著歌曲前進。` | 下一步 |
| 4 | Player | `#instrumentPanel` 或當前 active instrument container | 理解右側樂器練習區 | `右側是目前樂器的練習視圖。鋼琴會看到瀑布流與鍵盤；吉他/烏克麗麗會分成左手按法與右手撥弦；手風琴與編曲鍵盤會顯示對應的左右手區域。` | 下一步 |
| 5 | Player | `#tbPlayback` | 說明播放控制 | `這裡控制播放、暫停與回到開頭。歌曲播放後，左側和弦與右側練習畫面會同步移動。` | 下一步 |
| 6 | Player | `#tbInstrument` | 說明切換樂器 | `用這裡切換鋼琴、吉他、烏克麗麗、手風琴或編曲鍵盤。切換後，右側練習區會換成對應視圖。` | 下一步 |
| 7 | Player | `#tbTeaching` | 說明練習模式 | `這裡是練習設定。你可以選左手、右手、雙手、指法、和弦音與 AI 伴奏，先從簡單模式開始。` | 下一步 |
| 8 | Player | `#tbSpeed`, `#tbLoop`, `#tbTranspose` group | 說明常用播放輔助 | `速度、循環與移調是最常用的練習輔助。先放慢、圈選一小段，再逐步回到原速。` | 完成 |

### Step 4 樂器文案分流

Step 4 的 target 應依目前 `livechord_tab` 或 active DOM 決定：

| Active tab | Target | 文案補充 |
|---|---|---|
| Piano | `#chordDisplayPiano` | `瀑布流顯示即將落下的音，底部 88 鍵顯示該按的位置。` |
| Guitar | `#chordDisplayGuitar` | `左側顯示目前和弦與按法，右側顯示撥弦或分解節奏。` |
| Ukulele | `#chordDisplayUkulele` | `左側是烏克麗麗按法，右側是節奏與撥弦提示。` |
| Accordion | `#chordDisplayAccordion` | `左側是 bass/chord buttons，右側是鍵盤瀑布流。` |
| Arranger | `#chordDisplayArranger` | `上方瀑布流搭配下方鍵盤，幫你看左右手分工。` |

若特定 tab container 尚未顯示或尺寸為 0，fallback 到 `#instrumentPanel`。

## 6. 首頁齒輪與 player 齒輪新增項

首頁與 player 的齒輪都已經是設定入口，新增 `使用教學` 應放在 Help 前面，因為它比 Help 更偏向即時操作。

| Page | 位置 | 新增元素 |
|---|---|---|
| Home | `.header-nav-menu` 的 Help section | `<button id="btnStartTutorialHome" class="hn-row" type="button" role="menuitem">使用教學</button>` |
| Player | `.tb-popup-gear` 的 Home/Help section | `<button id="btnStartTutorialPlayer" class="hn-row" type="button" role="menuitem">使用教學</button>` |

i18n keys：

| Key | zh-TW | en |
|---|---|---|
| `tutorial.menu` | 使用教學 | Tutorial |
| `tutorial.next` | 下一步 | Next |
| `tutorial.back` | 上一步 | Back |
| `tutorial.skip` | 略過 | Skip |
| `tutorial.done` | 完成 | Done |
| `tutorial.open_demo_continue` | 開啟範例並繼續 | Open demo and continue |

## 7. 實作架構

建議新增獨立模組，避免把導覽狀態塞進已經很大的 `app.js` / `player.js`。

| 檔案 | 職責 |
|---|---|
| `frontend/js/tutorial-tour.js` | 共用 tour engine、定位、狀態、鍵盤事件、step state machine。 |
| `frontend/css/tutorial.css` | spotlight、popover、mobile bottom layout、target ring。 |
| `frontend/js/app.js` | 首頁 bootstrap：內容 render 後呼叫 `LiveChordTutorial.maybeStart("home")`，並綁定首頁齒輪按鈕。 |
| `frontend/js/player.js` | Player bootstrap：chord/player ready 後呼叫 `LiveChordTutorial.maybeStart("player")`，並綁定 player 齒輪按鈕。 |
| `frontend/index.html` | 載入 `tutorial.css` 與 `tutorial-tour.js`，新增首頁齒輪 row。 |
| `frontend/player.html` | 載入 `tutorial.css` 與 `tutorial-tour.js`，新增 player 齒輪 row。 |
| `frontend/i18n/zh-TW.json`, `frontend/i18n/en.json` | 教學文案與按鈕文字。 |

共用 API 草案：

```js
window.LiveChordTutorial = {
  maybeStart(page),
  start(page, { force = false, fromStep = null } = {}),
  close({ completed = false, dismissed = false } = {}),
  refreshPosition(),
};
```

Step resolver 草案：

```js
{
  id: "home-upload",
  page: "home",
  target: () => document.querySelector("#betaBrowseLocalBtn"),
  placement: "right",
  beforeShow: async () => ensureVisible("#secBetaLocalTracks"),
  titleKey: "tutorial.home_upload.title",
  bodyKey: "tutorial.home_upload.body",
}
```

## 8. 定位與互動規則

| 情境 | 規則 |
|---|---|
| Target 不在 viewport | `element.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" })`，等待 scroll 結束或 350ms 後定位。 |
| Target 在水平 scroll 容器內 | 對最近的 `.horizontal-scroll` 先做 `scrollLeft` 對齊，讓 demo card 出現在中間。 |
| Popover 超出 viewport | 依序嘗試 preferred placement、反向 placement、側邊 placement，最後 clamp 到 `8px` safe margin。 |
| Target 消失 | 跳到下一個可用 step；若沒有可用 step，顯示完成。 |
| Window resize / orientation change | debounce 100ms 後重算 spotlight 與 popover。 |
| 使用者點擊 target | 不攔截；教學保持目前 step。若 target 造成導頁，透過 sessionStorage resume。 |
| ESC | 關閉並記錄 dismissed。 |
| Back | 回上一個可用 step；若跨頁，初版不自動回首頁，只在目前 page 的步驟內回退。 |

## 9. 可近用性

| 項目 | 規格 |
|---|---|
| Focus | 開啟 step 後 focus 到 popover 的 primary action。關閉後 focus 回啟動按鈕或 target。 |
| Role | Popover 使用 `role="dialog"` 與 `aria-labelledby`；因不使用 backdrop，不做 focus trap。 |
| Keyboard | `Enter` 觸發 primary action，`Escape` 略過，`Shift+Tab/Tab` 在 popover 按鈕間自然移動。 |
| Motion | 若 `prefers-reduced-motion: reduce`，scroll 使用 instant 或最短動畫，不做 ring pulse。 |
| Contrast | Spotlight ring 使用 theme-aware accent，light themes 使用較深色 outline，避免淡色主題看不到。 |
| Hit target | 行動裝置所有教學按鈕至少 44px 高。 |

## 10. Analytics

事件應只記錄行為，不記錄檔名或使用者音訊資訊。

| Event | Properties |
|---|---|
| `tutorial_start` | `{ page, source: "auto" | "gear", version: 1 }` |
| `tutorial_step_view` | `{ page, step_id, step_index, version: 1 }` |
| `tutorial_step_next` | `{ page, step_id, version: 1 }` |
| `tutorial_skip` | `{ page, step_id, source, version: 1 }` |
| `tutorial_complete` | `{ page, version: 1 }` |
| `tutorial_target_missing` | `{ page, step_id, version: 1 }` |

## 11. 邊界情境

| 情境 | 處理 |
|---|---|
| 使用者未登入且沒有 local upload 區塊 | 首頁 Step 1 延後；若仍缺 target，從 demo Step 開始，文案不提上傳權限細節。 |
| 首次進入 player 但沒有 chord data | 不自動啟動；等 chord data ready 或使用者由齒輪手動開啟。 |
| Loading/detect overlay 顯示中 | 延後啟動，避免 spotlight 指到被遮住的區域。 |
| Mobile portrait | Popover 變成 bottom panel，spotlight 仍框 target；內容最多兩行，操作列不換出 viewport。 |
| Mobile landscape | 採 anchored popover，但需測 `#chordRibbonPanel` 與 `#instrumentPanel` 不被遮住。 |
| 使用者正在開啟 toolbar popup | 開始教學前先關閉其他 `.tb-popup`，避免多個 floating surface 疊在一起。 |
| i18n 尚未 ready | 使用英文 fallback 或等待 `livechord:i18nready`。 |
| bfcache 返回首頁 | 若 `livechord_tutorial_resume` 已清除，不重啟；若 force replay 未完成，重新定位當前 step。 |

## 12. QA 規劃

本功能屬於 LiveChord public/prod UX，實作後需依專案記憶在 8805 public-mode 做本地 QA。

| 類型 | 驗證內容 |
|---|---|
| Desktop 1920x1080 | 首頁自動啟動；Step 1 對準 `+ Pick local audio`；Step 2 scroll 到 Love Frequency；player Step 3/4 不遮住主要畫面。 |
| Laptop 1280x720 | Popover 不水平溢出；toolbar steps 可見且不蓋住 target。 |
| Mobile portrait | 每個按鈕至少 44px；bottom panel 不蓋住 target 主要資訊；文字不溢出。 |
| Mobile landscape | 左右主區導覽仍可讀；toolbar 不被透明 overlay 攔截。 |
| Theme matrix | Dark、Light、Sakura、Sunny、Sky 都能看清 spotlight 與 popover。 |
| Keyboard | Tab、Enter、Escape 可操作；關閉後 focus 回合理位置。 |
| State | 完成後 reload 不再自動出現；齒輪可重播；skip 後 reload 不再自動出現。 |
| Missing target | 手動隱藏 local upload 或 demo section 時，不 crash，會跳到下一個可用 step。 |
| Pointer events | `document.elementsFromPoint()` 檢查 toolbar target 上方沒有 invisible overlay 攔截點擊。 |

建議 Playwright smoke cases：

| Case | 重點 |
|---|---|
| `tutorial-home-first-run.spec` | 清空 tutorial localStorage，進首頁，檢查 Step 1/2 定位與下一步。 |
| `tutorial-player-resume.spec` | 從首頁 Step 2 開啟 `love_frequency`，確認 player 自動接 Step 3。 |
| `tutorial-gear-replay.spec` | 設定 done=true 後，從齒輪按 `使用教學` 仍能重播。 |
| `tutorial-mobile.spec` | portrait 與 landscape 截圖比對，確保無 overflow。 |

## 13. 實作階段

| Phase | 內容 | 驗收 |
|---|---|---|
| Phase 1 | 建立 `tutorial-tour.js` / `tutorial.css`，完成首頁 Step 1/2 與 player Step 3/4。 | 第一次首頁可走到 player，player 左右主區導覽可完成。 |
| Phase 2 | 新增齒輪入口、localStorage/sessionStorage 狀態、skip/done/replay。 | 完成後不再自動出現；齒輪可重播。 |
| Phase 3 | 補齊 player Step 5-8、樂器文案分流、i18n keys。 | 鋼琴、吉他、手風琴、編曲鍵盤至少各 smoke 一次。 |
| Phase 4 | Playwright smoke、mobile portrait/landscape QA、theme matrix、8805 public-mode QA。 | 截圖無遮擋、無水平 overflow、無 pointer interception。 |

## 14. 開放決策

| 決策 | 建議 |
|---|---|
| 首頁未登入時是否教上傳 | 初版不特別解釋登入限制；若 upload target 不存在就跳到 demo，降低首次摩擦。 |
| Step 2 是否一定使用 Love Frequency | 建議固定使用 `hash=9d19747b402b`，比用 title 選取穩定，也避免 Unicode/語系造成 selector 問題。 |
| 是否加入遮罩 | 初版不加 full-screen backdrop，只用 target ring；這最符合現有 popup taxonomy，也最不干擾 player。 |
| 是否讓使用者直接點 target 前進 | 初版不做自動判定。使用者按 Next 才推進，避免 file picker、播放、toolbar popup 等副作用把 state machine 複雜化。 |

