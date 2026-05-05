# LiveChord 壓力測試報告 — 2026-04-19

## 背景與問題

使用者想驗證：「10 個用戶同時按下分析新曲，伺服器會不會炸？」
這份報告記錄實際測試結果、過程中發現的邊角 bug，以及後續改進方向。

- **測試目標**：Beta 8801（LAN 直連 `http://192.168.50.6:8801`，非公開 `livechord.org`，見文末「方法論」）
- **測試工具**：`scratch/stress_test.py`（`httpx` + `asyncio`，不納入版控）
- **測試帳號**：`qa01..qa10`，每帳號 10/日 quota
- **URL 素材**：使用者提供 10 支全新 YouTube 短片
- **測試執行**：2026-04-19 21:14 → 21:27（總計 13 分鐘）

---

## 測試結論

### ✅ 伺服器沒炸

三個情境（Scenario A/B/C）全部走完，沒有 500、沒有 traceback、沒有 process 崩潰。
10 個朋友同時按分析，最糟情況是「有人看到 503 佇列已滿，等一下重送」──這是設計預期的行為，不是事故。

### 三情境結果

| Scenario | 投放 | 結果 | 耗時 |
|---|---|---|---|
| **C（最先跑，25 筆快速提交）** | 25 submit | **21 × 200 queued + 4 × 503 queue-full** | 最後一筆排隊到全部完成 465 秒（7.75 分） |
| **A（10 用戶 × 10 新 URL）** | 10 submit | 10 × REUSE（皆由 library_map 命中） | 30.4 秒（但平行呼叫卻花 30 秒，見下方警示） |
| **B（同一 URL × 10）** | 10 submit | 10 × REUSE | 172 ms |

關鍵觀察：

- **佇列溢出回傳乾淨 503**，body 是 `{"detail": "處理佇列已滿，請稍後再試"}`，不是 500 也不是裸 traceback。前端可以安全地對 503 顯示「請稍候」。
- **21 個 job 無一錯誤**（`done=21, error=0, timeout=0`）。
- **平均處理時間 ~22 秒/首**（比原先估計的 280 秒/首快得多，推測是短影片 + `LIVECHORD_BTC_WORKERS=2` 平行 BTC 的雙重效應）。

### ⚠️ 真正該注意的問題：壓測期間伺服器「還在」但「反應很慢」

我們在整個測試過程中每 5 秒戳一次 `/api/recent`（測 server 對無關請求的反應），共 50 筆樣本：

| 指標 | 值 |
|---|---|
| 最短延遲 | 46 ms |
| 最長延遲 | 10016 ms（觸及 10 秒 timeout） |
| 平均延遲 | **5196 ms** |
| Timeout / 連線失敗 | **21/50（42%）** |

這代表：當 BTC 在全速跑的時候，一個真實使用者想打開首頁 / 查看最近播放，**會看到 5~10 秒的明顯卡頓，甚至直接 timeout**。伺服器沒死，但使用者體驗是差的。

另一個佐證：Scenario A 的 10 個平行 REUSE 請求（原本應該只是 10 次 SQLite SELECT，每個 < 50 ms）整體花了 **30 秒**。這不是 library_map 查詢本身慢，是 API 執行緒被前一批 C 的後續工作（melody worker、library_map 寫入、audit 寫入）卡住。

---

## 過程中發現的邊角 bug

這些不是「10 人併發才暴露」的問題，而是測試腳手架在爬伺服器 API 時順便挖出來的。

### 🐛 bug #1 — admin 後台「產生邀請碼」在 HTTP 下顯示「建立失敗」但其實有成功

**現象**：按「+ 產生邀請碼」→ toast 跳「建立失敗: Cannot read properties of undefined (reading 'writeText')」，但 DB 裡其實已經新增成功，只是 UI 列表沒重新整理。

**根因**：`navigator.clipboard` 只在 HTTPS 或 localhost 能用；NUC 上的 admin 是 `http://192.168.50.6:8800/admin`（HTTP LAN），所以 `navigator.clipboard` 是 `undefined`，呼叫 `.writeText()` 直接擲出 TypeError，外層 `try/catch` 把它當成「建立失敗」處理。

**修正**：已部署到 `V:\frontend\admin.html`。現在會檢查 `window.isSecureContext && navigator.clipboard` 才複製；複製失敗也不影響列表重新整理（因為邀請碼已顯示在 toast 跟下方列表）。同時新增「+ 自訂次數」按鈕，支援使用者輸入任意次數（例如給 20 位朋友測試用）。

**檔案**：[frontend/admin.html](frontend/admin.html) `createInvite()` 與新增的 `createInviteCustom()`

### 🐛 bug #2 — 邀請碼 `use_count` 在 INSERT 之前就遞增了

**現象**：測試過程中發現，對一個**已存在**的使用者呼叫 `/api/auth/register`，伺服器回 400「已被註冊」，但該次嘗試**仍消耗了邀請碼一格 `use_count`**。

**根因**：[backend/auth_api.py](backend/auth_api.py) 的 register endpoint 流程是：
1. `_check_rate_limit`
2. `_validate_invite_code`（**內部直接 `UPDATE invite_codes SET use_count = use_count + 1`**）
3. password 長度檢查
4. `INSERT INTO users`（失敗會 IntegrityError → 400 已被註冊）

邀請碼在第 2 步就被記了一次「使用」，但第 4 步可能因為 username 重複而失敗。結果就是：**失敗的註冊嘗試也會消耗邀請碼配額**。

**建議修法**：把 `_validate_invite_code` 拆成兩個階段 — `_check_invite_valid()`（只讀，不改 `use_count`）跟 `_consume_invite(code)`（寫 `use_count += 1`）。register endpoint 在 INSERT 成功後才呼叫 `_consume_invite`。整個流程用 SQLite transaction 包住保證原子性。

**影響**：邀請朋友時如果有任何一個在註冊時失手（用重複 username、密碼太短、併發競速），邀請碼會被白白吃掉一格。給 5 位朋友的 5-次邀請碼，實際可能只有 3-4 位能成功。

### 🐛 bug #3 — Cloudflare Tunnel 下 rate limiter 沒讀到真實 client IP

**現象**：測試一開始走 `https://livechord.org`（公開路徑），才 3-4 個 auth 呼叫就被 429 rate-limited，遠低於 10/5min 上限。

**根因**：[backend/auth_api.py](backend/auth_api.py) 的 `_check_rate_limit` 原本用 `request.client.host`。在 Cloudflare Tunnel 架構下 cloudflared 把所有公開流量轉發到 uvicorn 時，`request.client.host = 127.0.0.1`（tunnel endpoint）。結果**所有公開使用者共用同一個 rate-limit bucket**，10/5min 是全球 10 次，不是每人 10 次。

**我做了什麼**：抽出 `_real_client_ip(request)` helper（沿用同檔案 `get_current_user` 現有的 header 讀法：`CF-Connecting-IP` → `X-Forwarded-For` → `request.client.host`），讓 `_check_rate_limit` 用真實 IP 當 key。已部署到 `V:\backend\auth_api.py`、NUC 重啟過。

**但沒有真正修好**：部署後，從我的 PC 走公開路徑再測，**仍然只 3-4 次就 429**。表示 `CF-Connecting-IP` header 在 cloudflared → uvicorn 這段沒有正確傳過來，或者還有別的 rate limit 層沒看到。為了讓壓測能繼續走，最後改成直接打 LAN IP `http://192.168.50.6:8801` 繞過 Cloudflare。

**未完待辦**：
- 在 `_check_rate_limit` 暫時加 log，印出 `request.headers` + `_real_client_ip` 結果，看 header 到底有沒有到
- 或者直接在 `/api/auth/login` 先加 log，確認 cloudflared 有轉發 `CF-Connecting-IP`
- Cloudflared 的 config 可能要加 `--protocol http2` 或檢查 tunnel 設定
- **在邀請朋友之前一定要修好**，否則 5 位朋友裡只要有 1-2 位在同時間登入註冊，其他人就會全部 429

---

## 改進建議（按優先度）

### P0 — 邀請朋友前必修

1. **修 bug #3（CF-Connecting-IP 沒生效）**：rate limiter 在公開路徑是壞的，朋友會互相卡。
2. **修 bug #2（邀請碼消耗順序）**：朋友註冊失手會白白耗掉配額。

### P1 — 壓測暴露出的可用性問題

3. **BTC 跑的時候 `/api/recent` 會卡 5~10 秒**。幾個可能方向：
   - 觀察：`/api/recent` 內部做了什麼？掃 `data/chords/` 目錄還是讀 SQLite？
   - 如果是 SQLite：檢查 WAL 模式是否真的有效（`PRAGMA journal_mode` 應回 `wal`），寫鎖理論上不該擋讀。
   - 如果是目錄掃描：加個記憶體快取（TTL 30 秒）避免每次 listdir。
   - 實作上：加 `logging.info` 到 `/api/recent` 入口與出口，測壓力下看真正耗時的是哪段。

4. **佇列上限 20 可能對「給朋友用」太小**。20 人分 10 首共 200 首，但同時線上併發不到 20。
   - 短期：評估加到 50，成本低。
   - 長期：前端先算「我的 quota 用剩幾個」不讓用戶戳爆佇列。

### P2 — 觀測性缺口

5. **`V:\data\server.log` 自 Apr 15 沒更新過**，代表 uvicorn 目前只寫 stdout（開在 cmd 視窗裡）。關了視窗就沒紀錄。建議：
   - uvicorn 啟動改加 `--log-config logging.json`，寫到 `data/server.log` + 輪替。
   - 這樣事後 debug 才有資料。

### P3 — 架構性改進（可選）

6. **Mid-flight dedupe**：目前 `find_existing_result` 只檢查 `status='done'`。如果 10 個朋友同時點同一個新 URL，會排 10 個重複 job。可以改成「若發現該 URL 已在 queue/processing，就 join 到既有 job 而不是另開」。
   - 本次測試 Scenario C 有 25 submit → 21 進 queue，URL 有重複但沒 dedupe；worker 真的跑了 21 次（其實只需要 10 次）。
   - 優先度不高，因為通常 URL 都是不同的。

7. **工作者併發性**：`LIVECHORD_BTC_WORKERS=2` 已經幫很多忙（22 秒/首）。如果 GPU VRAM 夠，可以試試 3-4。要先量個 VRAM。

---

## 方法論 / 附錄

### 為什麼最後沒跑公開 `livechord.org` 而改 LAN 直連？

原本計畫走 `https://livechord.org` 測「真正公開路徑」。但被 rate limiter 擋住（見 bug #3）。改繞 Cloudflare 走 LAN 直連後 uvicorn 會看到我 PC 的 LAN IP 作為 client.host，rate limit bucket 是獨立的，測試才能繼續。

結論：**Cloudflare Tunnel 那段的 rate-limit 行為還沒被這次壓測覆蓋到**。如果 bug #3 修好，建議再補跑一次公開路徑的 Scenario A。

### 執行順序的設計

原本計畫 A → B → C。但 A 跑完後 10 個 URL 會被寫進 `youtube_library_map`，後續 C 的 25 筆要用這些 URL 就全變 REUSE，無法真正填滿佇列。所以實際順序改成 **C → A → B**：
- C 先跑（`library_map` 空的，URL 真的進 BTC pipeline，能觀察佇列行為）
- C 排掉後 library_map 有了 10 筆
- A 跑（現在全部 REUSE，但測「10 併發 REUSE 是否卡」）
- B 跑（同 URL × 10，驗證 dedupe 成功）

### 硬體與設定

- NUC：Windows 11，`LIVECHORD_BTC_WORKERS=2`（由 `start_dual.bat` 設定）
- 8801 uvicorn：`python -m uvicorn main:app --host 0.0.0.0 --port 8801`（無 `--proxy-headers` 或 `--forwarded-allow-ips`）
- SQLite WAL 模式、`timeout=10`
- 佇列 `_job_queue = queue.Queue(maxsize=20)`，單一 worker thread

### 原始資料

- 測試腳本：`scratch/stress_test.py`（.gitignore 中，不進 repo）
- 完整結果 JSON：`scratch/stress_test_results.json`
- Log：`scratch/stress_test.log`

三者都在 git-ignored 的 `scratch/` 目錄，保留給日後 debug 用。
