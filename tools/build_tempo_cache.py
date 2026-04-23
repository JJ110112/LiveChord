"""
Build V:/Database/Chroma_Index/tempo_cache.json = {hash: BPM} for every
entry in manifest.jsonl. Used by identify_midi.py's tempo gate; without
this cache the identifier just skips the tempo check (neutral).

One-time side-pass: at ~2s per audio file over Z:/ network, 22k files ≈
9 hours. Resume-safe — restart picks up where it left off.

Usage:
  python build_tempo_cache.py --index V:/Database/Chroma_Index --workers 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    from concurrent.futures.process import BrokenProcessPool
except ImportError:
    BrokenProcessPool = Exception

SR = 22050
TEMPO_SAMPLE_SEC = 60.0  # load only this much audio — plenty for beat tracking


def estimate_tempo(path: str) -> float:
    import librosa
    y, _ = librosa.load(path, sr=SR, mono=True, duration=TEMPO_SAMPLE_SEC)
    if len(y) < SR * 10:
        return 0.0
    onset_env = librosa.onset.onset_strength(y=y, sr=SR)
    tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=SR)
    if hasattr(tempo, "__len__") and len(tempo) > 0:
        return float(tempo[0])
    return float(tempo)


def _process_one(args: tuple) -> dict:
    h, path = args
    try:
        bpm = estimate_tempo(path)
        if not np.isfinite(bpm) or bpm <= 0:
            return {"hash": h, "error": "invalid_bpm"}
        return {"hash": h, "bpm": bpm}
    except Exception as e:
        return {"hash": h, "error": f"{type(e).__name__}:{str(e)[:100]}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True, help="V:/Database/Chroma_Index")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    manifest_path = os.path.join(args.index, "manifest.jsonl")
    cache_path = os.path.join(args.index, "tempo_cache.json")
    log_path = os.path.join(args.index, "tempo_cache_errors.log")

    existing: dict[str, float] = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                existing = {k: float(v) for k, v in json.load(f).items()}
            print(f"  resume: loaded {len(existing)} existing tempo entries", flush=True)
        except Exception as e:
            print(f"  warn: couldn't parse existing cache ({e}); starting fresh", flush=True)
            existing = {}

    jobs: list[tuple[str, str]] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                h = r.get("hash"); p = r.get("path")
                if h and p and h not in existing:
                    jobs.append((h, p))
            except Exception:
                pass
    if args.limit and args.limit < len(jobs):
        jobs = jobs[: args.limit]
    print(f"  to process: {len(jobs)}", flush=True)
    if not jobs:
        print("nothing to do."); return

    # Atomic-ish save: write to .tmp then os.replace
    def _save(d: dict[str, float]):
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, cache_path)

    ok = fail = 0
    t0 = time.time()
    save_every = 500
    cache = dict(existing)
    log_fh = open(log_path, "a", encoding="utf-8")

    from collections import defaultdict
    retry_counts: dict[str, int] = defaultdict(int)
    pending_jobs = list(jobs)
    total_jobs = len(jobs)

    while pending_jobs:
        try:
            ex = ProcessPoolExecutor(max_workers=args.workers)
        except Exception as e:
            print(f"  [ERROR] pool start failed: {e}", flush=True); break
        with ex:
            BATCH = 200
            while pending_jobs:
                batch = pending_jobs[:BATCH]
                fut_to_job = {ex.submit(_process_one, j): j for j in batch}
                uncompleted = set(fut_to_job)
                pool_broken = False
                while uncompleted:
                    done, uncompleted = wait(uncompleted, timeout=180.0,
                                             return_when=FIRST_COMPLETED)
                    if not done and uncompleted:
                        print(f"  [WARN] no completion in 3 min. {len(uncompleted)} pending", flush=True)
                    for fut in done:
                        j = fut_to_job[fut]
                        try:
                            r = fut.result()
                        except BrokenProcessPool:
                            retry_counts[j[0]] += 1
                            if retry_counts[j[0]] > 2:
                                pending_jobs.remove(j); fail += 1
                            pool_broken = True; break
                        except Exception as e:
                            r = {"hash": j[0], "error": f"{type(e).__name__}:{str(e)[:100]}"}
                        if pool_broken: break
                        pending_jobs.remove(j)
                        if "bpm" in r:
                            cache[r["hash"]] = r["bpm"]; ok += 1
                        else:
                            fail += 1
                            log_fh.write(f"{r['hash']}\t{r.get('error','?')}\n"); log_fh.flush()
                        done_count = total_jobs - len(pending_jobs)
                        if done_count and done_count % save_every == 0:
                            _save(cache)
                            rate = done_count / (time.time() - t0 + 1e-6)
                            eta = len(pending_jobs) / max(rate, 1e-6)
                            print(f"  [{done_count}/{total_jobs}] ok={ok} fail={fail} "
                                  f"{rate:.1f}/s ETA {eta:.0f}s", flush=True)
                    if pool_broken: break
                if pool_broken:
                    print("  [INFO] pool died, restarting ...", flush=True); break

    _save(cache)
    log_fh.close()
    print(f"\nDone: ok={ok} fail={fail}  cache={cache_path}  errors={log_path}")


if __name__ == "__main__":
    main()
