"""Tuning correction: the source file on the NAS is rewritten, so the
guarantees that matter are (a) we only touch stable, clearly-off songs,
(b) the original survives every failure path, (c) revert restores the
exact pre-fix state including caches computed from the old audio."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import tuning_detect as td  # noqa: E402
import tuning_fix_queue as tq  # noqa: E402


def _tone(path, cents, seconds=100, sr=22050):
    """A-major-ish triad detuned by ``cents`` — a stand-in for a fast tape."""
    t = np.arange(int(seconds * sr)) / sr
    f = 440.0 * 2 ** (cents / 1200)
    y = sum(0.2 * np.sin(2 * np.pi * f * r * t) for r in (1, 1.25992, 1.49831))
    sf.write(path, np.stack([y, y], 1).astype("float32"), sr, subtype="PCM_16")


def test_circular_mean_wraps_at_quarter_tone():
    # +48 and -48 cents are 4 cents apart on the circle, not 96 — a naive
    # mean would call this song in tune (0) and a naive spread "drifting".
    mean, spread = td._circular_stats([48, -48, 49, -49])
    assert abs(abs(mean) - 50) < 2
    assert spread < 3


def test_stable_offset_is_fixable_and_fix_restores_pitch(tmp_path):
    p = str(tmp_path / "song.flac")
    _tone(p, 35)
    a = td.analyze_tuning(p)
    assert a["fixable"] and abs(a["offset_cents"] - 35) < 4

    res = td.correct_file(p, a["offset_cents"])
    assert os.path.exists(p + ".bak")
    # speed restored too: sharp tape was too fast, so the fixed file is longer
    assert res["duration_after"] > res["duration_before"]
    # residual must sit below the fix threshold, or a rescan would re-fix it
    assert abs(td.analyze_tuning(p)["offset_cents"]) < td.MIN_OFFSET_CENTS

    # a second fix must never overwrite the only true original
    with pytest.raises(FileExistsError):
        td.correct_file(p, 10)

    td.revert_file(p)
    assert not os.path.exists(p + ".bak")
    assert abs(td.analyze_tuning(p)["offset_cents"] - 35) < 4


def test_in_tune_and_lossy_songs_are_left_alone(tmp_path):
    p = str(tmp_path / "ok.flac")
    _tone(p, 3)
    assert td.analyze_tuning(p)["reason"] == "in_tune"
    w = str(tmp_path / "off.ogg")
    _tone(str(tmp_path / "off.wav"), 30)
    os.rename(str(tmp_path / "off.wav"), w)  # pretend lossy: we refuse to re-encode
    assert td.analyze_tuning(w)["reason"] == "unsupported_format"


def test_failed_swap_puts_original_back(tmp_path, monkeypatch):
    p = str(tmp_path / "song.flac")
    _tone(p, 30, seconds=20)
    before = open(p, "rb").read()
    real_replace = os.replace

    def flaky(src, dst):
        if src.endswith(".tuning_tmp.flac"):
            raise OSError("NAS dropped")
        return real_replace(src, dst)

    monkeypatch.setattr(td.os, "replace", flaky)
    with pytest.raises(OSError):
        td.correct_file(p, 30)
    monkeypatch.setattr(td.os, "replace", real_replace)
    assert open(p, "rb").read() == before
    assert not os.path.exists(p + ".bak")


def test_stash_unstash_roundtrip_and_human_data(tmp_path, monkeypatch):
    import chord_cache
    h = "abcdef123456"
    monkeypatch.setattr(tq, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tq, "PRETUNE_DIR", tmp_path / "tuning_pretune")
    cf = tmp_path / "chords" / h[:2] / f"{h}.json"
    monkeypatch.setattr(chord_cache, "chord_file_for", lambda _h: cf)
    cf.parent.mkdir(parents=True)
    cf.write_text("old chords")
    (tmp_path / "melodies").mkdir()
    (tmp_path / "melodies" / f"{h}.json").write_text("old melody")
    (tmp_path / "accompaniments").mkdir()
    (tmp_path / "accompaniments" / f"{h}_Block_1_verse_piano_v10.json").write_text("acc")
    (tmp_path / "accompaniments" / "ffffff000000_Block.json").write_text("other song")

    assert tq._stash_derived(h) == 3
    assert not cf.exists() and not (tmp_path / "melodies" / f"{h}.json").exists()
    assert (tmp_path / "accompaniments" / "ffffff000000_Block.json").exists()

    # post-fix data written by re-detection must not survive a revert
    cf.write_text("new chords")
    (tmp_path / "melodies" / f"{h}.json").write_text("new melody")
    assert tq._unstash_derived(h) == 3
    assert cf.read_text() == "old chords"
    assert (tmp_path / "melodies" / f"{h}.json").read_text() == "old melody"

    assert tq.human_data_reasons(h) == []
    (tmp_path / "users" / "hitea" / "chords").mkdir(parents=True)
    (tmp_path / "users" / "hitea" / "chords" / f"{h}.json").write_text("{}")
    assert tq.human_data_reasons(h) == ["user_chords:hitea"]


def test_scan_prefix_accepts_ime_and_explorer_input():
    # Admins paste folders from Explorer (backslashes) and type @ with a CJK
    # IME (full-width ＠); library paths are "@N/a/b", so either used to make
    # the scan match 0 tracks while the folder had songs.
    from tuning_api import normalize_prefix
    assert normalize_prefix(" ＠1/Relax\Album ") == "@1/Relax/Album"
    assert normalize_prefix("@1/Relax/") == "@1/Relax/"


def test_forced_direction_lands_on_the_chosen_grid_note():
    # A quarter-tone song is equally close to two notes. "down" must always
    # lower the pitch and "up" raise it (correct_file: > 0 = lower), each by
    # the distance to that note — never the other way round, or the admin's
    # chosen key would end up a semitone off from what they asked for.
    assert td.forced_correction_cents(49.7, "down") == 49.7
    assert td.forced_correction_cents(49.7, "up") == -50.3
    assert td.forced_correction_cents(-48.0, "down") == 52.0   # flat 48 -> next note down
    assert td.forced_correction_cents(-48.0, "up") == -48.0
    with pytest.raises(ValueError):
        td.forced_correction_cents(49.7, "sideways")


def test_quarter_tone_song_is_refused_until_a_direction_is_given(tmp_path):
    p = str(tmp_path / "hooked.flac")
    _tone(p, 48)   # Hooked on Classics measures +47.8 / +49.7
    a = td.analyze_tuning(p)
    assert not a["fixable"] and a["reason"] == "ambiguous_quarter_tone"
    td.correct_file(p, td.forced_correction_cents(a["offset_cents"], "down"))
    assert abs(td.analyze_tuning(p)["offset_cents"]) < 5
