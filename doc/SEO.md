# LiveChord — Search Visibility & Ranking Plan

How `livechord.org` becomes findable on Google. Started 2026-05-03 with the first SEO bootstrap commit (`4352390`).

## Current state (2026-05-03)

| Item | Status | Notes |
|---|---|---|
| Domain ownership in Google Search Console | ✅ verified | Property: `sc-domain:livechord.org` |
| `<title>` + `<meta description>` on `/` | ✅ deployed | EN copy, NA-targeted (see [project_audience_geography](../../.claude/projects/c--Users-hitea-Claude-LiveChord/memory/project_audience_geography.md)) |
| Open Graph + Twitter Card | ✅ on `/`, `/help`, `/sponsor` | OG image is the 512px app icon — replace with proper 1200×630 hero when designed |
| `<link rel="canonical">` | ✅ on `/`, `/help`, `/sponsor` | `tos.html` has `noindex` (legal page) |
| `hreflang` alternates (en + zh-TW + x-default) | ✅ on `/` | Other pages don't yet — add when language-specific content diverges |
| JSON-LD `WebApplication` schema | ✅ on `/` | Declares `applicationCategory: MusicApplication`, `offers: free` |
| `sitemap.xml` | ✅ at `/sitemap.xml` | Lists `/`, `/help`, `/sponsor` only |
| `robots.txt` | ✅ updated | Sitemap line + blocks `/api/`, `/admin`, `/editor`, `/process`, `/share`, `/tos` |
| GSC sitemap submission | ✅ accepted | "Sitemap 已順利處理完畢", 3 URLs discovered |
| `/` URL Inspection → request indexing | ✅ done 2026-05-03 | Was already indexed (last crawl 2026-04-19, pre-SEO push); re-requested to refresh |
| `/help` request indexing | ✅ done 2026-05-03 | Status: 已找到 - 目前尚未建立索引 → in priority queue |
| `/sponsor` request indexing | ✅ done 2026-05-03 | Status: 已找到 - 目前尚未建立索引 → in priority queue |
| Backlinks (參照網頁) | ❌ **0 detected** | Single biggest blocker for ranking — see Phase 2 below |

## Where the meta lives

- `frontend/index.html` head block — title, description, canonical, hreflang ×3, OG ×5, Twitter ×4, JSON-LD WebApplication
- `frontend/help.html` / `frontend/sponsor.html` — description, canonical, OG ×5
- `frontend/tos.html` — `<meta name="robots" content="noindex,follow">` only
- `frontend/sitemap.xml` — static XML, served by `@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])` in [backend/main.py](../backend/main.py)
- `frontend/robots.txt` — served the same way

The two routes use `methods=["GET", "HEAD"]` (not `@app.get`) because some crawlers HEAD before GET to validate; FastAPI's default 405 on HEAD broke the first GSC sitemap fetch. Don't downgrade these.

## Phase 1 — On-page SEO (✅ done 2026-05-03)

What we shipped in commit `4352390` + `3de1609`:
- Real title + description on indexable pages
- Canonical + hreflang
- OG / Twitter cards
- Sitemap + robots.txt with sitemap reference
- JSON-LD structured data
- HEAD-method support on static-served files

**This phase delivers ≈10% of total SEO impact.** It's the prerequisite — without it nothing else works — but it doesn't drive rankings on its own.

## Phase 2 — Backlinks (the actual ranking driver, ❌ not started)

Google's PageRank-derived ranking is **dominated** by domain authority, which comes almost entirely from inbound links. `livechord.org` has zero — confirmed by GSC's "未偵測到任何參照網頁".

**Priority order (highest ROI first):**

### 2a. Reddit (single highest-leverage move)
- **r/piano** (1.2M members) — "I built a free AI tool that turns any audio file you upload into a real-time chord chart"
- **r/WeAreTheMusicMakers** (2.1M) — same angle, different framing
- **r/chordsforpiano**, **r/Guitar**, **r/musictheory** — secondary
- One Reddit dofollow link in a high-engagement thread ≈ 30 generic blog backlinks
- **Risk**: low-effort posts get flagged as self-promo. Lead with a specific feature (e.g. "AI bar-line correction" or "transpose + slow-down + A-B loop") + a usage screenshot, mention LiveChord in the comments. Don't put the URL in the title.
- **Don't pitch as a "YouTube chord finder"** — public mode is upload-only as of 2026-05-04 (see [project_youtube_public_disabled](../../.claude/projects/c--Users-hitea-Claude-LiveChord/memory/project_youtube_public_disabled.md)). Anyone landing on `livechord.org` expecting YT URL ingest will bounce. Frame around "drop your MP3/WAV/FLAC" instead.

### 2b. Hacker News
- **Show HN: LiveChord – AI piano/guitar chords for any audio file you upload**
- Best posting window: Tuesday-Thursday 8-10am Pacific
- Upvote velocity in first 30 min decides front-page placement
- Even a 50-upvote post that doesn't front-page gives 1 dofollow link from `news.ycombinator.com` (DR ~91)

### 2c. Product Hunt
- Schedule a launch day; recruit 20-30 friends to upvote in the first hour
- Maker comment with screenshots and "what's next" roadmap
- Top-5 of day → 200-500 visits + 3-5 reviewer blog mentions

### 2d. Long-tail content sites
- **iThome 鐵人賽** — 30-day series "用 AI 做出能即時辨識音檔和弦並陪你練習的網站"
- **Medium / Dev.to** — "How I built a 110M-param music transformer for chord-aware MIDI generation" (technical angle, links back to demo)
- **痞客邦 / 方格子** — Mandarin audience, lower DA but easier to rank

### 2e. Niche directories (low priority, do once)
- **alternativeto.net** — list LiveChord as alternative to Chordify (paid), Chord ai (paid), Chordify (paid)
- **producthunt.com** alternative listings
- **musictech.com / pianodao.com** — email pitch, occasionally accept guest posts

## Phase 3 — Indexable surface area expansion (medium-term, ❌ not started)

Currently only 3 URLs are indexable. To compete for long-tail queries like "Despacito piano chords" or "Shape of You guitar chords", LiveChord needs **per-song landing pages** that crawlers can read without JavaScript:

- New route `/song/<slug>` that server-renders (a) song title + artist (b) detected key + BPM (c) first 16 chords as plain HTML text (d) embedded `<a href="/player?...">` to launch the interactive player
- Each landing page becomes one entry in `sitemap.xml` (need to bump from 3 URLs to potentially thousands; chunk into `sitemap-songs-1.xml` etc with a sitemap index file)
- Schema.org `MusicComposition` JSON-LD per page
- **Compliance gate**: chord transcriptions are a copyright grey area in some jurisdictions. Check Hooktheory, Ultimate Guitar, Chordify precedents before flipping this on. May need to (a) require user upload/analysis before exposing the page (b) attribute "user-generated transcription" prominently (c) DMCA takedown form ready
- Estimated impact when shipped: 10-100× indexable URLs → 10-50× organic traffic ceiling

## Phase 4 — Content that earns links naturally (long-term, ❌ not started)

The `/blog/` model — write posts that other people *want* to link to:
- "How beat tracking actually works (and why it gets BPM wrong on slow ballads)" — niche-relevant, tied to LiveChord's `bar_arbitrator` work
- "We analyzed 13,000 songs to train an AI bar-line correction model — here's what we found" — data-rich, screenshot-heavy
- "The chord-quality ★ rating system that learns from human corrections" — appeals to ML engineers + musicians

Each post should target a long-tail keyword (Ahrefs / Ubersuggest free tier is enough) and link internally to `/`, `/help`, and (eventually) `/song/<slug>` pages.

## Phase 5 — Technical SEO refinements (small wins, ❌ not started)

Pick up after Phases 1-3 are landing real traffic:

- **Server-side render hero copy** in `index.html` — currently the body is nearly empty for crawlers (everything renders via JS). Even one `<h1>` + one paragraph of static EN copy in the initial HTML response would significantly improve relevance scoring. Risk: the current SPA replaces the body content on JS load — need a markup strategy where the static hero coexists with the dynamic UI without flashing
- **Image SEO** — proper `alt=""` on every meaningful image (cover art, app icon, hero screenshots when added)
- **Page speed** — Cloudflare already minifies + brotli-compresses static assets. Lighthouse score >90 on `/` is achievable but currently untested
- **Core Web Vitals** — once GSC shows real data, watch CLS (cumulative layout shift) on `/` — the chord ribbon's font-loading flash is a likely offender
- **Proper 1200×630 OG image** — current OG is the 512px app icon, which renders centered with white space on Twitter/Facebook previews. Design a hero image with "LiveChord" text + a chord-ribbon screenshot + tagline. Save as `frontend/img/og-hero.png` and update the `og:image` URLs in 4 HTML files

## Expected timeline

| When | Realistic outcome |
|---|---|
| 2026-05-04 (next day) | `/help` and `/sponsor` indexed; `/` re-crawled with new meta |
| 2026-05-10 (1 week) | `site:livechord.org` Google search shows 3 results |
| 2026-05-17 (2 weeks) | First long-tail organic visits (1-10/day) — only after at least one Phase 2 backlink lands |
| 2026-06-03 (1 month) | If at least one Reddit/HN post landed: 50-200 visits/day. If no Phase 2 work happened: still <10/day |
| 2026-08-03 (3 months) | With sustained Phase 2 + a launched Phase 3: 500-2000 visits/day plausible. Without: stays <50/day forever |

The asymmetry is enormous: Phase 1 is necessary but ceiling-bound at ~50 visits/day. Phase 2 is the make-or-break.

## How to check progress

- **GSC → 成效 (Performance)**: shows clicks, impressions, average position over time. Comes online ~3-7 days after first index event
- **GSC → 網頁 (Pages)**: indexed vs not-indexed counts, with reasons for the not-indexed ones
- **`site:livechord.org`** in Google itself: rough sanity check of what's indexed
- **Ahrefs free backlink checker** (`ahrefs.com/backlink-checker`): track when Phase 2 efforts land detectable backlinks
- **`https://search.google.com/test/rich-results?url=https://livechord.org/`** — validates the JSON-LD schema renders correctly

## Quick reference — re-deploy after SEO content edit

After editing any meta tags in HTML files, sitemap.xml, or robots.txt:

1. Standard PC commit + push
2. Standard VPS deploy (see [OPS.md "Deploying code changes to the VPS"](OPS.md#deploying-code-changes-to-the-vps))
3. **Purge Cloudflare cache for the affected static files** — `robots.txt` and `sitemap.xml` are cached for 4 hours. Without a purge, Google sees stale content for up to 4h.
4. In GSC, re-submit the sitemap if structure changed; re-request indexing for any URL whose `<title>` or `<meta description>` changed (otherwise Google waits up to 30 days for natural re-crawl).
