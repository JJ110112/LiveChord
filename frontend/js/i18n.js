/**
 * LiveChord i18n (Phase C)
 *
 * Dot-namespaced key lookup with `{var}` interpolation. Per-page DOM patcher
 * that scans `[data-i18n]` text content and `[data-i18n-attr]` attribute
 * substitutions on demand. JSON dictionaries fetched once per language and
 * cached in memory for the page session; selected language persists in
 * localStorage so subsequent loads skip the round-trip flicker.
 *
 * Design choices:
 *
 *   - English is the default (public deployment is English-first per plan).
 *     Browser-language detection runs only on first visit; afterwards we
 *     respect the explicit user pick.
 *   - Missing keys fall back to the key itself (so debug-visible) rather
 *     than to another language. That makes it loud when a string slipped
 *     through untranslated, instead of silently mixing English+中文.
 *   - The `<html lang>` attribute is updated on every switch — accessibility
 *     readers + SEO + browser-built-in translate prompts all read it.
 *
 * Usage:
 *
 *     <span data-i18n="home.section.recent">最近播放</span>
 *     <input data-i18n-attr="placeholder=home.search.placeholder" placeholder="...">
 *     <select data-i18n-attr="title=common.lang_picker_title">…</select>
 *
 *     LiveChordI18n.t("toast.saved")          → string
 *     LiveChordI18n.t("error.upload.too_large", {size: "200"})
 *     LiveChordI18n.applyDom(document.body)   // patch all annotated nodes
 *     LiveChordI18n.setLang("en")             // change language at runtime
 *
 * The fallback inside [data-i18n] is the literal element text; this means
 * pages render correctly even before i18n.js loads (no FOUC of empty labels).
 */
(function () {
  "use strict";

  const SUPPORTED = ["en", "zh-TW"];
  const DEFAULT_LANG = "en";
  const STORAGE_KEY = "livechord_lang";

  const _dicts = {}; // { lang: { "key.path": "value" } }
  let _lang = DEFAULT_LANG;

  function _detectLang() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && SUPPORTED.indexOf(saved) >= 0) return saved;
      const nav = (navigator.language || navigator.userLanguage || "")
        .toLowerCase();
      // Treat any zh-* as zh-TW for now (we only ship Hant). Otherwise default.
      if (nav.startsWith("zh")) return "zh-TW";
      return DEFAULT_LANG;
    } catch (_e) {
      return DEFAULT_LANG;
    }
  }

  async function _loadLang(lang) {
    if (_dicts[lang]) return _dicts[lang];
    try {
      const resp = await fetch(`/i18n/${lang}.json`, { cache: "force-cache" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      _dicts[lang] = await resp.json();
    } catch (e) {
      console.warn(`[i18n] failed to load ${lang}.json:`, e);
      _dicts[lang] = {};
    }
    return _dicts[lang];
  }

  function _interpolate(s, vars) {
    if (!vars) return s;
    return s.replace(/\{(\w+)\}/g, (m, k) =>
      Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m
    );
  }

  function t(key, vars) {
    if (!key) return "";
    const d = _dicts[_lang] || {};
    const v = d[key];
    if (typeof v === "string") return _interpolate(v, vars);
    // Fall back to English if the active dict is missing this key but the
    // English dict has it — keeps mixed content readable instead of raw keys.
    if (_lang !== DEFAULT_LANG && _dicts[DEFAULT_LANG]) {
      const fallback = _dicts[DEFAULT_LANG][key];
      if (typeof fallback === "string") return _interpolate(fallback, vars);
    }
    return key;
  }

  function applyDom(root) {
    root = root || document;
    // Text content
    root.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (!key) return;
      // Honor optional `data-i18n-vars='{"size":"200"}'` for interpolation
      let vars;
      const v = el.getAttribute("data-i18n-vars");
      if (v) {
        try { vars = JSON.parse(v); } catch (_e) { /* ignore */ }
      }
      const out = t(key, vars);
      // If the key resolved to itself (untranslated) AND the element has a
      // non-empty original child text, leave the original alone — better to
      // show the fallback than the raw key.
      if (out === key && el.textContent && el.textContent.trim()) return;
      el.textContent = out;
    });
    // Attribute substitutions (placeholder, title, aria-label, ...)
    // Format: data-i18n-attr="placeholder=key.path,title=other.key"
    root.querySelectorAll("[data-i18n-attr]").forEach((el) => {
      const spec = el.getAttribute("data-i18n-attr");
      if (!spec) return;
      spec.split(",").forEach((pair) => {
        const [attr, key] = pair.split("=").map((s) => s.trim());
        if (!attr || !key) return;
        const out = t(key);
        if (out === key) return; // skip untranslated to preserve fallback
        el.setAttribute(attr, out);
      });
    });
  }

  async function setLang(lang) {
    if (SUPPORTED.indexOf(lang) < 0) return;
    _lang = lang;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (_e) {}
    document.documentElement.lang = lang === "zh-TW" ? "zh-Hant" : lang;
    await _loadLang(lang);
    // Always preload English in the background so fallback() works after
    // a switch to a non-default language with missing keys.
    if (lang !== DEFAULT_LANG && !_dicts[DEFAULT_LANG]) {
      _loadLang(DEFAULT_LANG);
    }
    applyDom(document);
    document.dispatchEvent(
      new CustomEvent("livechord:langchange", { detail: { lang } })
    );
  }

  // Boot: load detected language synchronously-as-possible. Pages that load
  // i18n.js with `defer` get a single applyDom pass before user-visible work.
  async function _boot() {
    _lang = _detectLang();
    document.documentElement.lang = _lang === "zh-TW" ? "zh-Hant" : _lang;
    await _loadLang(_lang);
    if (_lang !== DEFAULT_LANG) _loadLang(DEFAULT_LANG); // background
    applyDom(document);
    document.dispatchEvent(
      new CustomEvent("livechord:i18nready", { detail: { lang: _lang } })
    );
  }

  window.LiveChordI18n = {
    t,
    applyDom,
    setLang,
    getLang: () => _lang,
    SUPPORTED: SUPPORTED.slice(),
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _boot);
  } else {
    _boot();
  }
})();
