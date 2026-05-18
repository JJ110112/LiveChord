"""LiveChord 啟動器 — logging 由 main.py 統一管理（避免雙 handler 競爭 server.log）"""
import logging
import os

import uvicorn

if __name__ == "__main__":
    # run.py is the LAN/personal launcher for port 8800. Pin the default before
    # main.py loads .env so a public-mode review .env cannot hide the NAS library.
    os.environ.setdefault("LIVECHORD_MODE", "personal")
    from config import get_deployment_mode
    mode = get_deployment_mode()
    logging.info(f"LiveChord starting — mode={mode} port=8800")
    uvicorn.run("main:app", host="0.0.0.0", port=8800, log_config=None)
