# Cloudflare R2 — Cover-Art Storage Runbook

LiveChord stores per-song cover art (extracted from uploaded ID3 / FLAC / M4A tags or YouTube thumbnails) at `data/covers/<hash>.jpg`. On the NUC that's local SSD and stays that way. **For the VPS deployment** (Phase G), covers move to Cloudflare R2 — pay-once write, zero egress, no disk pressure on the small VPS.

This doc is the operator runbook: bucket → API token → env vars → flip → verify.

## Cost expectations

- **Storage**: free first 10 GB, then $0.015/GB/month. LiveChord covers average ~50 KB; 10 GB ≈ 200 000 covers (we'll never hit that).
- **Class A operations** (PUT, DELETE): free first 1 M/month. We do ~100 PUTs/day, so months between any charge.
- **Class B operations** (GET, HEAD): free first 10 M/month. Plenty of headroom even with traffic 100× current.
- **Egress**: **$0** — that's R2's headline feature. Even when we add a public custom domain in front, egress remains zero.

Expected monthly bill: **$0** for years.

## One-time setup

### 1. Create the bucket

Cloudflare Dashboard → R2 → Create bucket.
- **Name**: `livechord-covers`
- **Location**: `Auto` (or pick `WEUR` if your VPS is in Europe — bucket location only affects write latency from the VPS, which is microscopic for 50 KB cover writes)

### 2. Create an API token scoped to that bucket

R2 main page → "Manage R2 API Tokens" (or `dash.cloudflare.com/?to=/:account/r2/api-tokens`):
- **Name**: `livechord-vps`
- **Permissions**: `Object Read & Write`
- **Specify bucket**: `livechord-covers` only — don't grant on all buckets
- **TTL**: forever (or whatever you prefer)

Cloudflare shows the secret **once**. Copy:
- Access Key ID
- Secret Access Key
- S3 endpoint URL — `https://<account-id>.r2.cloudflarestorage.com` (the 32-hex prefix is your account ID)

### 3. Wire the four env vars on the host that will write/read covers (the VPS)

```ini
# .env
LIVECHORD_USE_R2=1
R2_ACCOUNT_ID=<32-hex account id>
R2_ACCESS_KEY_ID=<access key>
R2_SECRET_ACCESS_KEY=<secret>
R2_BUCKET_COVERS=livechord-covers
```

### 4. Install boto3 on the VPS

```bash
pip install boto3
```

(Already added to `backend/requirements.txt` as a documented optional dep — `pip install -r backend/requirements.txt` does not install it automatically; install explicitly when enabling R2.)

### 5. Restart the FastAPI service

```bash
systemctl restart livechord  # or your equivalent — restart.bat for the NUC
```

## How it routes after the flip

**Write path** (`backend/process_queue.py`):

1. Cover bytes extracted from upload (ID3 / FLAC / M4A) or YouTube thumbnail
2. If `LIVECHORD_USE_R2=1` → `r2_storage.upload_cover(hash, bytes)` puts it at `s3://livechord-covers/<hash>.jpg` with `Cache-Control: public, max-age=31536000, immutable`
3. On any R2 exception → falls back to local disk `data/covers/<hash>.jpg` (so a transient R2 outage never loses a cover)

**Serve path** (`backend/process_api.py:get_cover`):

1. URL stays `/api/process/cover/<hash>` — no frontend change required
2. Local-disk file checked first (so existing covers from before the flag flip keep working)
3. If absent and `LIVECHORD_USE_R2=1` → `r2_storage.download_cover(hash)` fetches bytes and streams them through FastAPI as `image/jpeg`
4. 404 if both miss

Streaming through FastAPI keeps the URL pattern stable. Bandwidth cost on the VPS is trivial (covers are ~50 KB; even 10 000 reads/day = 500 MB/day egress — well under any VPS allowance).

## Verification

After flipping `LIVECHORD_USE_R2=1` and restarting, analyze any new song through the homepage. Then:

```bash
# Confirm the cover landed in R2
aws s3 ls s3://livechord-covers/ \
    --endpoint-url=https://<account-id>.r2.cloudflarestorage.com
```

Or via the Cloudflare dashboard → R2 → livechord-covers → 物件. You should see `<hash>.jpg` files appearing.

Then hit `/api/process/cover/<hash>` in a browser — the JPEG should load.

## Day-2 operations

### Faster reads via custom domain (optional but recommended)

To skip the FastAPI proxy and serve covers directly from Cloudflare's edge:

1. Cloudflare DNS for `livechord.org` → add a CNAME `covers` → R2 will provide a target
2. R2 dashboard → bucket settings → "Custom Domains" → add `covers.livechord.org`
3. Wait for cert provisioning (~5 min)
4. Update the frontend cover URL pattern from `/api/process/cover/<hash>` to `https://covers.livechord.org/<hash>.jpg`

Egress stays zero (CF-proxied), and the VPS drops out of the read path entirely. Defer this until you're seeing real traffic — for the indie-scale we're at, FastAPI proxying is fine.

### Rotating keys

```bash
# 1. Create a new API token with the same scope
# 2. Update R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY in .env on the VPS
# 3. Restart livechord
# 4. Confirm cover writes still work (analyze a song)
# 5. Delete the old token from the dashboard
```

If a key is leaked, do steps 1–3 immediately; step 5 can wait a few minutes until you've confirmed the new key works.

### Migrating existing local covers to R2

If you've been on local-disk for a while and want to move accumulated covers up:

```bash
# From the host with both .env populated and boto3 installed
python -c "
import os
os.environ['LIVECHORD_USE_R2'] = '1'
import sys; sys.path.insert(0, 'backend')
from r2_storage import upload_cover
from pathlib import Path
covers = Path('data/covers')
for p in covers.glob('*.jpg'):
    upload_cover(p.stem, p.read_bytes())
    print(f'uploaded {p.name}')
"
```

Local files stay where they are afterward (the serve path checks them first). You can delete the local copies once you've verified R2 reads work — but no rush.

## Architecture in 30 seconds

```
VPS (CPU only)                                Cloudflare R2
┌────────────────────────┐                    ┌────────────────────────┐
│ process_queue.py       │  put_object        │ livechord-covers/      │
│  upload_cover()        │ ──────────────▶    │   <hash>.jpg           │
│                        │                    │   ...                  │
│ process_api.py         │  get_object        │                        │
│  get_cover() ◀──── streams bytes ──────     │  zero egress fee       │
└────────────────────────┘                    └────────────────────────┘
                                                        ▲
                                                        │ (optional later)
                                                        │
                                              custom-domain CDN edge
                                              (covers.livechord.org)
```

Personal/beta deploys never enter this diagram — covers stay on local disk under `data/covers/`.
