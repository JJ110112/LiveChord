"""
Software Design Practice V1.01 — Multi-Model Consensus QA Loop

Multi-Model Consensus：安全審計 & 品管判定由多個 LLM 獨立評估，
仲裁者綜合多方意見做出最終判定，消除單一模型盲點。

流程：
  test_runner (真實執行 Playwright)
      ↓
  security_consensus (3 模型獨立審查 → 仲裁者彙整)
      ↓
  qa_consensus (3 模型獨立判定 → 仲裁者裁決)
      │
  NEEDS_FIX → code_fixer → ⏸ Human 確認 → test_runner
  PASS/FAIL → END
"""

from typing import TypedDict, List, Dict
from langgraph.graph import StateGraph, START, END
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
import json, re, subprocess, os, asyncio
from concurrent.futures import ThreadPoolExecutor

# ══════════════════════════════════════════
#  狀態定義
# ══════════════════════════════════════════
class QAState(TypedDict):
    project_path: str
    target_files: List[str]

    # 測試員
    test_result: str
    test_passed: int
    test_failed: int
    test_details: str

    # 多模型安全審計
    security_report: str
    security_issues: List[str]
    security_score: int
    security_opinions: List[Dict]    # 各模型的獨立意見
    security_consensus: str          # 共識 / 分歧摘要

    # 多模型品管
    qa_verdict: str
    qa_report: str
    fix_instructions: str
    qa_opinions: List[Dict]          # 各模型的獨立判定
    qa_consensus: str                # 共識 / 分歧摘要

    # 修復員
    fix_patch: str
    fix_files: List[str]
    fix_summary: str

    # 控制
    iteration: int
    human_approved: bool


# ══════════════════════════════════════════
#  多模型設定
# ══════════════════════════════════════════
# 仲裁者用最強模型
arbiter_model = ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)

# 評審團：3 個不同模型（或同模型不同 temperature 增加多樣性）
# 若沒有 OpenAI key，會回退到全部用 Anthropic 不同溫度
def _build_panel():
    """建立評審團：盡量使用不同供應商的模型"""
    panel = []

    # Model A: Claude Sonnet (嚴謹)
    panel.append({
        "name": "Claude-Sonnet",
        "model": ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0),
        "persona": "嚴謹保守的資安專家，傾向找出潛在風險"
    })

    # Model B: 嘗試 OpenAI，失敗則用 Claude 高溫
    try:
        if os.environ.get("OPENAI_API_KEY"):
            panel.append({
                "name": "GPT-4o-mini",
                "model": ChatOpenAI(model="gpt-4o-mini", temperature=0),
                "persona": "務實的全端工程師，關注可利用性而非理論風險"
            })
        else:
            raise ValueError("No OpenAI key")
    except Exception:
        panel.append({
            "name": "Claude-Creative",
            "model": ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0.7),
            "persona": "務實的全端工程師，關注可利用性而非理論風險"
        })

    # Model C: Claude Haiku (快速、不同視角)
    panel.append({
        "name": "Claude-Haiku",
        "model": ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0.3),
        "persona": "注重使用者體驗的 QA 工程師，關注實際操作流程的問題"
    })

    return panel


PANEL = _build_panel()

# 專案路徑
PROJECT_ROOT = os.environ.get(
    "PROJECT_ROOT",
    r"C:\Users\hitea\Claude\Software Design Pratice V1.01"
)


def _parse_json(content: str) -> dict:
    try:
        match = re.search(r'\{[\s\S]*\}', content)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {}


def _run_playwright() -> dict:
    """真正執行 npx playwright test 並解析結果"""
    try:
        result = subprocess.run(
            ["npx", "playwright", "test", "--reporter=line"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            shell=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")

        passed = 0
        failed = 0
        passed_match = re.search(r'(\d+)\s+passed', output)
        failed_match = re.search(r'(\d+)\s+failed', output)
        if passed_match:
            passed = int(passed_match.group(1))
        if failed_match:
            failed = int(failed_match.group(1))

        fail_details = []
        for line in output.split('\n'):
            if '  x ' in line or 'FAILED' in line or '✘' in line:
                fail_details.append(line.strip())

        summary = output[-2000:] if len(output) > 2000 else output

        return {
            "passed": passed,
            "failed": failed,
            "details": "\n".join(fail_details[:20]) if fail_details else "無失敗項目",
            "summary": summary,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": -1, "details": "測試執行超時（> 5 分鐘）", "summary": "TIMEOUT", "exit_code": -1}
    except Exception as e:
        return {"passed": 0, "failed": -1, "details": f"執行錯誤：{str(e)}", "summary": str(e), "exit_code": -1}


def _read_key_files() -> dict:
    """讀取關鍵檔案內容供審查"""
    files_content = {}
    key_files = ["js/api.js", "js/users.js", "firestore.rules", "functions/index.js"]
    for f in key_files:
        fpath = os.path.join(PROJECT_ROOT, f)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                files_content[f] = fh.read()[:3000]
        except Exception:
            files_content[f] = "(檔案不存在或無法讀取)"
    return files_content


def _invoke_parallel(prompts_and_models: list) -> list:
    """並行呼叫多個 LLM"""
    def _call(item):
        model, prompt = item["model"], item["prompt"]
        try:
            return model.invoke(prompt).content
        except Exception as e:
            return json.dumps({"error": str(e)})

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_call, prompts_and_models))
    return results


# ══════════════════════════════════════════
#  節點 1：QA 測試員（真正執行測試）
# ══════════════════════════════════════════
def test_runner(state: QAState) -> dict:
    result = _run_playwright()
    passed, failed, details, exit_code = result["passed"], result["failed"], result["details"], result["exit_code"]

    if exit_code == 0:
        summary = f"✅ 全部通過：{passed} passed, {failed} failed"
    elif failed == -1:
        summary = f"❌ 測試執行異常：{details}"
    else:
        summary = f"⚠️ 有失敗：{passed} passed, {failed} failed"

    return {
        "test_passed": passed,
        "test_failed": failed,
        "test_result": summary,
        "test_details": details,
    }


# ══════════════════════════════════════════
#  節點 2：多模型安全審計（Consensus）
# ══════════════════════════════════════════
def security_consensus(state: QAState) -> dict:
    """3 個模型獨立審查 → 仲裁者彙整"""

    files_content = _read_key_files()
    test_passed = state.get("test_passed", 0)
    test_failed = state.get("test_failed", 0)

    base_context = f"""## 專案背景
- 學校內部練習系統（非金融/醫療）
- Firebase API Key 暴露在前端是正常的（Firebase 用 Security Rules 保護）
- Anonymous Auth 是刻意設計
- 測試結果：{test_passed} passed / {test_failed} failed

## 關鍵檔案
### firestore.rules
```
{files_content.get('firestore.rules', 'N/A')}
```

### functions/index.js（前 3000 字元）
```javascript
{files_content.get('functions/index.js', 'N/A')}
```

### js/api.js（前 2000 字元）
```javascript
{files_content.get('js/api.js', 'N/A')[:2000]}
```"""

    # Phase 1: 3 個模型並行獨立審查
    calls = []
    for p in PANEL:
        prompt = f"""你是 {p['persona']}。

{base_context}

## 評分面向（各 0-10 分，滿分 60 → 換算百分制）
1. 資料寫入保護 — Firestore Rules 是否阻止前端直寫？
2. Cloud Function 驗證 — auth + 欄位驗證 + 防連刷？
3. XSS 防護 — innerHTML 都有 escapeHTML？
4. 排行榜保護 — rebuildLeaderboard 有 rate limit？
5. 名冊管理 — saveRosterSecure 需教師密碼？
6. 前端防弊 — 防貼上 + 時間限制？

回覆 JSON：
{{"security_score": 數字(0-100), "security_issues": ["只列實際可利用的問題"], "reasoning": "你的推理過程(100字)"}}"""
        calls.append({"model": p["model"], "prompt": prompt, "name": p["name"]})

    raw_results = _invoke_parallel(calls)

    # 收集各模型意見
    opinions = []
    all_issues = []
    scores = []
    for i, raw in enumerate(raw_results):
        data = _parse_json(raw)
        opinion = {
            "model": PANEL[i]["name"],
            "score": data.get("security_score", 50),
            "issues": data.get("security_issues", []),
            "reasoning": data.get("reasoning", ""),
        }
        opinions.append(opinion)
        scores.append(opinion["score"])
        all_issues.extend(opinion["issues"])

    # Phase 2: 仲裁者綜合判定
    arbiter_prompt = f"""你是首席安全官，以下是 3 位審計員對同一系統的獨立評估：

{json.dumps(opinions, ensure_ascii=False, indent=2)}

## 你的任務
1. 分析 3 位審計員的共識與分歧
2. 對有分歧的議題做出最終裁決
3. 去除誤報（false positive），保留真正可利用的問題
4. 給出最終安全分數

回覆 JSON：
{{"security_score": 最終分數(0-100), "security_issues": ["最終確認的問題清單"], "security_report": "共識摘要(200字)", "consensus": "共識/分歧分析(100字)"}}"""

    arbiter_data = _parse_json(arbiter_model.invoke(arbiter_prompt).content)

    return {
        "security_score": arbiter_data.get("security_score", int(sum(scores) / len(scores)) if scores else 50),
        "security_issues": arbiter_data.get("security_issues", list(set(all_issues))),
        "security_report": arbiter_data.get("security_report", "審查完成"),
        "security_opinions": opinions,
        "security_consensus": arbiter_data.get("consensus", ""),
    }


# ══════════════════════════════════════════
#  節點 3：多模型品管判定（Consensus）
# ══════════════════════════════════════════
def qa_consensus(state: QAState) -> dict:
    """3 個模型獨立判定 PASS/NEEDS_FIX/FAIL → 仲裁者裁決"""

    iteration = state.get("iteration", 0) + 1

    # 超過最大迭代直接 FAIL
    if iteration >= 3:
        return {
            "qa_verdict": "FAIL",
            "qa_report": f"❌ 已達最大迭代次數 ({iteration})，仍有問題未解決。\n"
                         f"測試：{state.get('test_passed', 0)} passed / {state.get('test_failed', 0)} failed\n"
                         f"安全：{state.get('security_score', 0)}/100",
            "fix_instructions": "",
            "qa_opinions": [],
            "qa_consensus": "已達迭代上限",
            "iteration": iteration,
        }

    qa_context = f"""## QA 數據
- 測試結果：{state.get('test_passed', 0)} passed / {state.get('test_failed', 0)} failed
- 測試摘要：{state.get('test_result', '')}
- 失敗詳情：{state.get('test_details', '')}
- 安全分數：{state.get('security_score', 0)}/100
- 安全問題：{json.dumps(state.get('security_issues', []), ensure_ascii=False)}
- 安全共識：{state.get('security_consensus', '')}
- 目前迭代：第 {iteration} 輪"""

    # Phase 1: 3 個模型並行獨立判定
    calls = []
    for p in PANEL:
        prompt = f"""你是 {p['persona']}，擔任品管評審。

{qa_context}

## 判定標準
- PASS：測試失敗 ≤ 1 且安全分數 ≥ 70，系統可上線
- NEEDS_FIX：有問題但可修復，需要具體修復指示
- FAIL：嚴重問題無法在合理時間內修復

回覆 JSON：
{{"verdict": "PASS/NEEDS_FIX/FAIL", "reasoning": "判定理由(100字)", "fix_instructions": "若 NEEDS_FIX，列出修復步驟"}}"""
        calls.append({"model": p["model"], "prompt": prompt, "name": p["name"]})

    raw_results = _invoke_parallel(calls)

    opinions = []
    verdicts = []
    for i, raw in enumerate(raw_results):
        data = _parse_json(raw)
        opinion = {
            "model": PANEL[i]["name"],
            "verdict": data.get("verdict", "NEEDS_FIX"),
            "reasoning": data.get("reasoning", ""),
            "fix_instructions": data.get("fix_instructions", ""),
        }
        opinions.append(opinion)
        verdicts.append(opinion["verdict"])

    # Phase 2: 仲裁者做最終裁決
    arbiter_prompt = f"""你是品管總監，以下是 3 位品管評審的獨立判定：

{json.dumps(opinions, ensure_ascii=False, indent=2)}

## 原始數據
{qa_context}

## 裁決規則
- 如果 3 位都判 PASS → PASS
- 如果 2 位以上判 NEEDS_FIX 或 FAIL → 取多數意見
- 如果意見分歧（如 1 PASS + 1 NEEDS_FIX + 1 FAIL）→ 偏保守，取 NEEDS_FIX
- 合併所有 fix_instructions 去重

回覆 JSON：
{{"verdict": "PASS/NEEDS_FIX/FAIL", "qa_report": "最終報告(200字)", "fix_instructions": "合併的修復指示", "consensus": "投票結果與分歧分析"}}"""

    arbiter_data = _parse_json(arbiter_model.invoke(arbiter_prompt).content)

    # 回退：如果仲裁者解析失敗，用多數投票
    final_verdict = arbiter_data.get("verdict", "")
    if final_verdict not in ("PASS", "NEEDS_FIX", "FAIL"):
        from collections import Counter
        vote = Counter(verdicts).most_common(1)[0][0]
        final_verdict = vote

    return {
        "qa_verdict": final_verdict,
        "qa_report": arbiter_data.get("qa_report",
            f"投票：{verdicts} → {final_verdict}"),
        "fix_instructions": arbiter_data.get("fix_instructions", ""),
        "qa_opinions": opinions,
        "qa_consensus": arbiter_data.get("consensus", f"投票：{verdicts}"),
        "iteration": iteration,
    }


# ══════════════════════════════════════════
#  節點 4：修復員
# ══════════════════════════════════════════
def code_fixer(state: QAState) -> dict:
    instructions = state.get("fix_instructions", "")
    test_details = state.get("test_details", "")
    security_issues = state.get("security_issues", [])

    prompt = f"""你是資深前端工程師，根據 QA 報告修復程式碼。

## 專案路徑：{PROJECT_ROOT}
## 技術：純 HTML/CSS/JS + Firebase + Cloud Functions

## 修復指示（多位審查員共識）
{instructions}

## 測試失敗詳情
{test_details}

## 安全問題（已去除誤報）
{security_issues}

## 規則
- 只修復問題，不改功能
- 產生 unified diff 格式的修復
- 列出會修改的檔案

回覆 JSON：
{{"fix_patch": "diff 或程式碼", "fix_files": ["檔案"], "fix_summary": "摘要"}}"""

    data = _parse_json(arbiter_model.invoke(prompt).content)
    return {
        "fix_patch": data.get("fix_patch", "無法產生修復"),
        "fix_files": data.get("fix_files", []),
        "fix_summary": data.get("fix_summary", "修復方案已產生"),
        "human_approved": False,
    }


# ══════════════════════════════════════════
#  路由
# ══════════════════════════════════════════
def route_after_qa(state: QAState) -> str:
    verdict = state.get("qa_verdict", "FAIL")
    if verdict == "NEEDS_FIX":
        return "code_fixer"
    return "end"


def route_after_human_review(state: QAState) -> str:
    if state.get("human_approved", False):
        return "test_runner"
    return "end"


# ══════════════════════════════════════════
#  構建圖
# ══════════════════════════════════════════
workflow = StateGraph(QAState)

workflow.add_node("test_runner", test_runner)
workflow.add_node("security_consensus", security_consensus)
workflow.add_node("qa_consensus", qa_consensus)
workflow.add_node("code_fixer", code_fixer)

workflow.set_entry_point("test_runner")
workflow.add_edge("test_runner", "security_consensus")
workflow.add_edge("security_consensus", "qa_consensus")

workflow.add_conditional_edges(
    "qa_consensus",
    route_after_qa,
    {"code_fixer": "code_fixer", "end": END}
)

workflow.add_conditional_edges(
    "code_fixer",
    route_after_human_review,
    {"test_runner": "test_runner", "end": END}
)

graph = workflow.compile(interrupt_after=["code_fixer"])
