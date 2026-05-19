# frontend/js/vendor/

Self-hosted third-party libraries. Pinned versions, served from `/js/vendor/` via the existing `/js` FastAPI static mount in `backend/main.py:236`. Living under `/js/` (rather than a new `/vendor/` mount) avoids touching `main.py` — no NUC restart required when adding a vendored library.

## vexflow.min.js — VexFlow 4.2.3 (Bravura build)

- **Source**: `https://cdn.jsdelivr.net/npm/vexflow@4.2.3/build/cjs/vexflow-bravura.js`
- **Why bravura build**: bundles full notation engine + Bravura SMuFL font in one file (~570 KB raw, ~140 KB gzip). Avoids the separate font load that `vexflow-core.js` (~356 KB) would require. The full `vexflow.js` (992 KB) ships every font variant — overkill for our single-engraver use case.
- **License**: MIT (Mohit Cheppudira et al., see https://github.com/0xfe/vexflow/blob/master/LICENSE)
- **Self-host rationale**: CDN dependency would break offline development, break NUC personal mode (LAN-only), and re-introduce the Cloudflare edge-cache bust problem we explicitly avoid in the cache-bust discipline. Everything served from `/js/vendor/...?v=N` is under our cache-bust control.
- **Upgrade path**: download new version, rename to `vexflow.min.js`, bump `?v=` in [player.html](../../player.html). VexFlow 4.x→5.x has API changes — check release notes before bumping major.
