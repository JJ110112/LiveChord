"""LiveChord — 背景掃描音樂庫（命令列工具）

用法:
  python scan.py                 增量掃描（預設）
  python scan.py full            全部重掃
  python scan.py incremental     增量掃描
"""

import sys
import time
import json
import urllib.request

BASE = "http://localhost:8800"


def api_get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode())


def api_post(path):
    req = urllib.request.Request(BASE + path, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    if mode not in ("full", "incremental"):
        print(f"未知模式: {mode}，使用 incremental")
        mode = "incremental"

    print("=" * 50)
    print(f"  LiveChord 音樂庫掃描")
    print(f"  模式: {mode}")
    print(f"  時間: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 檢查伺服器
    try:
        api_get("/api/library/stats")
    except Exception:
        print("\n[錯誤] 伺服器未啟動！請先執行 start.bat")
        sys.exit(1)

    # 啟動掃描
    print(f"\n[1/3] 啟動 {mode} 掃描...")
    result = api_post(f"/api/library/scan?mode={mode}")
    if not result.get("ok"):
        print(f"  {result.get('message', '啟動失敗')}")
        if result.get("progress"):
            print(f"  目前進度: {result['progress']} 首")
        # 如果已在掃描中，繼續輪詢
        if not result.get("ok") and "進行中" in result.get("message", ""):
            pass
        else:
            sys.exit(1)

    # 輪詢進度
    print("[2/3] 掃描進行中...\n")
    last_progress = -1
    while True:
        time.sleep(2)
        try:
            st = api_get("/api/library/scan/status")
        except Exception as e:
            print(f"  [警告] 無法取得狀態: {e}")
            continue

        p = st["progress"]
        if p != last_progress:
            new = st["new_tracks"]
            upd = st["updated_tracks"]
            dirs = st["total_dirs"]
            print(f"  已掃描: {p:>6} 首 | 目錄: {dirs} | 新增: {new} | 更新: {upd}")
            last_progress = p

        if not st["running"]:
            break

    # 完成
    print(f"\n[3/3] 掃描完成！")
    print(f"  總曲目:  {st['progress']}")
    print(f"  新增:    {st['new_tracks']}")
    print(f"  更新:    {st['updated_tracks']}")
    print(f"  刪除:    {st.get('deleted_tracks', 0)}")
    print(f"  開始:    {st['started_at']}")
    print(f"  結束:    {st['finished_at']}")
    if st.get("error"):
        print(f"  錯誤:    {st['error']}")
    print("\n" + "=" * 50)
    print("  掃描完成！可回到瀏覽器使用搜尋功能")
    print("=" * 50)


if __name__ == "__main__":
    main()
