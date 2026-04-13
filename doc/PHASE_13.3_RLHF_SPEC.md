# LiveChord Phase 13.3: 人機協作回饋與微調系統 (RLHF & Section Editor)

為了解決「偶爾不合群的推論孤島」並落實長遠的 AI 模型自我進化，我們將正式進入人機協作 (Human-in-the-Loop) 的微調階段。
此階段的功能皆已實裝完畢並部署於伺服器。

## 🎯 核心機制

1. **打通 和弦品質 (Star Rating) 回饋系統**：將 UI 右上角的「評分」連動至後端資料庫。
2. **建構 樂句編輯 (Section Editing) 與 覆蓋機制**：允許使用者在 UI 點擊修改錯誤段落（例如把不合群的 Chorus 切換回 Verse），並將此「完美修正」永久保存。
3. **離線微調準備 (Online-to-Offline Fine-tuning)**：讓這些人類提供的黃金標準 (Ground Truth) 能夠成為下一波模型重新訓練 (Retraining) 的無價養料。

---

## 🛠 實作架構 (Implementation Details)

### Backend API (`backend/ai_api.py`)

*   **`POST /api/ai/evaluate-feedback`**
    *   接收和弦星星評分 (Good / Bad)。
    *   將使用者的回饋附加 (Append) 儲存至 `W:\data\human_feedback\chord_eval.jsonl`，作為未來機器的偏好對齊學習訓練集。
*   **`POST /api/ai/sections/feedback`**
    *   接收使用者手動修正後的 `[{type: "verse", start: 0, end: 12}, ...]` 陣列。
    *   覆寫並儲存進 `W:\data\human_sections\{song_hash}.json`，建構人類標註庫。

### AI 推理引擎 (`backend/ai/section_detect.py`)

*   **`detect_sections()` 權限覆寫**
    *   在呼叫 Rule-based 或 DL 推理**之前**，先攔截檢查 `W:\data\human_sections\{song_hash}.json` 是否存在。
    *   若存在，**強制優先載入人類標記的段落**。這能讓使用者立刻感受到「修正後一勞永逸」的成果，解決深度學習孤島特徵問題。

### Frontend UI (`frontend/js/player.js` & `player.css`)

*   **星星評分機制實裝**
    *   解除原先 UI 的假按鈕狀態，實裝真正的 `fetch('/api/ai/evaluate-feedback')` 呼叫，並提供 Toast 成功提示。
*   **段落編輯器 Modal (右鍵選單)**
    *   使用者在樂句名稱上「點擊右鍵 (Context Menu)」時，會跳出黑色懸浮選單修改標籤。
    *   後台送出 HTTP 請求並自動刷新 UI (`_loadSections()`) 以反映人類專家的指引。

---

## 👨‍🏫 測試與未來展望

1. **強制覆蓋測試**：在播放器介面對著錯誤的色塊點選右鍵，修改為正確段落後，重新載入該首曲目，驗證系統會載入您的修正結果。
2. **未來 Phase 14 計畫**：等 `W:\data\human_sections\` 累積足夠多的人工精心校正樣本後，我們可以編寫 `fine_tune.bat`，將這個人類最高指導原則混入訓練資料集重新執行一遍 Back-propagation，根治神經網路在相同橋段的聽力障礙。
