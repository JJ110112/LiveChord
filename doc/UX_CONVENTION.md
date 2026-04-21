# LiveChord UX Convention

Canonical UX rules for LiveChord. **All UI changes MUST follow this document.** When
a new pattern emerges that isn't covered here, update this file in the same PR that
introduces it — the doc is the reference, not a historical log.

Last updated: 2026-04-21 (live)
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

## §11  When these rules conflict with urgency

If a hotfix needs to ship in 5 minutes and following §1-10 would take 20, **fix
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

## Appendix B — Adding a new popup: 10-step checklist

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
