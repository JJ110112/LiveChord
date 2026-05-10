"""
Catch FastAPI route handlers declared `async def` that never `await` anything.

This pattern has bitten LiveChord three times — `/api/ai/evaluate` (commit
2b503b4), `/api/ai/retrain` (commit 7cc3fd0), and `/api/ai/sections`
(commit 96aba1f). Each handler was `async def` while the body did
synchronous heavy work (file I/O, librosa, PyTorch inference). FastAPI
runs `async def` handlers ON the event loop instead of dispatching to the
thread pool, so a single in-flight request blocks every other request
including the page reload itself, surfacing as "no response".

Rule (per CLAUDE.md `feedback_async_def`): an `async def` route handler
must have at least one `await` somewhere in its body. If it doesn't, the
async-ness buys nothing and the handler must be `def`.

Usage:
    python tools/lint_async_handlers.py             # exit 0 clean, 1 NEW violations
    python tools/lint_async_handlers.py --paths backend/ai_api.py   # one file
    python tools/lint_async_handlers.py --update-baseline   # rewrite allowlist
    python tools/lint_async_handlers.py --no-baseline       # report ALL, no allowlist

Baseline: `tools/async_handler_baseline.txt` lists `path::funcname` entries
that are known offenders at adoption time. The lint silences these so CI
fails only on NEW violations. To clear an entry: convert the handler to
`def` (or add a real `await`), then either re-run with --update-baseline
or hand-edit the baseline file. The baseline keys on function name (not
line number) so unrelated edits don't churn it.

Heuristic limits:
  - Detects only static decorators of the form `@router.<verb>(...)`,
    `@app.<verb>(...)`, `@<name>.<verb>(...)`. WebSocket handlers
    (`@router.websocket`) are skipped — they legitimately need `async`.
  - `await` anywhere in the function body counts (including inside `if`,
    `try`, comprehensions, nested functions). Nested `async def`s buried
    inside the route are ignored.
  - False-negative cases that this lint won't catch on purpose:
      * Handler awaits a no-op coroutine just to silence the lint.
        That's a code-review concern, not a lint concern.
      * Sync handler that loads a 10-second model on the request path.
        Different bug class — covered by future runtime middleware.
"""
from __future__ import annotations

import ast
import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ROUTE_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _is_route_decorator(dec: ast.expr) -> bool:
    """True iff dec looks like @<router>.<verb>(...) for an HTTP verb."""
    call = dec if isinstance(dec, ast.Call) else None
    target = call.func if call is not None else dec
    if not isinstance(target, ast.Attribute):
        return False
    return target.attr in ROUTE_VERBS


def _has_await(node: ast.AST) -> bool:
    """True iff `node`'s body contains any Await/AsyncFor/AsyncWith.

    Ignores nested `async def` / `def` — `await` inside a helper closure
    doesn't make the outer handler awaitable.
    """
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef)):
            # Skip subtree rooted at nested function — ast.walk yields it
            # but we don't want to descend into it. Pragmatic fix:
            # re-walk top-level body instead of using ast.walk blindly.
            pass
    # Re-walk the body explicitly with our own depth control.
    return _body_has_await(node.body)


def _body_has_await(body: Iterable[ast.stmt]) -> bool:
    for stmt in body:
        for descendant in _walk_skip_nested_funcs(stmt):
            if isinstance(descendant, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
                return True
    return False


def _walk_skip_nested_funcs(node: ast.AST):
    """Like ast.walk, but doesn't recurse into nested def/async def bodies."""
    todo = [node]
    while todo:
        cur = todo.pop()
        yield cur
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and cur is not node:
            continue
        for child in ast.iter_child_nodes(cur):
            todo.append(child)


def find_violations(path: Path) -> List[Tuple[int, str]]:
    """Return [(lineno, function_name), ...] for offending handlers in `path`."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        print(f"  ! parse error in {path}: {e}", file=sys.stderr)
        return []

    violations: List[Tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            if any(_is_route_decorator(d) for d in node.decorator_list):
                if not _body_has_await(node.body):
                    violations.append((node.lineno, node.name))
            # Don't descend into nested defs.

        # Don't recurse into sync defs either — route handlers are top-level.
        def visit_FunctionDef(self, node):
            self.generic_visit(node)

    Visitor().visit(tree)
    return violations


BASELINE_FILE = "tools/async_handler_baseline.txt"


def _baseline_path(repo_root: Path) -> Path:
    return repo_root / BASELINE_FILE


def _load_baseline(repo_root: Path) -> set:
    p = _baseline_path(repo_root)
    if not p.is_file():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def _violation_key(rel_path: Path, funcname: str) -> str:
    # Forward slashes for portability across the Win/Linux split (NUC + VPS).
    return f"{rel_path.as_posix()}::{funcname}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1].strip())
    ap.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Files or directories to scan. Default: backend/",
    )
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite tools/async_handler_baseline.txt with the current scan.",
    )
    ap.add_argument(
        "--no-baseline",
        action="store_true",
        help="Ignore baseline; report every violation. Useful for triage.",
    )
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    targets: List[Path] = []
    if args.paths:
        for p in args.paths:
            pp = Path(p)
            if not pp.is_absolute():
                pp = repo_root / pp
            targets.append(pp)
    else:
        targets.append(repo_root / "backend")

    files: List[Path] = []
    for t in targets:
        if t.is_dir():
            files.extend(sorted(t.rglob("*.py")))
        elif t.is_file() and t.suffix == ".py":
            files.append(t)
        else:
            print(f"  ! skipping {t}: not a .py file or directory", file=sys.stderr)

    all_findings: List[Tuple[Path, int, str, str]] = []  # (rel, lineno, name, key)
    for f in files:
        if "__pycache__" in f.parts or ".venv" in f.parts or "venv" in f.parts:
            continue
        vios = find_violations(f)
        if not vios:
            continue
        try:
            rel = f.relative_to(repo_root) if f.is_absolute() else f
        except ValueError:
            # File lives outside the repo (e.g. ad-hoc test path). Use as-is.
            rel = f
        for lineno, name in vios:
            all_findings.append((rel, lineno, name, _violation_key(rel, name)))

    if args.update_baseline:
        keys = sorted({k for *_, k in all_findings})
        body = (
            "# Baseline allowlist for tools/lint_async_handlers.py.\n"
            "# Each line: <relative-path>::<function-name>.\n"
            "# Generated by --update-baseline. Hand-edit to remove entries\n"
            "# (after fixing the handler) or to add justified exceptions.\n"
            "#\n"
            "# Adding a new line is a regression — fix the handler instead.\n"
            "\n"
            + "\n".join(keys) + "\n"
        )
        _baseline_path(repo_root).write_text(body, encoding="utf-8")
        print(f"Wrote {len(keys)} entries to {BASELINE_FILE}")
        return 0

    baseline = set() if args.no_baseline else _load_baseline(repo_root)

    new_violations: List[Tuple[Path, int, str]] = []
    silenced = 0
    for rel, lineno, name, key in all_findings:
        if key in baseline:
            silenced += 1
            continue
        new_violations.append((rel, lineno, name))

    for rel, lineno, name in new_violations:
        print(f"{rel}:{lineno}: async def {name}(...) has no await — should be `def`")

    if new_violations:
        suffix = f" ({silenced} known offender(s) silenced via baseline)" if silenced else ""
        print(
            f"\n{len(new_violations)} NEW violation(s){suffix}.\n"
            f"`async def` route handlers MUST `await` something; otherwise change to `def` "
            f"so FastAPI dispatches to the thread pool. See CLAUDE.md feedback_async_def.\n"
            f"If this is intentional, run with --update-baseline (with reviewer sign-off).",
            file=sys.stderr,
        )
        return 1

    msg = f"OK — scanned {len(files)} file(s), no NEW violations."
    if silenced:
        msg += f" ({silenced} known offender(s) silenced via baseline; shrink over time.)"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
