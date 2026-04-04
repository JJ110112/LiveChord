@echo off
chcp 65001 >nul
color 0A
title LiveChord Super Worker (RTX 5080 + i9)

echo ==================================================
echo         LiveChord 雙引擎批次工作站啟動器
echo           專屬客製化: RTX 5080 + i9
echo ==================================================
echo.
echo 準備啟動 24 執行緒火力全開壓榨模式...
echo 掃描路徑: Y:\ 與 Z:\ 
echo (提示: 執行前請確認已啟動 Python 環境)
echo.
timeout /t 3

:: 切換到安裝 LiveChord 的磁碟機 (W槽)
cd /d W:\

echo ==================================================
echo [階段 1/2] 準備開始掃描 Y 槽音樂庫...
echo ==================================================
python backend\batch_super_worker.py --root Y:\ --workers 24

echo.
echo ==================================================
echo [階段 2/2] Y 槽任務結束。準備開始掃描 Z 槽音樂庫...
echo ==================================================
python backend\batch_super_worker.py --root Z:\ --workers 24

echo.
echo ==================================================
echo 🎉 雙料壓榨任務全部完成！ 🎉
echo ==================================================
pause
