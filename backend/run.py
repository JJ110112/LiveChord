"""LiveChord 啟動器 — 含 file logging"""
import logging
import os
from pathlib import Path

# Log 檔案放在 data/ 目錄
LOG_DIR = Path(__file__).parent.parent / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"

# 設定 logging：console + file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

# uvicorn access log 也寫入同一個檔案
for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    logging.getLogger(name).handlers = logging.root.handlers

import uvicorn

if __name__ == "__main__":
    logging.info(f"LiveChord starting — log file: {LOG_FILE}")
    uvicorn.run("main:app", host="0.0.0.0", port=8800, log_config=None)
