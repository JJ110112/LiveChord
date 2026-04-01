"""直接執行 Multi-Model Consensus QA"""
import os, sys

# 載入 .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

from agent import graph, QAState

print("=" * 60)
print("  Multi-Model Consensus QA — 開始執行")
print("=" * 60)

initial_state: QAState = {
    "project_path": os.environ.get("PROJECT_ROOT", r"C:\Users\hitea\Claude\Software Design Pratice V1.01"),
    "target_files": [],
    "test_result": "", "test_passed": 0, "test_failed": 0, "test_details": "",
    "security_report": "", "security_issues": [], "security_score": 0,
    "security_opinions": [], "security_consensus": "",
    "qa_verdict": "", "qa_report": "", "fix_instructions": "",
    "qa_opinions": [], "qa_consensus": "",
    "fix_patch": "", "fix_files": [], "fix_summary": "",
    "iteration": 0, "human_approved": False,
}

config = {"configurable": {"thread_id": "qa-run-1"}}

print("\n🚀 啟動 QA Pipeline...\n")

for event in graph.stream(initial_state, config, stream_mode="updates"):
    for node_name, updates in event.items():
        print(f"\n{'─' * 50}")
        print(f"📍 節點: {node_name}")
        print(f"{'─' * 50}")

        if node_name == "test_runner":
            print(f"  結果: {updates.get('test_result', '')}")
            print(f"  通過: {updates.get('test_passed', 0)}")
            print(f"  失敗: {updates.get('test_failed', 0)}")
            if updates.get('test_details'):
                print(f"  詳情: {updates['test_details'][:300]}")

        elif node_name == "security_consensus":
            print(f"  最終安全分數: {updates.get('security_score', 0)}/100")
            opinions = updates.get("security_opinions", [])
            for op in opinions:
                print(f"  ┌ {op['model']}: {op.get('score', '?')}/100")
                print(f"  │ {op.get('reasoning', '')[:80]}")
            print(f"  └ 共識: {updates.get('security_consensus', '')[:150]}")
            issues = updates.get("security_issues", [])
            if issues:
                print(f"  確認的問題:")
                for issue in issues[:5]:
                    print(f"    • {issue}")

        elif node_name == "qa_consensus":
            print(f"  最終判定: {updates.get('qa_verdict', '')}")
            opinions = updates.get("qa_opinions", [])
            for op in opinions:
                print(f"  ┌ {op['model']}: {op.get('verdict', '?')}")
                print(f"  │ {op.get('reasoning', '')[:80]}")
            print(f"  └ 共識: {updates.get('qa_consensus', '')[:150]}")
            print(f"\n  📋 報告:\n  {updates.get('qa_report', '')[:500]}")

        elif node_name == "code_fixer":
            print(f"  修復摘要: {updates.get('fix_summary', '')}")
            print(f"  修改檔案: {updates.get('fix_files', [])}")
            print(f"\n  ⏸ 等待 Human-in-the-loop 確認...")

print("\n" + "=" * 60)
print("  QA Pipeline 結束")
print("=" * 60)
