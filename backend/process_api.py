"""Process API — 上傳音檔 和弦偵測"""

import hashlib
import os
import queue
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from auth_api import get_current_user, get_admin_user, get_user_or_anon, is_anon
from config import is_beta_mode, is_public_mode, is_personal_mode
from process_queue import (
    ProcessJob, JobStatus, TMP_DIR, COVERS_DIR,
    submit_job, get_job, generate_job_id,
    check_quota, get_user_daily_count, get_audit_log, get_user_audit_log,
    delete_audit_entries, delete_user_audit_entry,
    CHORDS_DIR,
)
from chord_cache import chord_file_for, chord_bak_for, ensure_chord_bucket

router = APIRouter(prefix="/api/process", tags=["process"])

# Max upload size: 200 MB
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

ALLOWED_AUDIO_MIME_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac",
    "audio/wav", "audio/x-wav", "audio/ogg",
}
MIDI_MIME_TYPES = {
    "audio/midi", "audio/mid", "audio/x-midi", "audio/x-mid",
    "application/x-midi", "application/midi",
}
MIDI_EXTENSIONS = {".mid", ".midi"}


def _require_user_facing():
    """Upload API available on all supported deployment modes."""
    if not (is_beta_mode() or is_public_mode() or is_personal_mode()):
        raise HTTPException(status_code=404, detail="Not available")


# Back-compat alias — old beta-only callers still resolve.
_require_beta = _require_user_facing


def _validate_upload_format(filename: str, content_type: str) -> tuple[str, bool]:
    ct = (content_type or "").lower().strip()
    ext = Path(filename or "").suffix.lower()
    is_midi = ext in MIDI_EXTENSIONS
    if ct in ALLOWED_AUDIO_MIME_TYPES:
        return ext, False
    if ct in MIDI_MIME_TYPES:
        return ext, True
    if is_midi and ct in {"", "application/octet-stream", "binary/octet-stream"}:
        return ext, True
    raise HTTPException(status_code=400, detail=f"不支援的音檔格式: {ct or '(empty)'}")


def _convert_midi_to_wav(midi_path: Path, wav_path: Path):
    try:
        import pretty_midi
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="伺服器缺少 pretty_midi，無法讀取 MIDI") from exc
    try:
        import numpy as np
        import soundfile as sf
        pm = pretty_midi.PrettyMIDI(str(midi_path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"MIDI 讀取失敗: {exc}") from exc
    if not pm.instruments:
        raise HTTPException(status_code=400, detail="MIDI 檔沒有可播放音軌")
    wave = pm.synthesize(fs=22050)
    if wave is None or len(wave) == 0:
        raise HTTPException(status_code=400, detail="MIDI 內容為空，無法分析")
    peak = float(np.max(np.abs(wave)))
    if peak > 1.0:
        wave = wave / peak
    sf.write(str(wav_path), wave.astype(np.float32), 22050, subtype="PCM_16")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload", dependencies=[Depends(_require_user_facing)])
def upload_audio(file: UploadFile = File(...),
                 username: str = Depends(get_user_or_anon)):
    # Quota check
    if not check_quota(username):
        raise HTTPException(
            status_code=429,
            detail=f"每日額度已用完 ({get_user_daily_count(username)}/10)"
        )

    ext, is_midi_upload = _validate_upload_format(file.filename or "", file.content_type or "")

    # Read file with size limit
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="檔案超過 200MB 上限")

    # Save to tmp
    job_id = generate_job_id()
    if not ext:
        ext = ".mid" if is_midi_upload else ".mp3"
    tmp_path = TMP_DIR / f"{job_id}{ext}"
    tmp_path.write_bytes(data)
    audio_path = tmp_path
    if is_midi_upload:
        wav_path = TMP_DIR / f"{job_id}.wav"
        try:
            _convert_midi_to_wav(tmp_path, wav_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        audio_path = wav_path

    # Compute file hash for audit
    file_hash = hashlib.md5(data).hexdigest()[:12]

    title = os.path.splitext(file.filename or "")[0].strip() or "Uploaded"

    job = ProcessJob(
        job_id=job_id,
        username=username,
        source_type="upload",
        audio_path=str(audio_path),
        title=title,
        file_hash=file_hash,
    )

    try:
        submit_job(job)
    except queue.Full:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="處理佇列已滿，請稍後再試")

    return {"job_id": job_id, "status": "queued"}


# ---------------------------------------------------------------------------
# Status & Result
# ---------------------------------------------------------------------------

@router.get("/status/{job_id}", dependencies=[Depends(_require_user_facing)])
def job_status(job_id: str, username: str = Depends(get_user_or_anon)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "progress": job.progress,
        "stage": job.stage,
        "title": job.title,
        "error": job.error_msg if job.status == JobStatus.ERROR else "",
        "result_hash": job.result_hash if job.status == JobStatus.DONE else None,
        "source_type": job.source_type,
    }


@router.get("/result/{job_id}", dependencies=[Depends(_require_user_facing)])
def job_result(job_id: str, username: str = Depends(get_user_or_anon)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.DONE:
        raise HTTPException(status_code=400, detail=f"Job not done (status: {job.status.value})")
    if not job.result_hash:
        raise HTTPException(status_code=500, detail="No result hash")

    chord_file = chord_file_for(job.result_hash)
    if not chord_file.is_file():
        raise HTTPException(status_code=404, detail="Chord data not found")

    import json
    return json.loads(chord_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------

# Lazy demo-cover lookup: build {demo_hash: cover_path} on first miss so
# replays from 最近播放 cards (which always route through this endpoint with
# the demo hash, not the demo id) still resolve to the shipped cover JPG.
_DEMO_COVER_MAP: dict[str, "Path"] | None = None


def _get_demo_cover_map() -> dict[str, "Path"]:
    global _DEMO_COVER_MAP
    if _DEMO_COVER_MAP is not None:
        return _DEMO_COVER_MAP
    from pathlib import Path
    import json as _json
    out: dict[str, Path] = {}
    manifest = Path(__file__).parent.parent / "data" / "demo" / "manifest.json"
    if manifest.is_file():
        try:
            for entry in _json.loads(manifest.read_text(encoding="utf-8")):
                h = entry.get("hash")
                demo_id = entry.get("id")
                cover_url = (entry.get("cover_url") or "").split("?", 1)[0]
                # cover_url shape: /static/demo/covers/<id>.jpg → resolve to disk
                if h and cover_url.startswith("/static/demo/"):
                    rel = cover_url[len("/static/demo/"):]
                    p = Path(__file__).parent.parent / "data" / "demo" / rel
                    if p.is_file():
                        out[h] = p
                        if demo_id:
                            for alias in (
                                f"demo/{demo_id}.mp3",
                                f"Y:/demo/{demo_id}.mp3",
                            ):
                                alias_hash = hashlib.md5(
                                    alias.encode("utf-8")
                                ).hexdigest()[:12]
                                out[alias_hash] = p
        except Exception:
            pass
    _DEMO_COVER_MAP = out
    return out


@router.get("/cover/{hash}")
def get_cover(hash: str):
    """Serve cover art for a processed song.

    Public mode with LIVECHORD_USE_R2=1 streams from Cloudflare R2; the
    local-disk path is checked first (so covers extracted before R2 was
    enabled keep working) and falls through to R2 on miss. Personal/beta
    deploys never touch R2.

    Demo-song fallback: replay from 最近播放 records the path as
    ``__hash/<demo_hash>``; the homepage card then asks for cover via this
    endpoint with that hash, which has no entry under data/covers/. We
    look the hash up against data/demo/manifest.json to surface the
    shipped JPG instead of returning a 404 placeholder."""
    cover_path = COVERS_DIR / f"{hash}.jpg"
    if cover_path.is_file():
        return FileResponse(
            cover_path, media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    demo_cover = _get_demo_cover_map().get(hash)
    if demo_cover is not None:
        return FileResponse(
            demo_cover, media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    from r2_storage import is_r2_enabled, download_cover
    if is_r2_enabled():
        data = download_cover(hash)
        if data is not None:
            from fastapi.responses import Response
            return Response(
                content=data,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )

    raise HTTPException(status_code=404, detail="No cover")


# ---------------------------------------------------------------------------
# On-demand beat upgrade — runs the slow madmom path against an existing
# chord JSON. Default ingest uses librosa (fast); users opt into rubato
# tracking from the player Tools popup. See doc/QA_BATTLE_STORY.md 番外篇 VII.
# ---------------------------------------------------------------------------

def upgrade_beats(hash: str, username: str = Depends(get_current_user)):
    """Enqueue an on-demand madmom beat-detection job.

    Returns immediately with ``{status: queued|duplicate, hash, ...}``.
    The actual madmom run (~30s) happens in beat_upgrade_queue's daemon
    worker thread; client polls ``GET /api/process/upgrade-beats/status``
    for completion. Pre-flight rejects (early validation):
      - madmom missing → 503 with diagnostic
      - upload-mode path (audio cleaned up) → 410
      - chord JSON / audio missing → 404
      - chord array empty → 400
    """
    import json as _json
    from beat_snap import HAS_MADMOM, MADMOM_IMPORT_ERROR
    from beat_upgrade_queue import enqueue as _enqueue, get_status as _get_status
    from config import resolve_path

    if not HAS_MADMOM:
        raise HTTPException(
            status_code=503,
            detail=f"madmom not installed: {MADMOM_IMPORT_ERROR or 'unknown'}",
        )

    chord_file = chord_file_for(hash)
    if not chord_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    sheet = _json.loads(chord_file.read_text(encoding="utf-8"))
    track_path = sheet.get("path", "")

    # Upload-mode (process_queue cleaned up the audio after melody extraction):
    # no recovery path → 410 with prompt.
    if track_path.startswith("__upload/"):
        raise HTTPException(
            status_code=410,
            detail="此曲為直接上傳音檔，原始檔已清除；請從首頁重新上傳此曲以重新偵測節拍",
        )

    # Library-mode (NAS path) songs: audio must exist on disk now.
    audio_path = ""
    if track_path:
        audio_path = resolve_path(track_path)
        if not audio_path or not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail="audio file not found")

    chords = sheet.get("chords") or []
    if not chords:
        raise HTTPException(status_code=400, detail="chord JSON has no chords")

    title = sheet.get("title") or os.path.basename(track_path) or hash
    queued_status = _enqueue(hash, audio_path, str(chord_file),
                             title=title, requested_by=username)
    snapshot = _get_status(hash) or {}

    logger.info("upgrade_beats enqueued by %s: hash=%s status=%s",
                username, hash, queued_status)

    return {
        "ok": True,
        "hash": hash,
        "title": title,
        "status": snapshot.get("status", queued_status),
        "queued": queued_status == "queued",
        "duplicate": queued_status == "duplicate",
    }


def upgrade_beats_status(hash: str, username: str = Depends(get_user_or_anon)):
    """Return current status of a beat-upgrade job for ``hash``.

    Status values: queued / running / done / error / not_found.
    Frontend polls this to decide when to fire the completion toast.
    """
    from beat_upgrade_queue import get_status as _get_status
    snap = _get_status(hash)
    if snap is None:
        return {"status": "not_found", "hash": hash}
    return {"hash": hash, **snap}


router.add_api_route("/upgrade-beats", upgrade_beats, methods=["POST"])
router.add_api_route("/upgrade-beats/status", upgrade_beats_status,
                     methods=["GET"])


# ---------------------------------------------------------------------------
# Beat source toggle (personal-only, 8800)
# ---------------------------------------------------------------------------
# Lets the user flip a song between librosa (fast, ingest default) and madmom
# (rubato tracking) beat sources, for A/B comparison. Each source's result is
# cached to disk so subsequent switches are instant (no 30s madmom re-run).
#
# Caches:
#   <hash>.json.bak.librosa  — full sheet snapshot when librosa was source
#   <hash>.json.bak.madmom   — full sheet snapshot when madmom was source
# Legacy .bak.beats (written by old upgrade_beats flow) migrates to .bak.librosa
# on first read — semantically equivalent (pre-madmom == librosa).
#
# Swap preserves user edits to chords/sections/etc; only beat-related fields
# are replaced (see BEAT_FIELDS).
# ---------------------------------------------------------------------------
_BEAT_FIELDS = ("bpm", "beats", "downbeats", "tempo_curve",
                "beats_source", "beat_version", "bpm_correction")


def _beat_source_category(src) -> str:
    if not src:
        return "librosa"
    s = str(src).lower()
    if "beat_this" in s:
        return "beat_this"
    if "madmom" in s:
        return "madmom"
    return "librosa"


def _migrate_legacy_bak(sheet_path: str):
    legacy = sheet_path + ".bak.beats"
    lib = sheet_path + ".bak.librosa"
    if os.path.exists(legacy) and not os.path.exists(lib):
        try:
            os.replace(legacy, lib)
            logger.info("migrated legacy .bak.beats -> .bak.librosa: %s", sheet_path)
        except Exception as e:
            logger.warning("bak migration failed %s: %s", sheet_path, e)


def _apply_beat_fields_from_snapshot(current: dict, snapshot: dict):
    """Copy beat fields from snapshot onto current (in-place).

    Keys missing in snapshot get removed from current, so a librosa snapshot
    predating bpm_correction doesn't leave a stale correction behind.
    """
    for k in _BEAT_FIELDS:
        if k in snapshot:
            current[k] = snapshot[k]
        else:
            current.pop(k, None)


def _overlay_beat_fields_to_user_version(hash: str, username: str, sheet: dict):
    """If the caller has a user-specific chord version saved (which is what
    GET /api/chords serves them on LAN personal mode), overlay the beat fields
    from the canonical sheet onto that file so the UI sees the new beats_source.

    The user version was saved via ChordSheet (Pydantic) which only preserves
    path/key/capo/bpm/chords — beat fields get stripped. Without this overlay,
    the user continues to see stale/missing beat data after a switch.
    """
    import json as _json
    from pathlib import Path
    if not username or username == "admin" and False:
        return  # (no special-case; kept explicit for reviewers)
    if not username:
        return
    user_path = (Path(__file__).parent.parent / "data" / "users"
                 / username / "chords" / f"{hash}.json")
    if not user_path.is_file():
        return
    try:
        user_sheet = _json.loads(user_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("overlay %s -> user %s failed to read: %s",
                       hash, username, e)
        return
    # Apply the same beat fields (bpm is in ChordSheet so already tracked;
    # beats/downbeats/tempo_curve/beats_source/beat_version/bpm_correction
    # are the ones that normally get stripped).
    _apply_beat_fields_from_snapshot(user_sheet, sheet)
    tmp = str(user_path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(user_sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(user_path))
        logger.info("overlay beat fields to user version: %s / %s", username, hash)
    except Exception as e:
        logger.warning("overlay %s -> user %s write failed: %s",
                       hash, username, e)


@router.post("/beats/switch")
def switch_beats(
    mode: str,
    hash: str = "",
    path: str = "",
    username: str = Depends(get_user_or_anon),
):
    """Swap a song's beat source between librosa, madmom, and beat_this.

    - If target cache exists: atomic in-place swap, <1s.
    - If target == librosa and cache missing: run librosa synchronously (~1-2s).
    - If target == madmom and cache missing: enqueue madmom job (~30s,
      background) — client polls /upgrade-beats/status.
    - If target == beat_this and cache missing: dispatched to Modal serverless
      GPU when LIVECHORD_USE_MODAL_BEAT_THIS=1; otherwise 503 with
      "run PC bulk batch first".

    Accepts ``hash`` OR ``path`` (8800 path-mode sends path; hash-mode sends hash).

    Mode + auth gating:
    - ``librosa`` and ``madmom`` require BOTH personal-mode AND a logged-in user
      (they compute on the host: librosa loads audio in-memory; madmom needs
      the MSVC build toolchain unlikely on a VPS). Anonymous or public callers
      asking for those return 404 / 401.
    - ``mode=beat_this`` is allowed in any deployment mode AND for anonymous
      callers, because the heavy lifting is done on Modal — the local process
      only does file I/O around the dispatch. Anonymous callers still send a
      well-formed X-Anon-Id header (auto-injected by the frontend auth wrapper)
      so audit / per-browser tracking still works.
    """
    import json as _json
    from chord_cache import song_hash
    from config import resolve_path, is_personal_mode
    from beat_snap import (HAS_MADMOM, MADMOM_IMPORT_ERROR,
                           analyze_and_snap_dynamic)

    if mode not in ("librosa", "madmom", "beat_this"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'librosa', 'madmom' or 'beat_this'",
        )

    # Librosa / madmom: personal-mode + real account required.
    if mode != "beat_this":
        if not is_personal_mode():
            raise HTTPException(status_code=404, detail="Not available on this instance")
        if is_anon(username):
            raise HTTPException(status_code=401, detail="未授權 (Unauthorized)")

    if not hash and path:
        hash = song_hash(path)
    if not hash:
        raise HTTPException(status_code=400, detail="hash or path required")

    chord_file = chord_file_for(hash)
    if not chord_file.is_file():
        raise HTTPException(status_code=404, detail="chord JSON not found")

    sheet_path = str(chord_file)
    sheet = _json.loads(chord_file.read_text(encoding="utf-8"))

    _migrate_legacy_bak(sheet_path)

    current_cat = _beat_source_category(sheet.get("beats_source"))
    if current_cat == mode:
        return {
            "ok": True, "already": True, "hash": hash, "mode": mode,
            "bpm": sheet.get("bpm"),
            "beats_source": sheet.get("beats_source"),
        }

    target_bak = sheet_path + f".bak.{mode}"
    other_bak = sheet_path + f".bak.{current_cat}"

    # Fast path: target cache hit
    if os.path.isfile(target_bak):
        try:
            target_snapshot = _json.loads(
                open(target_bak, encoding="utf-8").read())
        except Exception as e:
            raise HTTPException(status_code=500,
                                detail=f"target cache read failed: {e}")
        # Save current state as the other-mode cache if not already stored
        if not os.path.exists(other_bak):
            try:
                with open(other_bak, "w", encoding="utf-8") as f:
                    _json.dump(sheet, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("switch %s: %s cache write failed: %s",
                               hash, current_cat, e)
        _apply_beat_fields_from_snapshot(sheet, target_snapshot)
        tmp = sheet_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sheet_path)
        _overlay_beat_fields_to_user_version(hash, username, sheet)
        logger.info("switch_beats %s -> %s (cached, by %s)",
                    hash, mode, username)
        return {
            "ok": True, "switched": True, "cached": True,
            "hash": hash, "mode": mode,
            "bpm": sheet.get("bpm"),
            "beats_source": sheet.get("beats_source"),
        }

    # Slow path: target cache missing → compute
    track_path = sheet.get("path", "")
    audio_path = ""
    if track_path and not track_path.startswith("__upload/"):
        audio_path = resolve_path(track_path) or ""

    chords = sheet.get("chords") or []
    if not chords:
        raise HTTPException(status_code=400, detail="chord JSON has no chords")

    if mode == "librosa":
        if not audio_path or not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail="audio file not found")
        # Snapshot current (madmom) as its cache before overwriting
        if not os.path.exists(other_bak):
            try:
                with open(other_bak, "w", encoding="utf-8") as f:
                    _json.dump(sheet, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("switch %s: %s cache write failed: %s",
                               hash, current_cat, e)
        chords_copy = [dict(c) for c in chords]
        info = analyze_and_snap_dynamic(audio_path, chords_copy,
                                        prefer_madmom=False)
        if not info.get("beats_source"):
            raise HTTPException(status_code=500,
                                detail="librosa returned no beats")
        sheet["chords"] = chords_copy
        if info.get("bpm"):
            sheet["bpm"] = round(info["bpm"], 1)
        sheet["beats"] = info.get("beats", [])
        sheet["downbeats"] = info.get("downbeats", [])
        sheet["tempo_curve"] = info.get("tempo_curve", [])
        sheet["beats_source"] = info["beats_source"]
        sheet["beat_version"] = int(sheet.get("beat_version", 0)) + 1
        # Write librosa cache
        try:
            with open(sheet_path + ".bak.librosa", "w", encoding="utf-8") as f:
                _json.dump(sheet, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("switch %s: librosa cache write failed: %s", hash, e)
        tmp = sheet_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(sheet, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sheet_path)
        _overlay_beat_fields_to_user_version(hash, username, sheet)
        logger.info("switch_beats %s -> librosa (fresh, by %s)", hash, username)
        return {
            "ok": True, "switched": True, "cached": False,
            "hash": hash, "mode": "librosa",
            "bpm": sheet.get("bpm"),
            "beats_source": sheet.get("beats_source"),
        }

    # mode == "beat_this" — two routes depending on host capability:
    #   (a) LIVECHORD_USE_MODAL_BEAT_THIS=1 → dispatch to Modal serverless GPU
    #       via the same upgrade-beats queue (asynchronous, ~10-15s warm).
    #   (b) flag unset → no on-demand path (NUC has no CUDA, VPS without Modal
    #       can't run beat_this either). Instruct the user.
    # Cache is populated either way (.bak.beat_this), so future switches are
    # instant once a song has been beat_this-analyzed once.
    if mode == "beat_this":
        from modal_beat_this import modal_beat_this_enabled
        if not modal_beat_this_enabled():
            raise HTTPException(
                status_code=503,
                detail="此曲尚未由 beat_this 分析。請在 PC 端跑批次後再切換。",
            )
        if track_path.startswith("__upload/"):
            raise HTTPException(
                status_code=410,
                detail="此曲為直接上傳音檔，原始檔已清除；請從首頁重新上傳此曲以重新偵測節拍",
            )
        if track_path:
            if not audio_path or not os.path.isfile(audio_path):
                raise HTTPException(status_code=404, detail="audio file not found")

        # Snapshot current as its cache before queuing the beat_this run
        if not os.path.exists(other_bak):
            try:
                with open(other_bak, "w", encoding="utf-8") as f:
                    _json.dump(sheet, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("switch %s: %s cache write failed: %s",
                               hash, current_cat, e)

        from beat_upgrade_queue import enqueue as _enqueue, get_status as _get_status
        title = sheet.get("title") or os.path.basename(track_path) or hash
        qs = _enqueue(hash, audio_path, str(chord_file),
                      title=title, requested_by=username,
                      tracker="beat_this")
        snap = _get_status(hash) or {}
        logger.info("switch_beats %s -> beat_this (Modal) queued by %s: %s",
                    hash, username, qs)
        return {
            "ok": True, "queued": True, "hash": hash, "mode": "beat_this",
            "title": title,
            "status": snap.get("status", qs),
            "duplicate": qs == "duplicate",
        }

    # mode == "madmom" — delegate to background queue
    if not HAS_MADMOM:
        raise HTTPException(
            status_code=503,
            detail=f"madmom not installed: {MADMOM_IMPORT_ERROR or 'unknown'}",
        )
    if track_path.startswith("__upload/"):
        raise HTTPException(
            status_code=410,
            detail="此曲為直接上傳音檔，原始檔已清除；請從首頁重新上傳此曲以重新偵測節拍",
        )
    if track_path:
        if not audio_path or not os.path.isfile(audio_path):
            raise HTTPException(status_code=404, detail="audio file not found")

    # Snapshot current (librosa) as its cache before queuing madmom run
    if not os.path.exists(other_bak):
        try:
            with open(other_bak, "w", encoding="utf-8") as f:
                _json.dump(sheet, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("switch %s: librosa cache write failed: %s", hash, e)

    from beat_upgrade_queue import enqueue as _enqueue, get_status as _get_status
    title = sheet.get("title") or os.path.basename(track_path) or hash
    qs = _enqueue(hash, audio_path, str(chord_file),
                  title=title, requested_by=username)
    snap = _get_status(hash) or {}
    logger.info("switch_beats %s -> madmom queued by %s: %s",
                hash, username, qs)
    return {
        "ok": True, "queued": True, "hash": hash, "mode": "madmom",
        "title": title,
        "status": snap.get("status", qs),
        "duplicate": qs == "duplicate",
    }


# ---------------------------------------------------------------------------
# User history (non-admin)
# ---------------------------------------------------------------------------

@router.get("/my-history", dependencies=[Depends(_require_user_facing)])
def my_history(limit: int = 20, username: str = Depends(get_user_or_anon)):
    """Get current identity's own process history from audit log."""
    return {"history": get_user_audit_log(username, limit)}


@router.delete("/my-history/{entry_id}", dependencies=[Depends(_require_user_facing)])
def delete_my_history_entry(entry_id: int, username: str = Depends(get_user_or_anon)):
    """Delete one processed upload owned by the current identity."""
    deleted = delete_user_audit_entry(entry_id, username)
    if not deleted:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"deleted": 1, **deleted}


# ---------------------------------------------------------------------------
# Admin: Audit log
# ---------------------------------------------------------------------------

@router.get("/admin/audit")
def admin_audit(limit: int = 50, offset: int = 0,
                admin: str = Depends(get_admin_user)):
    return {"audit": get_audit_log(limit, offset)}


class DeleteAuditRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/admin/audit/delete")
def admin_audit_delete(req: DeleteAuditRequest,
                       admin: str = Depends(get_admin_user)):
    """Delete audit entries and associated chord/cover/melody files."""
    deleted = delete_audit_entries(req.ids)
    return {"deleted": deleted}
