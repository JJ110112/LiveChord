"""Trigger tier2 backup on NUC 8800 and poll until done.

LAN-bypass admin auth — works from any 192.168.x.x machine without token.

Usage:
    python scripts/trigger_nuc_backup.py [--tier tier2] [--nuc http://192.168.50.6:8800]

Exit codes:
    0  backup completed ok
    1  pre-flight / HTTP error
    2  backup ran but reported ok=False
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _get(url: str, timeout: float = 10.0) -> dict:
    return json.loads(urllib.request.urlopen(url, timeout=timeout).read())


def _post(url: str, body: dict, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout).read().decode(errors="replace")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", default="tier2", choices=["tier1", "tier2", "tier3"])
    p.add_argument("--nuc", default="http://192.168.50.6:8800")
    p.add_argument("--poll-interval", type=float, default=15.0)
    args = p.parse_args()

    nuc = args.nuc.rstrip("/")

    # Health probe.
    try:
        urllib.request.urlopen(f"{nuc}/api/config/public", timeout=5).read()
    except Exception as e:
        print(f"ERROR: NUC unreachable at {nuc} — {e}")
        print("  Run restart_dual.bat on NUC and wait ~15s, then retry.")
        return 1

    # Already-running guard.
    try:
        state = _get(f"{nuc}/api/admin/backup/status")
    except Exception as e:
        print(f"ERROR: cannot read backup status — {e}")
        return 1

    if state.get("running"):
        print(
            f"WARN: backup already running (tier={state.get('tier')}, "
            f"item={state.get('current_item')}). Polling existing run."
        )
    else:
        try:
            resp = _post(f"{nuc}/api/admin/backup/run", {"tier": args.tier})
            print(f"triggered {args.tier}: {resp}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            print(f"FAIL: HTTP {e.code} — {body}")
            return 1
        except Exception as e:
            print(f"FAIL: {e}")
            return 1

    print(
        f"Polling /api/admin/backup/status every {args.poll_interval:.0f}s. "
        "Ctrl+C is safe — backup continues on NUC."
    )
    last_item = None
    while True:
        time.sleep(args.poll_interval)
        try:
            s = _get(f"{nuc}/api/admin/backup/status")
        except Exception as e:
            print(f"  (status poll failed: {e})")
            continue
        if not s.get("running"):
            last = s.get("last_result") or {}
            ok = bool(last.get("ok"))
            print(
                f"=== backup done — ok={ok} bytes={last.get('bytes_total')} "
                f"items={last.get('items_total')} dest={last.get('dest')} ==="
            )
            for err in last.get("errors") or []:
                print(f"  ERROR: {err}")
            return 0 if ok else 2
        item = s.get("current_item")
        prog = s.get("progress_items")
        total = s.get("total_items")
        marker = "  item:" if item != last_item else "  ... still on"
        print(f"{marker} {item}  ({prog}/{total})")
        last_item = item


if __name__ == "__main__":
    sys.exit(main())
