"""Sweep zh-TW fallback text in HTML files.

For every `data-i18n="key">CONTENT</tag>`, `data-i18n-html="key">CONTENT</tag>`,
and `data-i18n-attr="attr=key,..."` pattern, replace the inline fallback
(content / attribute value) with the English string from `en.json` when:
  1. The key exists in en.json
  2. The current value contains CJK characters

Why: the EN-default site (livechord.org) shows the inline fallback for
~100ms before i18n.js patches the DOM. Chinese fallbacks leak into the
EN UX during that window. Replacing fallbacks with EN keeps the flash
correct; the data-i18n patcher will still swap to zh-TW for users who
pick that language.

Run from repo root:  python scripts/sweep_zhtw_fallback.py
Use --dry-run to print stats only.
"""

import argparse
import json
import re
import sys
from pathlib import Path


CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def _load_dict(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _has_cjk(s: str) -> bool:
    return bool(CJK.search(s))


def _replace_text_content(html: str, en: dict, stats: dict) -> str:
    """Replace `data-i18n="k">CJK</...>` content with en[k]."""
    pat = re.compile(r'(data-i18n="([^"]+)">)([^<]*)(<)', re.U)

    def sub(m: re.Match) -> str:
        prefix, key, content, lt = m.groups()
        if key not in en or not _has_cjk(content):
            return m.group(0)
        new_content = str(en[key])
        # HTML-escape minimal characters that could break parse if present.
        new_content = (
            new_content.replace("&", "&amp;").replace("<", "&lt;")
        )
        # Preserve a leading newline + indent that some pretty-printed HTML uses
        # between the opening tag and the text (rare, but defensive).
        if content.startswith("\n"):
            new_content = "\n" + new_content
        stats["text"] += 1
        return f"{prefix}{new_content}{lt}"

    return pat.sub(sub, html)


def _replace_html_content(html: str, en: dict, stats: dict) -> str:
    """Replace `data-i18n-html="k">CJK</...>` content with en[k] verbatim
    (no entity escaping — values are intentionally raw HTML)."""
    pat = re.compile(r'(data-i18n-html="([^"]+)">)([^<]*)(<)', re.U)

    def sub(m: re.Match) -> str:
        prefix, key, content, lt = m.groups()
        if key not in en or not _has_cjk(content):
            return m.group(0)
        new_content = str(en[key])
        stats["html"] += 1
        return f"{prefix}{new_content}{lt}"

    return pat.sub(sub, html)


_ATTR_NAMES_OK = {"placeholder", "title", "aria-label", "alt", "value"}


def _replace_attr_fallbacks(html: str, en: dict, stats: dict) -> str:
    """For each `data-i18n-attr="placeholder=k,title=k2"`, find the
    surrounding tag and replace each named attribute's value with en[k]
    when the current attr value contains CJK.
    """
    # Walk attribute references one at a time. Each iteration may mutate the
    # html, so we re-scan from the start of the modified tag every time.
    out = html
    # Find every data-i18n-attr occurrence by left-to-right offset.
    pos = 0
    while True:
        m = re.search(r'data-i18n-attr="([^"]+)"', out[pos:])
        if not m:
            break
        attr_decl_start = pos + m.start()
        attr_decl_end = pos + m.end()
        attr_decl_value = m.group(1)

        # Find the enclosing tag's opening '<' and closing '>'.
        tag_start = out.rfind("<", 0, attr_decl_start)
        # Walk forward looking for '>' that isn't inside an attribute value.
        tag_end = _find_tag_end(out, attr_decl_end)
        if tag_start < 0 or tag_end < 0:
            pos = attr_decl_end
            continue

        tag_text = out[tag_start:tag_end + 1]
        new_tag_text = tag_text

        for pair in attr_decl_value.split(","):
            if "=" not in pair:
                continue
            attr_name, key = (x.strip() for x in pair.split("=", 1))
            if attr_name not in _ATTR_NAMES_OK or key not in en:
                continue
            new_val = str(en[key])
            # Replace `attr_name="..."` within the tag.
            attr_pat = re.compile(
                rf'(\s{re.escape(attr_name)}=")([^"]*)(")'
            )
            am = attr_pat.search(new_tag_text)
            if am and _has_cjk(am.group(2)):
                escaped = (
                    new_val.replace("&", "&amp;")
                          .replace('"', "&quot;")
                )
                new_tag_text = (
                    new_tag_text[:am.start()]
                    + am.group(1) + escaped + am.group(3)
                    + new_tag_text[am.end():]
                )
                stats["attr"] += 1

        if new_tag_text != tag_text:
            out = out[:tag_start] + new_tag_text + out[tag_end + 1:]
            # Recompute position past the (potentially resized) tag.
            pos = tag_start + len(new_tag_text)
        else:
            pos = attr_decl_end

    return out


def _find_tag_end(s: str, start: int) -> int:
    """Find index of the first '>' from `start` that closes the current tag.
    Skips over '>' that occur inside double-quoted attribute values.
    """
    i = start
    in_quote = False
    while i < len(s):
        c = s[i]
        if c == '"':
            in_quote = not in_quote
        elif c == ">" and not in_quote:
            return i
        i += 1
    return -1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print stats and per-file changed-count, no writes")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    fe = repo_root / "frontend"
    en = _load_dict(fe / "i18n" / "en.json")

    grand = {"text": 0, "html": 0, "attr": 0}
    per_file = []

    for html_file in sorted(fe.glob("*.html")):
        original = html_file.read_text(encoding="utf-8")
        stats = {"text": 0, "html": 0, "attr": 0}
        new = _replace_text_content(original, en, stats)
        new = _replace_html_content(new, en, stats)
        new = _replace_attr_fallbacks(new, en, stats)

        for k in grand:
            grand[k] += stats[k]
        if any(stats.values()):
            per_file.append((html_file.name, stats, len(original), len(new)))
            if not args.dry_run and new != original:
                html_file.write_text(new, encoding="utf-8", newline="\n")

    print(f"=== {'DRY RUN' if args.dry_run else 'WROTE'} ===")
    for name, stats, before, after in per_file:
        delta = after - before
        sign = "+" if delta >= 0 else ""
        print(
            f"  {name:<22}  text={stats['text']:>3}  "
            f"html={stats['html']:>2}  attr={stats['attr']:>3}  "
            f"size {before}->{after} ({sign}{delta})"
        )
    print()
    print(f"  TOTAL  text={grand['text']}  "
          f"html={grand['html']}  attr={grand['attr']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
