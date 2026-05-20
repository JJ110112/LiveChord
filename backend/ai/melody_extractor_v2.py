import os
import sys
import time
import librosa
import numpy as np

from .melody_schema import finalize_melody_events


class MelodyExtractorV2:
    """
    主旋律萃取器 V2 (深度學習版) — Phase 1.5

    骨幹：Spotify Basic Pitch (ONNX 即時推論)
    硬體：PC=CUDA / NUC=OpenVINO (NPU/GPU) / fallback=CPU

    Phase 1.5 強化：
      - ONNX session 由我們接管，providers list 實際會生效（原 basic-pitch 寫死 CPU）
      - polyphonic → monophonic melody filter（highest-pitch wins per time slice）
      - 任何加速步驟失敗 → gracefully fall back 回 basic-pitch 預設路徑（退路保留）
    """

    def __init__(self, device_type=None):
        """
        device_type: OpenVINO 裝置選項（僅在 NUC 且 OpenVINOExecutionProvider 可用時生效）
                     "NPU" / "GPU_FP16" / "CPU_FP32" / None（= OpenVINO EP 自行決定）
        """
        self._bp_model = None          # basic_pitch.inference.Model instance OR path-string (fallback)
        self._predict = None           # basic_pitch.inference.predict function
        self._model_path = None        # ONNX file path
        self._provider_target = self._detect_provider()
        self._device_type = device_type
        self._providers_in_use = None  # what ONNX actually runs with (set after load)
        self._ensure_loaded()

    # ------------------------------------------------------------------
    # 硬體偵測
    # ------------------------------------------------------------------
    def _detect_provider(self):
        """PC=CUDA / NUC=OpenVINO / else=CPU. 可用 LIVECHORD_ENV=PC|NUC 強制。"""
        current_path = os.path.abspath(os.getcwd()).lower()
        env_mode = os.environ.get("LIVECHORD_ENV")
        if env_mode == "PC" or r"c:\users\hitea" in current_path:
            return "CUDAExecutionProvider"
        if env_mode == "NUC" or r"c:\livechord" in current_path:
            return "OpenVINOExecutionProvider"
        return "CPUExecutionProvider"

    # ------------------------------------------------------------------
    # 模型載入 — 自建 ONNX session 以真正接上 provider
    # ------------------------------------------------------------------
    def _ensure_loaded(self):
        if self._predict is not None:
            return

        print("Loading Basic Pitch V2 Model...")
        try:
            from basic_pitch.inference import predict, Model
            from basic_pitch import build_icassp_2022_model_path, FilenameSuffix
        except ImportError as e:
            print(f"Error: basic-pitch 尚未安裝 ({e})")
            raise

        self._predict = predict
        self._model_path = build_icassp_2022_model_path(FilenameSuffix.onnx)

        # 預設退路：直接丟 path 給 predict()（等同 Phase 1 行為）
        self._bp_model = str(self._model_path)
        self._providers_in_use = ["default (basic-pitch managed)"]

        # 嘗試升級：自建 ONNX session 並覆寫 Model.model
        try:
            # 重要：在 Windows 上 openvino.dll 在 site-packages/openvino/libs/，
            # 必須先 import openvino 讓它的 __init__ 呼叫 os.add_dll_directory 註冊
            # DLL 搜尋路徑，否則 onnxruntime_providers_openvino.dll 會找不到 openvino.dll
            if self._provider_target == "OpenVINOExecutionProvider":
                try:
                    import openvino  # noqa: F401
                except ImportError:
                    print("[V2] openvino package missing, OpenVINO provider will be skipped.")

            import onnxruntime as ort
            avail = ort.get_available_providers()

            providers = []
            if self._provider_target in avail and self._provider_target != "CPUExecutionProvider":
                if self._provider_target == "OpenVINOExecutionProvider" and self._device_type:
                    providers.append((self._provider_target, {"device_type": self._device_type}))
                else:
                    providers.append(self._provider_target)
            providers.append("CPUExecutionProvider")

            model = Model(str(self._model_path))
            if model.model_type == Model.MODEL_TYPES.ONNX:
                sess_options = ort.SessionOptions()
                custom_sess = ort.InferenceSession(
                    str(self._model_path),
                    sess_options=sess_options,
                    providers=providers,
                )
                model.model = custom_sess
                self._bp_model = model
                self._providers_in_use = list(custom_sess.get_providers())
                print(f"[V2] ONNX session overridden. Providers in use: {self._providers_in_use}")
            else:
                # Model 被路由到 TF/CoreML/TFLite（因為 TF 優先）
                # 強制用 onnxruntime 自建 session + 空殼 Model 包起來
                sess_options = ort.SessionOptions()
                custom_sess = ort.InferenceSession(
                    str(self._model_path),
                    sess_options=sess_options,
                    providers=providers,
                )
                forced = Model.__new__(Model)
                forced.model_type = Model.MODEL_TYPES.ONNX
                forced.model = custom_sess
                self._bp_model = forced
                self._providers_in_use = list(custom_sess.get_providers())
                print(
                    f"[V2] Model auto-picked {model.model_type.name}; forced ONNX. "
                    f"Providers in use: {self._providers_in_use}"
                )
        except Exception as e:
            # 保留退路：若 ONNX session 建不起來，維持 path-string 給 predict 自己處理
            print(
                f"[V2] ONNX session setup failed ({type(e).__name__}: {e}). "
                f"Falling back to basic-pitch default backend."
            )

    # ------------------------------------------------------------------
    # 主要 API
    # ------------------------------------------------------------------
    def extract_melody(
        self,
        audio_path,
        filter_melody=True,
        min_confidence=0.3,
        bpm=120.0,
        tempo_curve=None,
        time_signature="4/4",
    ):
        """
        解析音檔並回傳與 V1 pYIN 相同格式的音符區段。

        filter_melody: True → 套 polyphonic→monophonic 主旋律過濾（預設開）
                       False → 回傳 basic-pitch 原始 polyphonic 結果（偵錯用）
        min_confidence: 過濾門檻（basic-pitch 的 amplitude 做 confidence 使用）。
                        Phase 2.5 掃描結論：0.3 最佳（V1 coverage 64.8%）；0.5 時
                        V1 coverage 崩到 40.8%（輕柔主旋律被 amplitude 門檻誤殺）。
                        extras 問題是架構性（polyphonic+highest-pitch filter）不是
                        threshold 問題，需 demucs 預分離才能根治。詳見
                        doc/AI_MIGRATION_REPORT.md §8

        回傳格式:
        [{"start": 1.2, "end": 2.5, "note": "G4", "midi": 67, "confidence": 0.85}, ...]
        """
        self._ensure_loaded()
        print(
            f"Extracting melody V2... (target={self._provider_target}, "
            f"actual={self._providers_in_use})"
        )

        t0 = time.time()
        _, _, note_events = self._predict(
            audio_path,
            self._bp_model,
            onset_threshold=0.5,
            frame_threshold=0.3,
            minimum_note_length=58,
            minimum_frequency=None,
            maximum_frequency=None,
            melodia_trick=True,
        )

        events = []
        for s_time, e_time, pitch_midi, amp, _bends in note_events:
            midi_int = int(round(pitch_midi))
            events.append({
                "start": round(float(s_time), 3),
                "end": round(float(e_time), 3),
                "note": librosa.midi_to_note(midi_int),
                "midi": midi_int,
                "confidence": round(float(amp), 3),
            })

        events.sort(key=lambda x: (x["start"], -x["midi"]))
        raw_count = len(events)

        if filter_melody:
            events = self._filter_to_melody(events, min_confidence=min_confidence)
        events = finalize_melody_events(
            events,
            bpm=bpm,
            tempo_curve=tempo_curve,
            time_signature=time_signature,
        )

        dt = time.time() - t0
        print(
            f"Extraction V2 Finished in {dt:.2f}s. "
            f"raw={raw_count} → melody={len(events)} notes"
        )
        return events

    # ------------------------------------------------------------------
    # Polyphonic → monophonic melody filter
    # ------------------------------------------------------------------
    def _filter_to_melody(self, events, min_confidence=0.3):
        """
        basic-pitch 輸出的是多聲部 polyphonic；主旋律通常是最高聲部。

        步驟：
          1. 過濾 confidence 過低的 note（pitch-tracker 噪音）
          2. 以 midi 中位數為中心，將距中位數 > 12 半音的低音 bass note 整體剃掉
             （避免貝斯線干擾；若整首真的很低則 median 會跟著低，不會誤砍）
          3. 時間排序（start asc, midi desc）後，掃過每個 event：
             若目前 event 跟上一個保留的 event 時間重疊：
               - 本身 midi ≤ 上一個 → 丟棄（上聲部贏）
               - 本身 midi > 上一個 → 截斷上一個的 end 到本 event.start，本 event 保留（切換到更高聲部）
          4. 合併相鄰同 midi 段（gap < 0.1s）
        """
        if not events:
            return events

        # Pass 1: confidence 門檻
        events = [e for e in events if e.get("confidence", 0) >= min_confidence]
        if not events:
            return events

        # Pass 2: 剃掉明顯低於 median 1 個八度的 bass note
        midis = [e["midi"] for e in events]
        median_midi = float(np.median(midis))
        events = [e for e in events if e["midi"] >= median_midi - 12]
        if not events:
            return events

        # Pass 3: highest-pitch wins per time slice
        events.sort(key=lambda e: (e["start"], -e["midi"]))
        kept = []
        for e in events:
            if not kept:
                kept.append(dict(e))
                continue
            last = kept[-1]
            if e["start"] < last["end"]:
                # 時間重疊
                if e["midi"] <= last["midi"]:
                    continue  # 低於或等於 → 丟
                # 更高聲部 → 截斷上一個
                last["end"] = e["start"]
                if last["end"] <= last["start"]:
                    kept.pop()
                kept.append(dict(e))
            else:
                kept.append(dict(e))

        # Pass 4: 合併相鄰同 midi
        merged = []
        for e in kept:
            if (
                merged
                and merged[-1]["midi"] == e["midi"]
                and e["start"] - merged[-1]["end"] < 0.1
            ):
                merged[-1]["end"] = e["end"]
                merged[-1]["confidence"] = max(
                    merged[-1]["confidence"], e["confidence"]
                )
            else:
                merged.append(e)

        return merged


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _parse_cli_args(argv):
    """Tiny argparse-free parser so the shadow runner has a stable contract."""
    args = {"audio": None, "json_out": None, "quiet": False, "raw": False}
    it = iter(argv)
    for a in it:
        if a == "--json-out":
            args["json_out"] = next(it, None)
        elif a == "--quiet":
            args["quiet"] = True
        elif a == "--raw":
            args["raw"] = True
        elif not a.startswith("--") and args["audio"] is None:
            args["audio"] = a
    return args


if __name__ == "__main__":
    import json

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    opts = _parse_cli_args(sys.argv[1:])
    if not opts["audio"]:
        print("Usage: python -m backend.ai.melody_extractor_v2 <audio_file> "
              "[--json-out <path>] [--quiet] [--raw]")
        sys.exit(2)

    if opts["quiet"]:
        # Silence basic-pitch / tensorflow chatter on stdout/stderr
        import logging as _logging
        _logging.getLogger().setLevel(_logging.ERROR)
        # Redirect builtins print to devnull only for our own chatter
        _orig_print = print
        def print(*a, **kw):  # noqa: A001
            pass

    extractor = MelodyExtractorV2()
    try:
        results = extractor.extract_melody(
            opts["audio"], filter_melody=not opts["raw"]
        )
        if opts["json_out"]:
            out_path = os.path.abspath(opts["json_out"])
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False)
            # Exit quietly after write; caller reads the file
            sys.exit(0)

        print(f"Total segments: {len(results)}")
        if results:
            midis = [e["midi"] for e in results]
            confs = [e["confidence"] for e in results]
            print(
                f"Range: {librosa.midi_to_note(min(midis))} - "
                f"{librosa.midi_to_note(max(midis))} | "
                f"conf mean={np.mean(confs):.3f}"
            )
        print(json.dumps(results[:10], indent=2, ensure_ascii=False))
    except Exception as e:
        sys.stderr.write(f"V2 Error: {e}\n")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
