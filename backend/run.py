"""LiveChord 啟動器 — 含每日輪替 file logging"""
import logging
import logging.handlers
from pathlib import Path

# Log 檔案放在 data/ 目錄
LOG_DIR = Path(__file__).parent.parent / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"

# 設定 logging：console + 每日輪替檔案（保留 7 天）
# 檔案只記錄 ERROR 以上
file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE, when="midnight", backupCount=7, encoding="utf-8"
)
file_handler.suffix = "%Y-%m-%d"
file_handler.setLevel(logging.ERROR)

# Console 維持 INFO（看得到 access log）
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(fmt)
console_handler.setFormatter(fmt)

logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])

for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    logging.getLogger(name).handlers = logging.root.handlers

import uvicorn

if __name__ == "__main__":
    logging.info(f"LiveChord starting — log: {LOG_FILE} (7-day rotation)")
    uvicorn.run("main:app", host="0.0.0.0", port=8800, log_config=None)
