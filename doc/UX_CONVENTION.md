# LiveChord UX Convention

Canonical UX rules for LiveChord. **All UI changes MUST follow this document.** When
a new pattern emerges that isn't covered here, update this file in the same PR that
introduces it — the doc is the reference, not a historical log.

Last updated: 2026-04-24 (live; added §12 popup taxonomy — shared .lc-* surface for non-toolbar popups)
Audience: anyone editing `frontend/*.html`, `frontend/js/*.js`, `frontend/css/*.css`
— including future-Claude.

---

## §1  Three-tier information hierarchy inside popups

Toolbar popups that expose more than ~4 controls **MUST** be organized into at most
**three subgroups**, ordered by user priority:

| Tier | Label pattern | Content |
|---|---|---|
| 1 `主要動作` | learner-visible verb (e.g. "練習 — 由易到難") | The things a user touches most. Put `.practice-preset`-style quick-choice grids here. |
| 2 `設定 / 配置` | configuration-sounding (e.g. "伴奏模式 — 右手伴奏的風格") | Dropdowns, regeneration buttons, style pickers. Things the user touches once per song. |
| 3 `其他` | shown only when there are orphan toggles (chord tones, debug) | Display toggles that don't fit above. Omit the whole subgroup if empty. |

Do **not** use a separator `<div class="tb-separator">` inside `.tb-popup-wide` —
use `.tb-subgroup` instead. Each subgroup renders its own visual boundary.

### Required HTML scaffold

```html
<div class="tb-popup tb-popup-wide">
  <div class="tb-popup-title">標題</div>

  <div class="tb-subgroup">
    <div class="tb-subgroup-label">主要動作 — 一句話描述</div>
    <div class="tb-subgroup-row"> … buttons/selects … </div>
  </div>

  <div class="tb-subgroup">
    <div class="tb-subgroup-label">設定</div>
    <div class="tb-subgroup-row"> … </div>
  </div>
</div>
```

CSS guarantees:
- `.tb-popup-wide { flex-wrap: wrap; max-width: min(720px, 92vw); }` so popups
  never exceed viewport width even on 4K monitors.
- `.tb-subgroup { flex-basis: 100%; }` forces each subgroup to a new row.
- Last subgroup has no bottom border (`:last-child { border-bottom: none; }`).

---

## §2  Preset button pyramids

When a setting is best chosen from a small, fixed list (practice modes, speed,
jazzify level), render it as a **preset pyramid**: easy/simple options on top,
complex/advanced at the bottom.

**Practice-mode example** (the canonical case):

```
Row 1:  [ 左手 ]  [ 右手伴奏 ]  [ 右手旋律 ]     ← 3 single-role (easiest)
Row 2:  [ 左手+右手伴奏 ]  [ 左手+右手旋律 ]    ← 2 combined (medium)
Row 3:  [ 左手+右手伴奏+右手旋律 ]              ← 1 full (hardest)
```

CSS rule (shared `.practice-presets` class):
```css
.practice-presets .practice-opt:nth-child(-n+3) { flex: 1 1 calc(33.333% - 4px); }
.practice-presets .practice-opt:nth-child(4),
.practice-presets .practice-opt:nth-child(5)      { flex: 1 1 calc(50% - 4px); }
.practice-presets .practice-opt:nth-child(6)      { flex: 1 1 100%; }
```

### Rules

- **Ordering reflects difficulty, not alphabetical/codepoint order**. Easiest on
  top so a student scans top-to-bottom.
- **Label is semantic, not ordinal**. Use `"左手+右手伴奏"` not `"雙手·伴奏"` —
  the former reads as "what's included", the latter as a product SKU.
- **Active state**: single button has `.active` class; use `--accent` background.
- **Reverse-sync**: if other buttons can also change the same state (shortcut
  cycles, keybindings), their handlers MUST call `_syncPracticeModeUI()`-style
  helper to re-apply `.active` on the current preset. No out-of-sync UI.

---

## §3  Single source of truth for rendered state

If a UI state (e.g. "right-hand content mode") affects **more than one rendered
surface** (waterfall + scheduled audio + MIDI export + keyboard highlight), it
**MUST** resolve through one shared helper.

**Canonical example**: `_resolveRhEvents()` in [frontend/js/player.js:51+](../frontend/js/player.js).
Four call sites all derive right-hand events from the same helper:
1. Waterfall rendering (`.render-waterfall`)
2. Audio scheduler (`scheduleNotes`)
3. MIDI export (`exportMidi` via caller)
4. Keyboard `activeRh` (via `_resolveRhEvents` output)

Past incident (Apr 2026): `rhContentMode === "mel"` was only respected by the
waterfall because each call site had its own inline filtering. Audio played
accompaniment while the screen showed melody. **Cost: silent user confusion for
weeks.** Root cause: no shared helper. Fix was to extract and reuse.

**Rule**: any time you see the same filtering/mapping expression in 2+ places,
extract a helper. Future surfaces (e.g. fingering overlay, Piano-Tiles practice
mode) will silently forget otherwise.

---

## §4  Filename / state-persistence conventions

When a UI state affects **exported artifacts** (downloaded MIDI, shared URL,
bookmark), encode the state into the artifact so reproducing it is unambiguous.

Practice-mode MIDI filename template:

```
<SongName>_<Style>_<Level>[_<PracticeSuffix>].mid
e.g.  Europe - The Final Countdown_Auto_L1_Rmel.mid
```

Suffix encoding (`_practiceModeSuffix()` in player.js):
| Mode | Suffix |
|---|---|
| both + acc (default) | (empty — no suffix) |
| both + mel | `_mel` |
| both + both | `_full` |
| left-only | `_L` |
| right + acc | `_R` |
| right + mel | `_Rmel` |
| right + both | `_Rfull` |

State persistence: use `localStorage` with `livechord_` prefix. Example keys:
- `livechord_active_hand` (`both`/`left`/`right`)
- `livechord_rh_mode` (`acc`/`mel`/`both`)
- `livechord_rh_content_mode` (legacy alias, don't add new aliases)

---

## §5  Icon conventions

### Inline SVG, not icon fonts

All icons are **inline lucide-style SVG** with these required attributes:
```svg
<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
     aria-hidden="true"> … </svg>
```

- `stroke="currentColor"` so the icon inherits text color automatically — works
  with active state, dark/light themes, and accessibility high-contrast modes.
- `aria-hidden="true"` because the adjacent `<span>` label already names it.
- 14px inside preset buttons (`.pp-hand`, `.pp-mark`), 16px default for toolbar.

### Composed pictographs

Preset buttons that represent *combinations* (e.g. "left + right + chord")
compose small sub-icons inside a `.pp-icons` container:

```html
<span class="pp-icons" aria-hidden="true">
  <svg class="pp-hand">…</svg>
  <svg class="pp-hand pp-flip">…</svg>   <!-- mirrored = right hand -->
  <svg class="pp-mark">…</svg>            <!-- content indicator: chord/melody -->
</span>
<span class="pp-label">左手+右手伴奏</span>
```

The `pp-flip` class uses `transform: scaleX(-1)` to mirror the same hand SVG.

---

## §6  Mobile / touch rules

All new popups MUST survive three test environments:
1. Desktop Chrome 1920×1080 — reference.
2. Desktop Chrome 4K (3840×2160) — verify no unintended nowrap overflow.
3. Android Chrome portrait (S22 Ultra field-test standard).

### Touch hit targets

Per Apple HIG / Android UX guidelines, every tappable element MUST be ≥ 44×44 px
in portrait. The existing `.bottom-toolbar .tb-trigger { min-height: 44px }` rule
covers the toolbar triggers. For new inline buttons inside popups, use
`padding: 8px 10px` minimum or let `.tb-popup-btn` default handle it.

### Ghost-click prevention

Any new clickable SVG icon MUST have `pointer-events: none` on its children so
taps route to the parent button regardless of which `<path>` the finger hits:
```css
.practice-opt > *, .practice-opt svg, .practice-opt .pp-icons { pointer-events: none; }
```
(This is already applied via the global `.tb-trigger` rule — verify when adding
new container types.)

### Popup vs cycle — mobile vs desktop

Standard pattern for cycle-able controls (speed, loop, A-B, jazzify):
- **Touch**: tap → open `.tb-popup`, show all options, one tap picks.
- **Desktop**: click → cycle through options silently; the popup can still open
  on secondary trigger.

Gate with `_isTouchLike` (`'ontouchstart' in window || matchMedia('(pointer:coarse)').matches`)
so real mobile + Chromium DevTools mobile emulation both work.

---

## §7  Cache-bust discipline (non-negotiable)

Any edit to `.js`, `.css`, or included partials MUST bump the corresponding
`?v=N` query-string in every HTML tag that references it, in the same commit.

```html
<link rel="stylesheet" href="/css/player.css?v=61">
<script src="/js/midi-exporter.js?v=2"></script>
<script src="/js/player.js?v=172"></script>
```

**Missing a bump is a silent production bug**: users see stale JS from browser
cache → your "fix" looks broken to them even though `V:\` is correct. CI does
not check this; reviewers MUST.

---

## §8  Popup width clamp (viewport safety)

`.tb-popup-wide { max-width: min(720px, 92vw); }` is non-optional. Without it,
wide monitors (≥ 2560px) render the popup as a single 1800px-wide row of all
buttons — unusable.

Width must also clamp BELOW viewport so popups don't clip off-screen. Never
use fixed pixel widths > 720 for toolbar popups.

---

## §9  Redundancy elimination

When the same user intent (e.g. "switch to left-hand-only") is reachable via
both a preset picker AND a legacy cycle button, the cycle button either:

1. **Becomes a hidden shell** (`<div hidden>…</div>`) retained only so existing
   JS handlers + reverse-sync don't break — OR
2. **Is repurposed** as a keyboard/power-user shortcut (with `_isTouchLike`
   gating so mobile doesn't see it).

Never show both. Users testing both paths repeatedly ask "what's the
difference?" — UX debt.

Past incident (Apr 2026): 左右手 cycle + 右手內容 cycle + 練習模式 picker all
co-existed briefly. Field feedback: "每個都會動，我不知道按哪個" (every one
responds, I don't know which to press). Fix: hidden-shell approach.

---

## §10  Label & tooltip style

- **Label** (`<span>`): verb + object, ≤ 8 Han/CJK chars. No ordinal prefixes
  (don't say "雙手1 / 雙手2").
- **Tooltip** (`title` attribute): adds difficulty hint or explanation.
  Pattern: `難度 + 冒號 + 簡述`. Example: `最簡單：只練左手伴奏`.
- `aria-label` is required only when the `<span>` label is missing or
  decorative-only (rare — only icon-only buttons like `×` close).

---

## §11  z-index tier map (DO NOT invent new numbers)

The player page has grown ~50 stacking contexts. Picking a z-index "that seems
big enough" silently breaks when a later feature lands a bigger number or when
an ancestor creates a new stacking context (`transform`, `filter`, `position:fixed`
with z-index). Use the tier below; if your new overlay doesn't fit, **grow the
tier, not your local value**.

Past incident (2026-04-21): auto-split settings panel used `z-index: 300`.
`.chord-display-area` is `position:fixed; z-index:500` — so the panel rendered
*behind* the waterfall canvas. From the user's POV, clicking 工具 → 自動切分
did nothing. Fix was z-index: 1000 + a matching backdrop at 999.

### Tier map (player + editor combined)

| Tier | Range | What lives here | Example selectors |
|---|---|---|---|
| **0 Inline** | 0–10 | In-flow stacking within a component. No cross-component reach. | `.chord-block.selected` (5), `.rv-grid-phrase` (10), `.playhead` (10) |
| **1 Component chrome** | 10–100 | Sticky headers inside a panel, inline tooltips scoped to one card. | `.browse-header` (10), `.toast` (100 via base.css) |
| **2 Panel overlays** | 100–999 | Per-page overlays that must sit above normal content but below the toolbar. Tooltips, inline dropdowns, in-panel context menus. | `.topbar-search .search-results` (200), editor `#splitPopup` / `#beatAdjustPopup` (200), editor `.help-overlay` (300/299) |
| **3 Page-fixed chrome** | 500–999 | `position:fixed` chrome that defines the page layout: player's chord display area, progress bars that overlay content. **THIS TIER IS A PAINT TRAP** — anything below 500 that floats over the player ribbon will be hidden by the waterfall canvas. | `.chord-display-area` (500), `.progress-bar-mid` (8000) |
| **4 Modal dialogs** | 1000–8999 | Full-page modals, settings panels, correction panels. MUST have a backdrop one tier below. | Auto-split panel (1000), backdrop (999). `.correction-panel` (was 1000; check current) |
| **5 Toolbar chrome** | 9000–9499 | The persistent bottom toolbar and top bar. Always visible, always above modals-in-progress so user can escape. | `.player-topbar` (9000), bottom-toolbar popups (9000–9200) |
| **6 Floating widgets** | 9500–9999 | YouTube PiP, FABs, transient hints that must sit above even modals. | `.yt-embed-container` (9500), `.chord-ribbon-panel` overlay (9500), bug-report FAB (9600) |
| **7 Critical overlays** | 10000+ | Loading splash, fatal-error toast, auth-expired redirect banner — things that *must* block interaction. | `#initLoader` (9999–10000), loader bar (15000 in base.css) |

### Rules

1. **Never use numbers outside these ranges.** If a tier feels wrong, revisit
   the design rather than jumping tiers. A new modal is tier 4, never tier 7.

2. **Stacking context traps**. `transform`, `filter`, `perspective`, `clip-path`,
   `position:fixed with z-index`, `opacity < 1` on a parent all create a new
   stacking context. Your child's `z-index: 9999` is scoped to that context.
   Before picking a z-index, walk up the ancestor chain and check the tree.

3. **Every modal needs a backdrop**. Backdrop at `tier - 1` (e.g. modal=1000
   → backdrop=999). Clicking the backdrop closes the modal. Without a backdrop
   the user can click toolbar buttons behind the modal without closing it.

4. **!important is a red flag, not a feature**. Two uses of `!important` on
   z-index (`.chord-ribbon-panel` line 1985) are grandfathered past-incident
   workarounds — **do not add new ones**. If you need `!important` to win,
   your ancestor chain is fighting you; fix that instead.

5. **Dev tool: find what covers your element**. In DevTools console:
   ```js
   const el = document.querySelector(".my-panel");
   const r = el.getBoundingClientRect();
   document.elementFromPoint(r.x + r.width/2, r.y + r.height/2)
   ```
   If that returns anything other than your panel (or a descendant), something
   is painting on top — check the returned element's ancestor chain for a
   stacking context you didn't account for.

### Quick reference

```
   Tier 7:  loader / fatal toast                         10000+
   Tier 6:  FAB / PiP / floating hints                   9500–9999
   Tier 5:  toolbar + topbar                             9000–9499
   Tier 4:  modals + correction panels                   1000–8999
   Tier 3:  page-fixed chrome (chord-display-area=500)   500–999
   Tier 2:  panel overlays                               100–499
   Tier 1:  component chrome                             10–99
   Tier 0:  inline stacking                              0–10
```

---

## §12  Popup taxonomy — pick one of four types, never invent a fifth

Every popup/panel/overlay in the player (and, progressively, the rest of the app)
MUST fall into one of these four types. Each has a shared base class that owns
surface colour, blur, border, radius, shadow, title style, and z-index. Per-popup
CSS handles **only** content and positional overrides. Do not introduce new base
surfaces, new shadow presets, or new title styles — if your popup doesn't fit a
type, fix the type or widen the taxonomy in the same PR.

### The four types

| Type | Anchor | Backdrop | Blocks page | Z-tier (per §11) | Base class |
|---|---|---|---|---|---|
| **A — Toolbar popup** | Above a toolbar trigger | no | no | 5 (9000–9499) | `.tb-popup` (see §1) |
| **B — Modal dialog** | Centered in viewport | yes, dim + click-dismiss | yes | 4 (1000–8999) | `.lc-modal-backdrop` + `.lc-modal` |
| **C — Floating panel** | Fixed corner | no | no | 6 (9500–9999) | `.lc-panel` |
| **D — Toast banner** | Top or bottom full-width slim | no | no | 7 (10000+) | `.lc-banner` (+ `--warn` / `--info` / `--success` / `--bottom`) |

Decision flowchart:
- Is it anchored to a toolbar trigger button? → **A**.
- Does the user have to deal with it before continuing (a decision or form)? → **B**.
- Does it live in a corner while the user keeps using the page? → **C**.
- Is it a slim informational strip? → **D**.

### Shared surface tokens (defined in [frontend/css/player.css](../frontend/css/player.css))

```css
:root {
  --lc-popup-bg: rgba(22, 27, 34, 0.96);
  --lc-popup-blur: blur(12px);
  --lc-popup-border: 1px solid var(--border);
  --lc-popup-radius-sm: 10px;   /* panel + banner */
  --lc-popup-radius-lg: 12px;   /* modal */
  --lc-popup-shadow: 0 8px 24px rgba(0,0,0,0.5);
}
```

All four types use **the same frosted dark surface** (`--lc-popup-bg` +
`--lc-popup-blur`). The only visual variation is radius (sm vs lg) and z-tier.
`.tb-popup` has its own tokens baked in — it predates this section — but matches
the same rgba + blur values.

### Shared title + close button

- `.lc-title` — 14px / 700 weight / `var(--text)` / sentence-case. Use inside
  Type B (modal) and Type C (panel). **Do NOT** apply to `.tb-popup` — it has
  its own 11px uppercase `.tb-popup-title` suited to dense dropdowns.
- `.lc-subtitle` — 12px / `var(--text-dim)`. One-line hint under the title.
- `.lc-close` — 28×28 absolute-positioned top-right × button. Mobile hit-target
  is upsized to 40×40 via a `@media (pointer: coarse)` override.
- Button rows at the bottom of a dialog use `.lc-modal-actions`
  (`display: flex; gap: 8px; justify-content: flex-end`).
- Action buttons inside any popup MUST use `.tb-popup-btn` — no new button
  class. Semantic colour variants (green apply / red cancel) can add a second
  class for the colour tint only.

### Type B — Modal dialog scaffold

```html
<div class="my-modal lc-modal-backdrop">
  <div class="lc-modal">
    <button class="lc-close" aria-label="關閉">&times;</button>
    <div class="lc-title">對話框標題</div>
    <div class="lc-subtitle">一句話說明（選填）</div>
    <!-- body -->
    <div class="lc-modal-actions">
      <button class="tb-popup-btn">取消</button>
      <button class="tb-popup-btn active">確認</button>
    </div>
  </div>
</div>
```

- Backdrop click dismisses (guard: only when `e.target === backdrop`; clicks
  inside the modal card must not bubble-close).
- `ESC` dismisses. Add a `keydown` listener that removes itself on close.
- Dynamically injected: create backdrop + inner card, `appendChild(backdrop)`,
  only once. Do **not** `appendChild` the card separately.

### Type C — Floating panel scaffold

```html
<div class="my-panel lc-panel">
  <div class="lc-title">面板標題</div>
  <!-- body -->
</div>
```

Set position by overriding `top` / `bottom` / `left` / `right` in the per-popup
CSS block. `.lc-panel` defaults to no positional values so each usage is
explicit.

### Type D — Toast banner scaffold

```html
<div class="my-banner lc-banner lc-banner--warn">
  <span>⚠ 訊息內容</span>
  <button class="lc-banner-btn">動作</button>
  <button class="lc-banner-close" aria-label="關閉">&times;</button>
</div>
```

- `--warn` adds orange border + warm dark background.
- `--info` / `--success` add coloured border only (surface stays frosted dark).
- `--bottom` anchors to the bottom edge with safe-area-aware offset above
  the toolbar (accounts for 54px toolbar + 80px progress-bar-mid + safe-area).

### Responsive layout — one popup, two devices, zero overflow

Every popup MUST render correctly across **PC desktop ≥ 1920px**, **laptop
~1280px**, and **Android portrait ~390×844**. "Correctly" means:

1. **Width clamps to viewport**. Base `.tb-popup` now carries
   `max-width: min(720px, calc(100vw - 16px))` + `flex-wrap: wrap` — no popup,
   regardless of button count, can bleed past the viewport. When you add a
   new popup, do **not** override `max-width` upward; if your content doesn't
   fit, reduce button count or promote to a Type B modal (§12 taxonomy).

2. **Position clamps to viewport**. Even after width is capped, a popup
   centered on a rightmost toolbar trigger (e.g. Tools, Bug Report) could
   still render past the right edge. The `_clampPopupToViewport(popup)`
   helper in [frontend/js/player.js](../frontend/js/player.js) runs on every
   popup open (and on window resize) to shift the popup back in-bounds via
   `transform: translateX(calc(-50% + Npx))`. Don't reinvent this logic — if
   a new popup surface needs the same behaviour, call that helper.

3. **Content reflows, not scrolls**. Popups should wrap their content (via
   `flex-wrap: wrap` on the base class, or explicit subgroup rows inside
   `.tb-popup-wide`). Horizontal scrollbars inside a popup are a code smell
   — they signal the popup is trying to be wider than the viewport allows.
   The `.ab-phrase-strip` is the one grandfathered exception (tiny label
   pills, user-scrollable by design).

4. **Single source of truth for viewport-size branches**. Use
   `@media (pointer: coarse)` for touch-density overrides (bigger hit
   targets, relaxed wrap rules). Do **not** sniff userAgent for mobile.

5. **Test matrix when you add a popup**:
   - **1920×1080 desktop**: content fits, no wrap needed for ≤6-button popups.
   - **1280×720 laptop**: still no horizontal clip; Tools-sized popups may
     wrap to 2 rows — verify they look intentional, not crammed.
   - **390×844 Android portrait**: every popup wraps cleanly; no button
     touches the viewport edge; every tap target ≥ 44×44 px.

Past incident (2026-04-25): Tools popup had no `.tb-popup-wide` class, so it
used the default `flex-wrap: nowrap` and grew to ~1080px natural width — at
1280px viewport the popup clipped off the right edge of the screen. Fix was
to promote `flex-wrap: wrap` + `max-width` clamp to the `.tb-popup` base
class itself, plus the `_clampPopupToViewport` helper for position overflow.

### Forbidden

- `background: var(--bg-card)` on any popup surface (use `--lc-popup-bg`).
- Inline `style="..."` attributes on popup content (use named classes — see
  the auto-split panel for the reference implementation of a fully class-based
  dynamic popup).
- Z-indexes outside the §11 tier ranges.
- New popup surfaces/blurs/radii. If you need a new shade, it belongs in
  `:root --lc-popup-*` and every type inherits.
- Re-declaring `.lc-title` or `.lc-close` with different sizes. Size overrides
  for a specific popup go on the parent selector (e.g.
  `.my-modal .lc-title { font-size: 16px; }`) and only with cause.

### Past incidents that drove this section

- **2026-04-24**: audit of the player page found 19 popups; 10 had drifted
  across bg/blur/radius/title/z-index/backdrop. Cause: no taxonomy — each new
  popup copied the nearest sibling and mutated it. Fix: this §12 plus shared
  `.lc-*` classes and a one-shot retrofit.
- Worst-case drift: the auto-split panel carried ~20 inline `style="..."`
  attributes inside its `innerHTML`, making dark-mode / responsive tuning
  impossible from CSS. Retrofit moved every inline style to `.as-*` classes in
  [frontend/css/player.css](../frontend/css/player.css).

---

## §13a  Native form widgets must follow the page theme

Native widgets (`<select>` dropdowns, `<input type="date">` pickers, scrollbars,
form autofill backgrounds) are painted by the OS, not by your CSS. Without
explicit guidance the browser falls back to system colours, which on Windows
in dark mode is **system-light** — leaving the popup white-on-white-invisible
even though the page itself is dark.

### Two-prong fix — apply both for every `<select>`, `<input>` etc.

1. **Set `color-scheme` on `:root`**. This signals which palette to use for
   native UI. [frontend/css/base.css](../frontend/css/base.css) already does
   this — `:root { color-scheme: dark; }` for the default dark theme, plus
   `[data-theme="light"|"sakura"|"sunny"|"sky"] { color-scheme: light; }`
   for the four light themes. Forest stays dark by inheritance. **If you
   add a new theme, set `color-scheme` in the same rule that sets `--bg`.**

2. **Style the dropdown contents explicitly**. Even with `color-scheme` set,
   not all browsers honour it for `<option>` / `<optgroup>` text colour.
   For every styled `<select>`, add **both** rules:
   ```css
   .my-select option   { background: var(--bg); color: var(--text); }
   .my-select optgroup { background: var(--bg); color: var(--text-dim);
                         font-weight: 600; font-style: normal; }
   ```
   `optgroup` defaults to italic in most browsers — looks off in our UI;
   `font-style: normal` flattens it. `font-weight: 600` keeps the group
   header distinguishable from its options.

### Past incident (2026-05-06)
The `<select id="teachStyle">` in the AI-teaching popup was extended with
`<optgroup>` blocks (Pop / Rock / Blues / Jazz / Latin / Other). Dark theme
left the optgroup labels invisible because no rule existed for `optgroup`
and Windows Chromium painted the dropdown popup with system-light colours,
producing dim-text-on-light-bg → invisible. Fix: `color-scheme` + explicit
`optgroup` rule per the recipe above.

---

## §13b  Toast banners must NEVER intercept pointer events

Toasts are passive notifications — they fade in, sit for ~1.5s, fade out,
and stay in the DOM with `opacity: 0` until the next call to `showToast()`
recycles them. Without `pointer-events: none`, the invisible-but-still-DOM
toast continues to capture clicks for hundreds of ms after each call,
masking whatever sits beneath it.

### Rule

`.toast` (and any toast/banner/snackbar variant) MUST carry
`pointer-events: none`:

```css
.toast {
  position: fixed; bottom: 64px; right: 24px;
  z-index: 15000;
  /* ... visual styling ... */
  pointer-events: none;   /* MANDATORY */
}
```

If a toast genuinely needs a click target (rare — e.g. an "Undo" affordance),
make only the inner action button click-receptive:

```css
.toast { pointer-events: none; }
.toast .toast-action { pointer-events: auto; }
```

### Past incident (2026-05-06)
Audio-mode toggles fire `showToast("Switched to Mix...")`. The toast's
fixed position (bottom-right, ~370px wide × 43px tall) overlaps the right
edge of the bottom toolbar where A-B / AI Acc / Tools / Gear sit. Because
the toast had `pointer-events: auto` (CSS default), every audio-mode click
left the lower 4/5 of those 4 toolbar buttons unclickable for the duration
of the fade-out — visible reproduction: "click Music → MIDI → Mix in
quick succession, then watch A-B / AI Acc / Tools / Gear stop responding
to mid/bottom clicks for several seconds." Fix:
`.toast { pointer-events: none; }` once and for all.

This is the same root cause as the S22 Ultra "工具/錯誤回報 buttons need
top-1/5 to trigger" report from earlier QA — every toast call leaves a
silent intercept zone over the toolbar's right corner. **Always run a
`document.elementsFromPoint(x, y)` probe under each toolbar trigger after
adding any new fixed-position overlay** to confirm the trigger button is
still on top.

---

## §13c  Saturated colours need a light-theme remap, not just headers

Brand-saturated colours (yellows, light oranges, pale greens) read as
high-contrast accents on dark backgrounds but dissolve into low-contrast
washes on cream/sakura/sky backgrounds.

### Rule

When a component uses saturated finger / status / hint colours that are
shared between dark and light themes, define **one** remap helper local
to the component and apply it **everywhere** the colour is consumed —
column headers, button fills, ghost previews, text labels. Do not remap
just the headers and leave the body of the component on the dark-theme
palette.

```js
const _accLight = (() => {
  const t = document.documentElement.getAttribute("data-theme");
  return t === "light" || t === "sakura" || t === "sunny" || t === "sky";
})();
const FINGER_LIGHT_REMAP = {
  "#ffeb3b": "#9a7a00",  // yellow → dark gold
  "#ff9800": "#c45a00",  // orange → burnt orange
  "#ef5350": "#b91c1c",  // red → dark red
  "#66bb6a": "#1b5e20",  // green → forest green
};
const _lc = (c) => (_accLight ? (FINGER_LIGHT_REMAP[c] || c) : c);

ctx.fillStyle = _lc(FCLR[finger]);     // header
const fingerColor = _lc(FCLR[finger]); // pass remapped colour into the
                                        // rest of the function — every
                                        // pill/ghost/label sees it
```

If text colour decisions branch on the colour (e.g. "yellow needs dark
text"), gate that branch on `!_accLight` too — remapped colours have
different luminance and need the inverse contrast pair.

### Past incident (2026-05-06)
Accordion bass grid had `HEADER_LIGHT_REMAP` covering the 3 column
headers but the actual chord-button fills, dashed ghost previews, and
labels still pulled from the unremapped `FCLR` map. Result on light
themes: the Bass-column ghost preview "A" rendered as bright yellow on
cream — nearly invisible. The active-pill text-colour branch
(`playFinger === 3 ? "#333" : "#fff"`) was tuned for the dark-theme
yellow background; with the light-theme remap producing dark gold, the
"#333 dark text on dark gold" combination was equally unreadable. Fix:
unified `_lc()` helper applied across header + fingerColor + playColor +
text-colour branch.

---

## §13  When these rules conflict with urgency

If a hotfix needs to ship in 5 minutes and following §1-12 would take 20, **fix
first, refactor within 24h**. Leave a TODO comment in the code:

```html
<!-- TODO(ux-debt 2026-04-21): this popup bypasses §1 hierarchy for hotfix X.
     Restructure to tb-subgroup pattern before next release. -->
```

A rule you can't always follow is still a rule — the TODO is the contract.

---

## Appendix A — Grep cheatsheet

```
# Find all popups to audit structure
grep -n "class=\"tb-popup" frontend/player.html

# Find all cache-busted script/link tags
grep -nE "\\?v=" frontend/*.html

# Find state variables that might need _resolveXxx() helpers
grep -nE "localStorage\\.(get|set)Item\\('livechord_" frontend/js/*.js
```

## Appendix B — Adding a new popup: checklist

0. **Pick a type from §12 (A/B/C/D)**. If none fit, update §12 in the same PR
   before writing any CSS. Then:
1. Draft the 3 subgroups on paper first. Can't name one? Merge or drop it.
2. HTML: `tb-popup tb-popup-wide` + `tb-popup-title` + N `tb-subgroup` blocks.
3. CSS: no new popup-wide rule; rely on the shared one. Any new button class?
   Add rules to `.tb-subgroup-row .xxx` scope to avoid leaking.
4. SVG icons inline with `stroke="currentColor"`.
5. Label text ≤ 8 chars; `title` attribute carries the detailed hint.
6. Any state setter? Extract a `_setXxx()` that also calls `_syncXxxUI()`.
7. Any multi-surface state? Audit all call sites — if 2+, extract a
   `_resolveXxx()` helper.
8. Bump `?v=N` on JS/CSS in the same edit.
9. Test on 8802 local; verify with Playwright `.tb-popup` visibility +
   dimension checks.
10. Sync V:\ via `cp` + `diff -q`; mention in PR "no restart needed" or
    "restart_dual.bat required" explicitly.
