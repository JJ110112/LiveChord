"""LiveChord 啟動器 — logging 由 main.py 統一管理（避免雙 handler 競爭 server.log）"""
import logging

import uvicorn

if __name__ == "__main__":
    from config import get_deployment_mode
    mode = get_deployment_mode()
    logging.info(f"LiveChord starting — mode={mode} port=8800")
    uvicorn.run("main:app", host="0.0.0.0", port=8800, log_config=None)
