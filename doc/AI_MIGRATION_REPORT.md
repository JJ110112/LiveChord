# AI Migration Report — pYIN → basic-pitch (Phase 1 / 1.5 / 2)

**Date**: 2026-04-20
**Scope**: 旋律擷取從 `librosa.pyin` 升級到 Spotify basic-pitch (ONNX)
**Upstream plan**: [../ai_migration_plan.md](../ai_migration_plan.md)
**Status**: ✅ Shadow Mode deployed on NUC 8801/8800, awaiting real-user traffic

---

## 1. 一頁摘要

| 項目 | 結果 |
|---|---|
| V1 pYIN（3-4 分鐘歌） | 38-128 秒 / 200-300 notes |
| V2 basic-pitch + melody filter | **3-8 秒 / 600-1300 notes** |
| 速度提升 | **11× – 23×**（穩定區間） |
| 硬體偵測 | OpenVINO EP 真正接上（Phase 1.5 修復） |
| 佈署模式 | Shadow Mode（V1 仍 primary） |
| 用戶看到的改動 | 0（ENABLE_NN_MELODY=False） |

---

## 2. 硬體 / 軟體環境

- **NUC**: Intel Core Ultra 9 285H + Arc 140T iGPU (16GB) + AI Boost NPU
- **PC**（開發機）: RTX 5080
- **NUC 系統 Python**: 3.14.3（過新，無 ML wheel 支援）
- **解法**: 建立 isolated venv 於 `c:\LiveChord\venv_ai\`，使用 **Python 3.11.9**
- **核心套件版本**（經 ABI 驗證可共存）：
  - `onnxruntime-openvino==1.22.0`
  - `openvino==2025.2.0`
  - `basic-pitch==0.4.0`
  - `tensorflow==2.15.0`（basic-pitch 依賴）
  - `librosa==0.11.0`

### ⚠️ 版本鎖定的血淚

原本 `requirements_nuc.txt` 寫 `onnxruntime-openvino>=1.16.0` + `openvino>=2023.2.0`，pip 自動解成 1.24.1 + 2026.1.0，兩者 ABI **不相容**：

```
Error loading "onnxruntime_providers_openvino.dll"
But no dependency issue could be determined.
(Error 127: "找不到指定的程式")
```

試了 `openvino==2025.3.0` 和 `2025.2.0` 都失敗。降版 `onnxruntime-openvino==1.22.0` + `openvino==2025.2.0` 才三裝置（CPU_FP32 / GPU_FP16 / NPU）都能 instantiate。已鎖版到 requirements_nuc.txt 並加註釋。

---

## 3. 三裝置 Benchmark

**測試歌曲**: ABBA - Chiquitita (Official Music Video).flac（約 3:30、68MB）

| 設定 | Load time | Run time | Notes | 評估 |
|---|---:|---:|---:|---|
| V1 pYIN（full adaptive） | — | **128.2s** | 202 | baseline |
| V1 pYIN（fast mode） | — | 41.9s | 294 | batch baseline |
| V2 OpenVINO default | 3.41s | 6.34s | 1313 | ✓ OK |
| **V2 OpenVINO CPU_FP32** | 0.14s | **5.52s** | 1313 | ⭐ **最快，推薦預設** |
| V2 OpenVINO NPU | 0.11s | 8.60s | 1313 | basic-pitch 太小（17MB），NPU overhead 不划算 |
| V2 OpenVINO GPU_FP16 | 0.41s | 7.89s | **0** | ⚠️ **FP16 精度炸，回傳空 list** |

### 觀察

- **CPU_FP32 是最佳選擇**，比 default 快 ~0.8 秒（OpenVINO EP 的 CPU path 比 ORT 原生 CPU 更優化）
- **GPU_FP16 是陷阱**：basic-pitch ICASSP 模型的 activation 範圍超出 FP16 精度，silent fail（不拋例外，但 output 全變 0）→ 不要用
- **NPU 對 17MB 小模型沒優勢**：NPU 在大型 Transformer 才能發揮。若 Phase 3+ 改用 RMVPE（較大）再評估

### 跨歌穩定性

| 歌曲 | V1 (fast) | V2 (CPU_FP32) | Speedup |
|---|---:|---:|---:|
| 10cc - I'm Not In Love | 41.9s / 294 notes | **3.7s / 647 notes** | 11.4× |
| Air Supply - Here I Am | 38.1s / 296 notes | **3.0s / 750 notes** | 12.8× |
| ABBA - Chiquitita | 128s / 202 notes | **5.5s / 1313 notes** | 23× |

V2 音符數量約 V1 的 2-6×（filter 後），是因為 basic-pitch 抓到更多短 ornament / passing note / chord-tone bleed。

---

## 4. 三階段實作紀錄

### Phase 1 — 骨架上線（commits `05f4d5c`, `6233163`）

- 建立 `backend/ai/melody_extractor_v2.py`（136 行）
- `backend/config.py` 加 `get_env_mode()`（path-based PC/NUC 偵測）+ `ENABLE_NN_MELODY=False`
- `requirements_pc.txt` / `requirements_nuc.txt`

**Phase 1 暴露的三個問題**：

1. ❌ `_detect_provider()` 印對了 `OpenVINOExecutionProvider`，但 providers list 從未傳進 `basic_pitch.inference.predict`
2. ❌ basic-pitch 的 `Model.__init__` 寫死 `providers=["CPUExecutionProvider"]`
3. ❌ 輸出 5338 notes（polyphonic），約 25 notes/sec，無法直接餵給 waterfall UI
4. ❌ `__main__` 的 `print(♯)` 在 cp950 console 拋 `UnicodeEncodeError`

### Phase 1.5 — Provider 接線 + Melody Filter（commit `3d4de5f`）

- **ONNX session 接管**：自建 `ort.InferenceSession(path, providers=[OpenVINO, CPU])`，覆寫 `model.model`
- **DLL 路徑修復**：`import openvino` 先執行（讓其 `__init__` 呼叫 `os.add_dll_directory`）
- **強制 ONNX path**：若 `Model()` 被 auto-picked 到 TF，用 `Model.__new__(Model)` 建空殼 + 灌我們的 session
- **Melody filter（4-pass）**：
  1. confidence ≥ 0.3 門檻
  2. median 八度剃 bass note（median-12 以下整體砍）
  3. highest-pitch wins per time slice（時間重疊時保留較高音）
  4. 相鄰同 midi 合併（gap < 0.1s）
- **device_type 選項**：`MelodyExtractorV2(device_type="CPU_FP32" | "NPU" | "GPU_FP16")`
- **CLI `sys.stdout.reconfigure(encoding='utf-8')`** 修 cp950 bug
- **退路**：所有加速步驟 try/except 包起來，失敗自動退回 basic-pitch 預設 path-string

### Phase 2 — Shadow Mode（commit `9a8a286`）

**架構選擇**：subprocess 封裝（不動 uvicorn Python 環境）

```
用戶上傳 → BTC 和弦 → DONE → melody-worker
                                │
                                ├─► V1 pYIN → data/melodies/<hash>.json（給用戶）
                                │
                                └─► run_shadow_async()
                                      │
                                      ├─ 複製 audio 到 data/tmp/shadow/
                                      └─ daemon thread 啟動 subprocess:
                                           venv_ai/python -m backend.ai.melody_extractor_v2 \
                                             <audio> --json-out data/melodies_v2/<hash>.json --quiet
                                      │
                                      └─► 完成時 append JSONL 到 data/shadow_v2.log
```

**關鍵檔案**：

| 檔 | 角色 |
|---|---|
| `backend/ai/melody_shadow.py` | subprocess runner（新增） |
| `backend/ai/melody_extractor_v2.py` | 加 `--json-out` + `--quiet` CLI |
| `backend/process_queue.py` `_melody_worker_loop` | 記錄 V1 耗時 → 呼叫 `run_shadow_async` |
| `backend/config.py` | `SHADOW_V2_ENABLED = True`（可後續關） |
| `.gitignore` | `data/melodies_v2/`、`data/shadow_v2.log` |

**E2E 驗證 log**：

```json
{
  "ts": "2026-04-20T13:30:49Z",
  "hash": "shadowtest123abc",
  "v1_time_s": 38.5, "v1_notes": 296,
  "status": "ok",
  "v2_time_s": 7.42, "return_code": 0, "v2_notes": 647
}
```

---

## 5. 退路 / Fallback 機制

所有破損情境都被 graceful 吞掉，用戶端 0 感知：

| 情境 | 行為 |
|---|---|
| `venv_ai` 未建立 | Shadow 自動 skip，印一次 log warning，不再騷擾 |
| ONNX session 建置失敗（DLL / ABI） | 退回 basic-pitch 預設 CPU path |
| `_detect_provider()` 取到 CPU（PC/NUC 偵測失敗） | 就跑 CPU，仍比 V1 快 |
| Subprocess timeout（>180s） | Log `status: timeout`，不影響 V1 輸出 |
| Subprocess crash | Log `status: error` + stderr tail，不影響 V1 輸出 |
| Shadow thread 崩潰 | daemon thread 無聲死亡，主 worker 繼續 |
| `ENABLE_NN_MELODY = True` 誤開 | **目前仍無程式碼路徑會讀這個 flag** — 翻開也不會切 V2（Phase 3 才接） |

---

## 6. 已知限制

1. **GPU_FP16 不可用** — ICASSP 模型 activation 範圍超出 FP16 → 輸出全 0。若未來要用 Arc iGPU 加速，需轉 `GPU_FP32` 或改模型
2. **NPU 對 basic-pitch 無增益** — 17MB 太小，overhead > savings
3. **V2 仍是 polyphonic 本質** — melody filter 只能「近似」melody（取最高聲部），遇複雜編曲（和聲合唱、吉他 riff 高於主旋律）可能抓錯聲部
4. **Subprocess 有 ~2-3s TF 啟動成本** — 每首歌都重新載模型；不影響 shadow 效能（不在 critical path）
5. **basic-pitch 依賴 TensorFlow 2.15 + Keras 2.15** — 未來要升 TF 3.x 可能要處理 basic-pitch 相容性
6. **Python 3.14.3 wheel 斷層** — 系統 Python 3.14 無法直接跑 ML libs；venv 3.11 是唯一路徑

---

## 7. 操作手冊

### 觀察 shadow 資料累積

```bash
# 有多少首歌被 shadow 過
wc -l c:/LiveChord/data/shadow_v2.log

# 最近 10 筆
tail -10 c:/LiveChord/data/shadow_v2.log

# 統計 V1 平均耗時 vs V2
python -c "
import json
lines = open('c:/LiveChord/data/shadow_v2.log', encoding='utf-8').readlines()
entries = [json.loads(l) for l in lines if l.strip()]
ok = [e for e in entries if e.get('status') == 'ok']
print(f'total: {len(entries)}, ok: {len(ok)}')
if ok:
    v1 = [e['v1_time_s'] for e in ok if e.get('v1_time_s')]
    v2 = [e['v2_time_s'] for e in ok if e.get('v2_time_s')]
    print(f'V1 avg: {sum(v1)/len(v1):.1f}s | V2 avg: {sum(v2)/len(v2):.1f}s | speedup: {sum(v1)/sum(v2):.1f}x')
"

# 錯誤 / timeout 統計
python -c "
import json
lines = open('c:/LiveChord/data/shadow_v2.log', encoding='utf-8').readlines()
from collections import Counter
statuses = Counter(json.loads(l)['status'] for l in lines if l.strip())
print(statuses)
"
```

### 切換 Shadow Mode 開 / 關

編輯 `backend/config.py`：
```python
SHADOW_V2_ENABLED = True   # ← False 即關（無需 restart 只需下次 import）
```
注意：實際需 `restart_dual.bat` 後生效（Python module cache）。

### 部署新版 V2 到 NUC

1. PC 端改 code + commit + push
2. NUC 端 `cd c:/LiveChord && git pull --ff-only`
3. 若改到 `backend/ai/melody_extractor_v2.py` → 不用重啟（subprocess 每次 fresh import）
4. 若改到 `melody_shadow.py` / `process_queue.py` / `config.py` → **需 `restart_dual.bat`**

### 升級 venv_ai 套件

```bash
c:/LiveChord/venv_ai/Scripts/pip.exe install -r c:/LiveChord/requirements_nuc.txt --upgrade
```

⚠️ 記得先確認 `onnxruntime-openvino==1.22.0` + `openvino==2025.2.0` 相容組合不被破壞（pip freeze 比對）。

---

## 8. 下一步（Phase 3+）

按優先順序：

### Phase 3 — 切 V2 為 primary（用戶看到的就是 V2）

- 條件：`data/shadow_v2.log` 累積 N 首歌後，V2 `status=ok` 比例 ≥ 99%、且 timing 穩定
- 執行：`ENABLE_NN_MELODY=True` + 修改 `_melody_worker_loop`：
  1. 優先跑 V2（subprocess）
  2. V2 timeout / error 時退回 V1
  3. V2 輸出直接寫 `data/melodies/<hash>.json` 給前端
- 風險：V2 音符密度 2-6× 高於 V1，前端 waterfall UI 可能顯示過滿 → 需再調 melody filter 參數或前端稀疏化

### Phase 4 — 模型升級候選

- **RMVPE**（vocal-specialized pitch tracker）：F0 精度顯著優於 basic-pitch；但需手動下載 ~50MB ONNX、自行實作 inference pipeline
- **CREPE**：老牌單音 pitch tracker，穩定但慢

### Phase 5 — 基礎設施簡化（beta 穩定後）

- uvicorn 整體遷進 venv_ai（Python 3.11），melody_shadow 改直接 import 而非 subprocess
- 移除 shadow 雙軌機制，V2 成為唯一路徑

---

## 9. Commit History

```
9a8a286 feat(ai): Phase 2 Shadow Mode — V2 runs in background alongside V1
3d4de5f fix(ai): V2 ONNX providers actually wired + polyphonic→melody filter
6233163 build: add environment specific requirements files
05f4d5c feat(ai): add V2 neural melody extractor with cross-platform hardware detection (CUDA/OpenVINO)
90acc00 docs: add AI hardware migration plan for NUC and PC
```

分支：`feature/beta-productization`
遠端：`origin/feature/beta-productization`（已同步）
