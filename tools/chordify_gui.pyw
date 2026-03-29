"""啟動器 — 執行 chordify_gui.py（無 terminal 視窗）"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
exec(open(os.path.join(os.path.dirname(__file__), 'chordify_gui.py'), encoding='utf-8').read())
