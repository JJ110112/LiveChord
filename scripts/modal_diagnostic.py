"""Self-contained Modal diagnostic — verifies what's actually inside the
deployed image. Run with: modal run scripts/modal_diagnostic.py
"""

import modal

# Mirror the EXACT same image definition as backend/modal_btc.py so we test
# the same environment. Keep this in sync if modal_btc.py changes.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "torch==2.4.0",
        "numpy<2.0",
        "librosa>=0.10.1",
        "soundfile>=0.12.1",
        "PyYAML>=6.0",
        "scipy>=1.10",
        "numba>=0.59",
        "mir_eval>=0.7",
    )
)

app = modal.App("livechord-btc-diag")


@app.function(image=image)
def check() -> dict:
    """Run inside the Modal container, report what works."""
    import os
    import subprocess
    import sys

    out = {}

    # 1. Direct mir_eval import
    try:
        import mir_eval
        out["mir_eval_import"] = f"OK — version {mir_eval.__version__}, file {mir_eval.__file__}"
    except Exception as e:
        out["mir_eval_import"] = f"FAIL: {type(e).__name__}: {e}"

    # 2. Pip list filtered for mir / torch / librosa
    try:
        result = subprocess.run(
            ["pip", "list", "--format=freeze"],
            capture_output=True, text=True, timeout=10,
        )
        relevant = [
            l for l in result.stdout.splitlines()
            if any(k in l.lower() for k in ["mir", "torch", "librosa", "numpy"])
        ]
        out["pip_packages"] = relevant
    except Exception as e:
        out["pip_packages"] = f"FAIL: {e}"

    # 3. Python sys.path
    out["sys_path"] = sys.path

    # 4. Where is mir_eval on disk?
    try:
        result = subprocess.run(
            ["find", "/usr/local/lib", "-name", "mir_eval*", "-maxdepth", "5"],
            capture_output=True, text=True, timeout=5,
        )
        out["mir_eval_files"] = result.stdout.strip().splitlines() or ["<none>"]
    except Exception as e:
        out["mir_eval_files"] = f"FAIL: {e}"

    return out


@app.local_entrypoint()
def main():
    result = check.remote()
    print("\n=== Modal container diagnostic ===")
    for k, v in result.items():
        print(f"\n{k}:")
        if isinstance(v, list):
            for item in v:
                print(f"  {item}")
        else:
            print(f"  {v}")
