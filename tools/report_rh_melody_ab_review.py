"""Create a clickable Markdown checklist for RH melody A/B review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_ab_review_report import (  # noqa: E402
    DEFAULT_BASE_URL,
    build_review_rows,
    read_smoke_result_rows,
    render_review_markdown,
    write_review_markdown,
)


DEFAULT_INPUT = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_results.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_vocal_ab_review.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an RH melody A/B review Markdown checklist.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Smoke results JSONL.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Markdown report output.")
    parser.add_argument("--group", default="vocal", help="Smoke group to include.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Admin/player base URL.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing report output.")
    args = parser.parse_args()

    rows = read_smoke_result_rows(Path(args.input))
    review_rows = build_review_rows(rows, group=args.group, base_url=args.base_url)
    markdown = render_review_markdown(review_rows)
    output = write_review_markdown(markdown, Path(args.out), force=args.force_output)
    print(f"Wrote {len(review_rows)} review rows to {output}")
    return 0 if review_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
