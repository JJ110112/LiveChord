# LiveChord 品管文件

> 版本: 2.0 | 日期: 2026-03-28
> 和弦引擎: BTC Transformer (ISMIR 2019)

---

## 1. 測試策略

| 測試層級 | 範圍 | 工具 | 執行時機 |
|----------|------|------|----------|
| API 測試 | 所有 REST endpoint | pytest + urllib | 每次後端修改 |
| UI 測試 | 頁面交互流程 | Playwright | 每次前端修改 |
| 和弦準確度 | 偵測 vs 參考答案 | run_test.py (Lv1-Lv5) | 每次演算法修改 |
| 效能測試 | 回應時間、記憶體 | DevTools + 伺服器 log | 重大變更 |
| 迴歸測試 | 全量測試 | pytest (66+ cases) | 上線前 |

---

## 2. 和弦偵測準確度（核心 KPI）

### 2.1 分級標準

| 級別 | 難度 | Key 正確率 | 和弦正確率 | 時間誤差 |
|------|------|-----------|-----------|----------|
| Lv1 | 三和弦流行 | 100% | ≥ 80% | < 1.0s |
| Lv2 | 七和弦 R&B | 100% | ≥ 70% | < 1.5s |
| Lv3 | 爵士標準曲 | ≥ 80% | ≥ 60% | < 2.0s |
| Lv4 | 複雜 Fusion | ≥ 70% | ≥ 50% | < 2.0s |
| Lv5 | 極難挑戰 | ≥ 60% | ≥ 40% | N/A |

### 2.2 測試曲目總覽

| 級別 | 曲目 | NAS 路徑 | 參考來源 |
|------|------|----------|----------|
| Lv1 | Dancing Queen - ABBA | Z:/POP/E-POP/ABBA/ABBA - ABBA Gold/ | Chordify |
| Lv1 | Hotel California - Eagles | Z:/POP/E-POP/Eagles/Eagles - Hell Freezes Over/ | Chordify |
| Lv1 | Shape of You - Ed Sheeran | Z:/POP/E-POP/Ed Sheeran/ | Chordify |
| Lv1 | Mamma Mia - ABBA | Z:/POP/E-POP/ABBA/ABBA - ABBA Gold/ | Chordify |
| Lv1 | Billie Jean - Michael Jackson | Z:/POP/E-POP/Michael Jackson/ | Chordify |
| Lv2 | Don't Stop 'Til You Get Enough - MJ | Z:/POP/E-POP/Michael Jackson/ | Chordify |
| Lv2 | Desperado - Eagles (Live) | Z:/POP/E-POP/Eagles/Eagles - Hell Freezes Over/ | Chordify |
| Lv2 | Castle on the Hill - Ed Sheeran | Z:/POP/E-POP/Ed Sheeran/ | Chordify |
| Lv2 | Afterglow - Ed Sheeran | Z:/POP/E-POP/Ed Sheeran/ | Chordify |
| Lv2 | I Can't Tell You Why - Eagles | Z:/POP/E-POP/Eagles/Eagles - Hell Freezes Over/ | Chordify |
| Lv3 | My Foolish Heart - Bill Evans | Z:/Jazz/Bill Evans/ | Real Book |
| Lv3 | Waltz for Debby - Bill Evans | Z:/Jazz/Bill Evans/ | Real Book |
| Lv3 | Get It On - Brian Culbertson | Z:/Jazz/Brian Culbertson/ | Chordify ✓已驗證 |
| Lv4 | So What - Miles Davis | Z:/Jazz/Miles Davis/Kind Of Blue/ | Real Book |
| Lv4 | Blue in Green - Miles Davis | Z:/Jazz/Miles Davis/Kind Of Blue/ | Real Book |
| Lv4 | Mediterranean Sundance - Al Di Meola | Z:/Jazz/Al Di Meola/ | 樂譜 |
| Lv5 | Chopin Polonaise Op.53 | Z:/Classics/Alfred Brendel/ | N/A |
| Lv5 | Freddie Freeloader - Miles Davis | Z:/Jazz/Miles Davis/Kind Of Blue/ | Real Book |

### 2.3 和弦正確率計算方式

```
正確率 = 正確和弦時長 / 總有和弦時長 × 100%

判定「正確」的條件（任一滿足即算正確）：
1. 完全正確：根音 + 品質皆正確（Cm7 = Cm7）
2. 根音正確：根音對但品質略偏（Cm7 vs Cm → 算 0.7 分）
3. 時間容差：和弦開始時間在 ±容差 秒內
```

### 2.4 測試執行

```bash
# 單級測試
cd data/test_songs
python run_test.py Lv1

# 全級測試
python run_test.py all

# 單曲命令列測試
cd backend
python chord_detect.py "Z:/POP/E-POP/ABBA/ABBA - ABBA Gold/Dancing Queen.flac"
```

### 2.5 已驗證結果

| 曲目 | 版本 | Key | 主要和弦 | 比對結果 |
|------|------|-----|----------|----------|
| Get It On - Brian Culbertson | BTC v4 | Cm ✓ | Cm→Dm7→G7→Cm ✓ | 與 Chordify 吻合 |

---

## 3. 後端 API 測試

### 3.1 音樂庫 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| M-01 | /api/browse | GET | 根目錄瀏覽 | 回傳 Genre 分類 | ☐ |
| M-02 | /api/browse?path=Jazz | GET | 子目錄瀏覽 | 回傳 Artist 清單 | ☐ |
| M-03 | /api/browse 深層 | GET | 瀏覽到 Track | 回傳 .flac 清單 | ☐ |
| M-04 | /api/search?q=blackpink | GET | 關鍵字搜尋 | 回傳匹配結果 | ☐ |
| M-05 | /api/search?q= | GET | 空搜尋 | 回傳空清單 | ☐ |
| M-06 | /api/track/info | GET | 單曲 metadata | 回傳 title/artist/duration | ☐ |
| M-07 | /api/track/stream | GET | FLAC 串流 | 回傳音訊，支援 Range | ☐ |
| M-08 | /api/track/stream (Range) | GET | 分段串流 | 206 + Content-Range | ☐ |
| M-09 | /api/track/cover | GET | 封面圖片 | 回傳 JPEG | ☐ |
| M-10 | /api/track/cover (無封面) | GET | 無圖時 | 404 | ☐ |
| M-11 | /api/library/stats | GET | 庫統計 | 回傳 total_tracks, scan_time | ☐ |
| M-12 | /api/library/scan | POST | 啟動掃描 | 回傳 ok + 背景執行 | ☐ |
| M-13 | /api/library/scan/status | GET | 掃描進度 | 回傳 running/progress | ☐ |

### 3.2 和弦 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| C-01 | /api/chord/info/Cmaj7 | GET | 和弦資訊 | 回傳 notes, jianpu | ☐ |
| C-02 | /api/chord/info/Unknown | GET | 未知和弦 | 404 | ☐ |
| C-03 | /api/chord/diagram/guitar/Am | GET | 吉他和弦圖 | 回傳 strings, baseFret | ☐ |
| C-04 | /api/chord/diagram/ukulele/C | GET | 烏克麗麗和弦圖 | 回傳 4 弦資料 | ☐ |
| C-05 | /api/chords?path=... | GET | 取得和弦譜 | 回傳 chords 陣列 | ☐ |
| C-06 | /api/chords (無資料) | GET | 未偵測曲目 | exists: false | ☐ |
| C-07 | /api/chords | POST | 儲存和弦譜 | ok: true | ☐ |
| C-08 | /api/chords/detect?path=... | POST | 單曲和弦偵測 | 回傳 key + chords | ☐ |
| C-09 | /api/chords/batch-detect | POST | 批次偵測啟動 | 背景執行 | ☐ |
| C-10 | /api/chords/batch-detect/status | GET | 批次進度 | done/total/current | ☐ |
| C-11 | /api/chords/stats | GET | 和弦統計 | total/coverage | ☐ |

### 3.3 使用者 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| U-01 | /api/favorites | GET | 取得最愛 | 回傳 favorites 清單 | ☐ |
| U-02 | /api/favorites | POST | 新增最愛 | ok: true | ☐ |
| U-03 | /api/favorites | DELETE | 移除最愛 | ok: true | ☐ |
| U-04 | /api/recent | GET | 最近播放 | 回傳 recent 清單 | ☐ |
| U-05 | /api/recent | POST | 新增紀錄 | ok: true | ☐ |

### 3.4 自動工作器 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| A-01 | /api/auto/status | GET | 工作器狀態 | running: false (預設) | ☐ |
| A-02 | /api/auto/settings | GET | 取得設定 | auto_scan_enabled: false | ☐ |
| A-03 | /api/auto/settings | POST | 更新設定 | ok: true | ☐ |
| A-04 | /api/auto/start | POST | 啟動工作器 | ok: true | ☐ |
| A-05 | /api/auto/stop | POST | 停止工作器 | ok: true | ☐ |
| A-06 | /api/auto/log | GET | 活動紀錄 | 回傳 log 陣列 | ☐ |

---

## 4. 前端 UI 測試

### 4.1 首頁

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| H-01 | 載入首頁 | 顯示瀏覽/最愛 Tab + 搜尋列 | ☐ |
| H-02 | 目錄瀏覽 | Genre → Artist → Album → Track 逐層展開 | ☐ |
| H-03 | 搜尋歌曲 | 輸入關鍵字，300ms debounce 後顯示結果 | ☐ |
| H-04 | 點擊結果 | 跳轉至播放頁 | ☐ |
| H-05 | 最愛 Tab | 顯示已收藏歌曲，可點擊播放 | ☐ |
| H-06 | 最近播放 Tab | 顯示最近播放紀錄 | ☐ |
| H-07 | 封面縮圖 | Album 資料夾有 cover.jpg 時顯示 | ☐ |
| H-08 | 庫狀態指示 | 右上角顯示已索引曲目數 | ☐ |
| H-09 | 空搜尋結果 | 顯示「找不到結果」 | ☐ |

### 4.2 播放頁

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| P-01 | 載入播放頁 | 顯示封面、歌曲資訊、播放器 | ☐ |
| P-02 | 播放/暫停 | 正常播放 FLAC，暫停/恢復正常 | ☐ |
| P-03 | 進度條 seek | 點擊/拖曳進度條跳轉正確位置 | ☐ |
| P-04 | 音量調整 | 滑桿調整音量 | ☐ |
| P-05 | 上一首/下一首 | 同目錄內換曲 | ☐ |
| P-06 | 收藏切換 | 愛心 toggle，狀態持久 | ☐ |
| P-07 | 歌曲 metadata | 標題/演出者/專輯/取樣率/位元深度 | ☐ |
| P-08 | **無和弦時** | 顯示「請按偵測按鈕」，播放不觸發偵測 | ☐ |
| P-09 | **偵測按鈕** | 手動點擊後開始偵測，顯示 overlay 進度 | ☐ |
| P-10 | **和弦顯示（簡譜）** | 顯示和弦名 + 數字簡譜 | ☐ |
| P-11 | **和弦顯示（吉他）** | 顯示 Canvas 和弦圖 | ☐ |
| P-12 | **和弦顯示（烏克麗麗）** | 顯示 4 弦和弦圖 | ☐ |
| P-13 | **即時高亮** | 播放時當前和弦高亮 + 自動捲動 | ☐ |
| P-14 | **大字和弦** | 上方大字顯示當前和弦名+簡譜/圖 | ☐ |
| P-15 | **點擊和弦跳轉** | 點擊和弦卡片跳轉到該時間 | ☐ |
| P-16 | **時間軸連動** | seek 後高亮更新到正確和弦 | ☐ |
| P-17 | **移調 +/-** | 所有和弦正確移調 | ☐ |
| P-18 | **Capo 設定** | Capo 數值影響顯示和弦 | ☐ |
| P-19 | 播放結束 | 進度歸零，高亮消失 | ☐ |
| P-20 | 大檔案播放 | >100MB FLAC 不中斷 | ☐ |

### 4.3 和弦編輯器

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| E-01 | 載入編輯器 | 顯示時間軸 + 和弦列表 | ☐ |
| E-02 | 新增和弦 | 點擊時間軸新增和弦標記 | ☐ |
| E-03 | 修改和弦名 | 雙擊編輯和弦名稱 | ☐ |
| E-04 | 拖曳調整時間 | 拖曳和弦標記調整 time/end | ☐ |
| E-05 | 刪除和弦 | 選取後刪除 | ☐ |
| E-06 | 儲存 | 儲存後播放頁可讀取 | ☐ |

### 4.4 管理頁

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| A-01 | 載入管理頁 | 顯示統計儀表板 | ☐ |
| A-02 | 音樂庫掃描 | 增量/全量掃描，進度即時更新 | ☐ |
| A-03 | 批次和弦偵測 | 啟動/停止，進度即時更新 | ☐ |
| A-04 | 自動掃描開關 | Toggle 後設定持久化 | ☐ |
| A-05 | 自動偵測開關 | Toggle 後設定持久化 | ☐ |
| A-06 | 工作器啟停 | 啟動/停止/立即觸發 | ☐ |
| A-07 | 活動紀錄 | 顯示最近操作 log | ☐ |

---

## 5. 安全性檢查

| # | 項目 | 檢查內容 | 預期 | 通過 |
|---|------|----------|------|------|
| S-01 | 路徑穿越 | path=../../etc/passwd | 403 拒絕 | ☐ |
| S-02 | 路徑穿越 (encoded) | path=%2e%2e%2f | 403 拒絕 | ☐ |
| S-03 | 檔案類型 | stream 非 .flac 檔案 | 400 拒絕 | ☐ |
| S-04 | XSS | 搜尋含 `<script>` 的字串 | 正確 escape | ☐ |
| S-05 | 輸入驗證 | 超長字串、特殊字元 | 不崩潰 | ☐ |

---

## 6. 效能基準

| 指標 | 目標值 | 實測值 | 通過 |
|------|--------|--------|------|
| 首頁載入 | < 1s | | ☐ |
| 搜尋回應 (78K tracks) | < 500ms | | ☐ |
| 串流首次播放 | < 2s | | ☐ |
| 目錄瀏覽 | < 500ms | | ☐ |
| 單曲和弦偵測 (BTC) | < 60s | | ☐ |
| 增量掃描 (無變動) | < 3min | | ☐ |
| 全量掃描 (78K tracks) | < 30min | | ☐ |
| 伺服器記憶體 (idle) | < 500MB | | ☐ |
| 伺服器記憶體 (偵測中) | < 2GB | | ☐ |

---

## 7. 相容性

| 環境 | 測試項目 | 通過 |
|------|----------|------|
| Chrome 120+ | 全功能 | ☐ |
| Edge 120+ | 全功能 | ☐ |
| Firefox 120+ | 全功能 | ☐ |
| Safari | FLAC 不支援，應顯示提示 | ☐ |
| Mobile Chrome | 基本播放+和弦檢視 | ☐ |

---

## 8. 已知限制

| 項目 | 說明 | 影響 |
|------|------|------|
| FLAC 播放 | Safari 不支援原生 FLAC 解碼 | 需改用 Opus 或提示 |
| NAS 離線 | Z:\ 不可用時所有功能失效 | 應顯示錯誤訊息 |
| 並行寫入 | JSON 檔無寫入鎖 | 私人用途可接受 |
| BTC 模型限制 | 170 種和弦詞彙，無法辨識所有變體 | 如 add9, 13 等 |
| BTC 推論速度 | CPU-only，每首約 30-60 秒 | 考慮 GPU 加速 |
| 古典音樂 | 和弦概念不適用古典和聲 | Lv5 僅參考 |

---

## 9. 缺陷分級

| 等級 | 定義 | 處理時限 | 範例 |
|------|------|----------|------|
| P0-Critical | 無法使用核心功能 | 立即修復 | 無法播放、偵測崩潰 |
| P1-Major | 功能異常但有替代 | 24h 內 | 和弦高亮不同步、搜尋失效 |
| P2-Minor | UI 瑕疵 | 下次迭代 | 和弦圖缺失、樣式偏移 |
| P3-Enhancement | 改善建議 | 排入 backlog | 新增和弦類型、UI 美化 |

---

## 10. 驗收流程

```
1. 將測試曲目 FLAC 放入 data/test_songs/Lv1~Lv5/
2. 啟動伺服器: start.bat
3. 執行 API 測試: cd tests && pytest
4. 執行和弦準確度: cd data/test_songs && python run_test.py all
5. 手動 UI 測試: 開啟 http://localhost:8800 逐項驗收
6. 記錄結果: 勾選上方清單，未通過項開 issue
7. 全部 P0/P1 修復後 → 版本通過
```

---

## 11. 版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
| 1.0 | 2025-03-25 | 初版：Phase 1-6 功能完成 |
| 2.0 | 2026-03-28 | 和弦引擎升級 BTC Transformer，新增分級測試框架 |
