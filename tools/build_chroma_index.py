"""
Chroma CQT index builder for LiveChord Phase 4 reverse identifier.
Restart-safe + RAM-bounded. Lives in AppData/Local/Temp since data/tmp/ files
mysteriously vanish (likely Windows Defender false-positive on .py in that path).

Usage:
  python build_chroma_index.py --source Z:/ --index V:/Database/Chroma_Index --resume
"""
import os, sys, json, hashlib, time, argparse, gc
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np

# Force UTF-8 on stdout/stderr — C-POP has Chinese filenames that crash
# cp1252/cp950 default consoles when the script prints a FAIL line.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

SR = 22050
HOP = 512
TIME_TARGET_HZ = 2.0
FRAMES_PER_SEC_CQT = SR / HOP
DECIMATE = max(1, int(FRAMES_PER_SEC_CQT / TIME_TARGET_HZ))
AUDIO_EXTS = (".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg")
MAX_AUDIO_SEC = 600          # librosa.load duration cap (intended trim)
MAX_FILE_DURATION = 1200     # 20 min hard skip — pre-probe header, skip before decode
                              # (libsndfile occasionally over-allocates on long FLACs despite
                              # duration= param, causing 700+ MiB MemoryError in workers)


def audio_hash(path, head_bytes=64 * 1024):
    h = hashlib.md5()
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        h.update(f.read(head_bytes))
    h.update(str(size).encode())
    return h.hexdigest()[:16]


def process_one(args):
    path, out_npz = args
    try:
        # Pre-probe duration via header (fast, no decode).
        # Skips long classical pieces that trigger libsndfile OOM on decode.
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(path)
            if audio is not None and hasattr(audio, "info") and audio.info.length > MAX_FILE_DURATION:
                return {"path": path, "error": f"too_long:{audio.info.length:.0f}s"}
        except Exception:
            pass  # fall through to librosa load — it may still fail on huge files, ProcessPool handles

        import librosa
        y, _ = librosa.load(path, sr=SR, mono=True, duration=MAX_AUDIO_SEC)
        if len(y) < SR * 5:
            return {"path": path, "error": f"too_short:{len(y)/SR:.1f}s"}
        chroma = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
        if chroma.shape[1] > DECIMATE * 2:
            trim = (chroma.shape[1] // DECIMATE) * DECIMATE
            chroma = chroma[:, :trim].reshape(12, -1, DECIMATE).mean(axis=2)
        chroma = chroma.astype(np.float32)
        norms = np.linalg.norm(chroma, axis=0, keepdims=True)
        chroma_n = chroma / np.clip(norms, 1e-6, None)
        sig = np.concatenate([chroma_n.mean(axis=1), chroma_n.std(axis=1)]).astype(np.float32)
        duration = float(len(y) / SR)
        np.savez_compressed(
            out_npz,
            chroma=chroma_n,
            chroma_sig=sig,
            duration=duration,
            audio_path=os.path.abspath(path),
            filename=os.path.basename(path),
        )
        result = {
            "path": path,
            "hash": os.path.splitext(os.path.basename(out_npz))[0],
            "duration": duration,
            "frames": int(chroma_n.shape[1]),
        }
        del y, chroma, chroma_n, norms, sig
        gc.collect()
        return result
    except Exception as e:
        gc.collect()
        return {"path": path, "error": f"{type(e).__name__}:{str(e)[:120]}"}


def walk_audio(root):
    for dp, _, fns in os.walk(root):
        if "#recycle" in dp or "System Volume" in dp:
            continue
        for fn in fns:
            if fn.lower().endswith(AUDIO_EXTS):
                yield os.path.join(dp, fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-tasks-per-child", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.index, exist_ok=True)
    manifest_path = os.path.join(args.index, "manifest.jsonl")

    print(f"walking {args.source}...", flush=True)
    paths = list(walk_audio(args.source))
    print(f"  found {len(paths)} audio files", flush=True)
    if args.limit:
        paths = paths[: args.limit]

    print("hashing headers...", flush=True)
    t0 = time.time()
    jobs = []
    skipped = 0
    for p in paths:
        try:
            h = audio_hash(p)
        except Exception as e:
            print(f"  hash fail {p}: {e}", flush=True)
            continue
        out_npz = os.path.join(args.index, f"{h}.npz")
        if args.resume and os.path.exists(out_npz):
            skipped += 1
            continue
        jobs.append((p, out_npz))
    print(f"  hashing done in {time.time()-t0:.1f}s (resume skip: {skipped}, to process: {len(jobs)})", flush=True)

    from concurrent.futures import FIRST_COMPLETED, as_completed, wait
    try:
        from concurrent.futures.process import BrokenProcessPool
    except ImportError:
        BrokenProcessPool = Exception

    ok = fail = 0
    t0 = time.time()
    pool_kwargs = {"max_workers": args.workers}
    # REMOVED max_tasks_per_child to avoid Windows deadlock bug where master 
    # hangs after workers gracefully terminate. We will recycle the pool manually instead.
    
    from collections import defaultdict
    retry_counts = defaultdict(int)
    pending_jobs = list(jobs)
    total_jobs = len(jobs)


    with open(manifest_path, "a", encoding="utf-8") as manifest:
        BATCH = 200
        while pending_jobs:
            with ProcessPoolExecutor(**pool_kwargs) as ex:
                while pending_jobs:
                    batch = pending_jobs[:BATCH]
                    fut_to_job = {ex.submit(process_one, j): j for j in batch}
                    uncompleted = set(fut_to_job.keys())
                    pool_broken = False

                    while uncompleted:
                        done, uncompleted = wait(uncompleted, timeout=120.0, return_when=FIRST_COMPLETED)
                        if not done and uncompleted:
                            print(f"  [WARNING] No jobs completed in last 2 mins. Possible hang... {len(uncompleted)} remaining in batch", flush=True)

                        for fut in done:
                            j = fut_to_job[fut]
                            try:
                                r = fut.result()
                            except BrokenProcessPool as e:
                                print(f"  [ERROR] WORKER_DEAD (Pool Broken) near file: {j[0]}", flush=True)
                                retry_counts[j[0]] += 1
                                if retry_counts[j[0]] > 2:
                                    print(f"  [ERROR] File {j[0]} killed worker 3 times. Dropping it permanently.", flush=True)
                                    pending_jobs.remove(j)
                                    fail += 1
                                pool_broken = True
                                break
                            except Exception as e:
                                r = {"path": j[0], "error": f"Exception: {str(e)[:120]}"}

                            if pool_broken:
                                break

                            pending_jobs.remove(j)
                            if "error" in r:
                                fail += 1
                                print(f"  FAIL {r['path']}: {r['error']}", flush=True)
                            else:
                                ok += 1
                                manifest.write(json.dumps(r, ensure_ascii=False) + "\n")
                                manifest.flush()
                            
                            done_count = total_jobs - len(pending_jobs)
                            if done_count % 50 == 0 or not pending_jobs:
                                rate = done_count / (time.time() - t0 + 1e-6)
                                eta = len(pending_jobs) / max(rate, 1e-6)
                                print(f"  [{done_count}/{total_jobs}] ok={ok} fail={fail} {rate:.1f}/s ETA {eta:.0f}s", flush=True)

                        if pool_broken:
                            break

                    if pool_broken:
                        print("  [INFO] Restarting ProcessPoolExecutor...", flush=True)
                        break

    print(f"\nDone: ok={ok} fail={fail} skipped={skipped}  index={args.index}")


if __name__ == "__main__":
    main()
