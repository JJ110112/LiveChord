# Scale Lab — 鋼琴 + 吉他音階小工具（規劃 + 實作紀錄）

日期：2026-09-02　狀態：Phase 1 已實作（首頁區塊 + Player 調性彈窗）

## 目標

1. 首頁新增一個像 Progression Library 的區塊「Scale Lab」：
   使用者選 12 個調 × 音階類型（大調、小調、藍調、爵士、教會調式、異國音階…），
   在鋼琴鍵盤或吉他指板 / TAB 上顯示音階，附音色特性標籤與介紹文字。
2. Player 點擊右上角調性徽章（`#chordKey`）→ 彈出該調性的音階（鋼琴 / 吉他可切換）。
3. 內容參考：Pianote《Piano Scales》、Audible Genius《Exotic Piano Scales》、Musicca《Scale Finder》。

## 架構

| 檔案 | 角色 |
|---|---|
| `frontend/js/scale-lab.js` | 單一共用模組 `window.ScaleLab`：音階目錄、拼音（enharmonic spelling）、鋼琴 / 指板 canvas 渲染、TAB 產生、WebAudio 試聽、`renderInto()` 通用檢視器、`openModal()`（Player 用 Type B modal）、首頁區塊 bootstrap |
| `frontend/index.html` `#secScaleLab` | 首頁區塊，沿用 `.proglib` 外觀（`class="proglib scalelab"`），內容由 `renderInto('#scalelabHost')` 產生 |
| `frontend/css/base.css` `.sl-*` | 兩頁共用的檢視器樣式（控制列、調性 chips、組成音 chips、canvas、TAB、介紹卡） |
| `frontend/css/home.css` `.scalelab` | 首頁區塊專屬（參考資料列） |
| `frontend/css/player.css` `.scale-modal` | Player 彈窗寬度 + `#chordKey` 可點擊的 hover 樣式 |
| `frontend/js/player.js` `_openKeyScalePopup` | 讀 `#chordKey` 的 `data-mobile-key`（轉調中的當前調）+ `data-mode`（Dorian / Mixolydian…）→ `ScaleLab.openModal({key, mode})` |

### 音階目錄（`SCALES`，5 類 31 種）

- 基礎：大調、自然小調、和聲小調、旋律小調、大調五聲、小調五聲
- 教會調式：Dorian、Phrygian、Lydian、Mixolydian、Locrian
- 爵士 / 藍調：藍調、大調藍調、Bebop 屬、Bebop 大、Altered、Lydian Dominant
- 對稱：半全減、全半減、全音、半音
- 異國 / 世界：和聲大調、匈牙利小調、Phrygian Dominant、雙和聲（拜占庭）、拿坡里小調、波斯、平調子、陰旋、埃及、謎之音階

每筆：`intervals`（半音）、`formula`、`mood[]`（音色特性標籤）、`desc`（介紹）、`use`（適用曲風）、`chords`（常見和弦）、`tip`（練習提示）、選填 `degrees`（級數標示覆寫，例如 Lydian 的 #4、Altered 的 #2/#5）。

### 拼音規則（`spellScale`）

- 7 音階：每個級數一個字母（D 大調 → D E F# G A B C#）。
- 非 7 音階：依級數字母（b3 用第 3 個字母、b5 用第 5 個字母），所以 E 藍調 = E G A **Bb** B D。
- 有 `degrees` 覆寫的音階依覆寫的級數決定字母（Altered 的 #2 與 3 共用字母 D）。
- 出現 2 個以上「怪音」（Fb / Cb / E# / B# 或重升重降）時，改用等音根音（Db → C#、Ab 小調 → G# 小調），仍不行則退回專案顯示拼法（降記號、F# 例外）。

### 吉他 TAB（`buildGuitarTab`）

根音放第 6 弦，5 格把位窗（root-1 … root+3），由低弦往高弦掃、只取上行不重複的音，最多到高兩個八度的根音。指板圖畫 12 格、高音 e 弦在上（TAB 方向），空弦音以空心點畫在弦名左側。

### Player 整合

- `_updateKeyDisplay` 會寫 `keyInfo.dataset.mode`；轉調曲目的 `data-mobile-key` 已是當前段落的調，所以彈窗開的是「現在」的調。
- `scaleIdForKey("Am", "Dorian")` → `dorian`；無 mode 時 `Xm` → 自然小調、否則大調。
- 彈窗用 UX_CONVENTION §12 Type B（`.lc-modal-backdrop` + `.lc-modal`）：backdrop 點擊 / ESC / × 關閉；樂器選擇記在 `localStorage.livechord_scalelab_player`。

## 快取版本（已同步 bump）

`base.css?v=24`（index + player）、`home.css?v=69`、`player.css?v=153`、`player.js?v=363`、`scale-lab.js?v=1`、`i18n.js?v=61` + `DICT_VERSION=61`（新增 `player.key_scale_title`）。

## 後續可做（未實作）

- 三度圈 / 相鄰調式一鍵切換（例如同主音大小調、平行調式列表）。
- 吉他多把位（CAGED 5 個 box）與烏克麗麗 / 貝斯調弦。
- 依歌曲和弦自動推薦可用音階（在 Player 彈窗顯示「此段可用：Dorian / 小調五聲」）。
- 首頁區塊 EN 介紹文字（目前介紹為 zh-TW，UI 標籤已雙語）。
