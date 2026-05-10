/**
 * LiveChord auth + fetch wrapper (Phase A — public mode anon support)
 *
 * Loaded BEFORE any other API calls. Responsibilities:
 *
 *   1. Mint / persist a per-browser anonymous identity (X-Anon-Id), used by
 *      the backend to key audit/quota for not-logged-in callers.
 *   2. Install a global fetch override that injects either Authorization
 *      (logged-in user) or X-Anon-Id (anonymous) on every same-origin /api/
 *      request.
 *   3. Decide bootstrap behaviour by deployment mode:
 *        - public: anon OK, NO redirect-to-login on missing token
 *        - personal / beta: legacy "no token → /login" redirect
 *      Mode is cached in localStorage (`livechord_mode_hint`) so subsequent
 *      page loads decide synchronously; refreshed in the background each load.
 *   4. 401 handling: in public, surface to the caller so the page can render
 *      "Login to unlock favorites" UI; in beta/personal, evict stale token
 *      and bounce to /login (legacy behaviour).
 *
 * This file replaces the inline <script> blocks at the top of index.html /
 * player.html that did mode-blind login redirects.
 */
(function () {
  "use strict";

  const ANON_KEY = "livechord_anon_id";
  const TOKEN_KEY = "livechord_token";
  const USERNAME_KEY = "livechord_username";
  const DISPLAY_NAME_KEY = "livechord_display_name";
  const MODE_HINT_KEY = "livechord_mode_hint";

  // Anon ID format must match backend _ANON_ID_RE: 8-32 chars [A-Za-z0-9_-].
  const ANON_FMT = /^[A-Za-z0-9_-]{8,32}$/;

  function _genAnonId() {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID().replace(/-/g, "").slice(0, 24);
    }
    // Fallback for older browsers — sufficient entropy for our use.
    return (
      Math.random().toString(36).slice(2, 14) +
      Date.now().toString(36)
    ).slice(0, 24);
  }

  function getAnonId() {
    let id = localStorage.getItem(ANON_KEY);
    if (!id || !ANON_FMT.test(id)) {
      id = _genAnonId();
      localStorage.setItem(ANON_KEY, id);
    }
    return id;
  }

  function getModeHint() {
    return localStorage.getItem(MODE_HINT_KEY) || "unknown";
  }

  function setModeHint(mode) {
    if (mode === "personal" || mode === "beta" || mode === "public") {
      localStorage.setItem(MODE_HINT_KEY, mode);
      window._lcDeploymentMode = mode;
    }
  }

  // Initialise window flag from cache so other scripts can read it synchronously.
  window._lcDeploymentMode = getModeHint();

  // ── OAuth token capture (Phase B) ────────────────────────────────────────
  // The OAuth callback (backend/oauth_api.py) bounces the browser to
  // `/?oauth_done=1#token=<t>&username=<u>`. We capture from the URL fragment
  // (which the backend never sees) and persist into localStorage, then strip
  // the fragment so the URL is clean and the token doesn't leak via screenshot
  // or browser history. Runs synchronously before any /api/ call so the very
  // first request after sign-in carries the new token.
  (function _captureOAuthFragment() {
    const h = (window.location.hash || "").replace(/^#/, "");
    if (!h) return;
    const params = new URLSearchParams(h);
    const tk = params.get("token");
    const un = params.get("username");
    if (!tk || !un) return;
    if (!/^[A-Za-z0-9_\-]{16,}$/.test(tk)) return;
    localStorage.setItem(TOKEN_KEY, tk);
    localStorage.setItem(USERNAME_KEY, un);
    const dn = params.get("display");
    if (dn) localStorage.setItem(DISPLAY_NAME_KEY, dn);
    // Strip the fragment without reloading.
    try {
      const clean = window.location.pathname + window.location.search;
      history.replaceState(null, "", clean);
    } catch (e) {
      window.location.hash = "";
    }
  })();

  // Backfill display_name for users who logged in before the URL fragment
  // started carrying it (or anyone whose localStorage got cleared). One /me
  // call per page load when missing.
  function _backfillDisplayName() {
    if (!localStorage.getItem(TOKEN_KEY)) return;
    if (localStorage.getItem(DISPLAY_NAME_KEY)) return;
    fetch("/api/auth/oauth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && data.display_name) {
          localStorage.setItem(DISPLAY_NAME_KEY, data.display_name);
          // Notify any greeting-renderer that an updated name is available.
          document.dispatchEvent(
            new CustomEvent("livechord:profile", { detail: data })
          );
        }
      })
      .catch(() => {});
  }

  // ── fetch wrapper ────────────────────────────────────────────────────────
  const _origFetch = window.fetch;
  window.fetch = async function (resource, config) {
    if (config == null) config = {};
    const url =
      typeof resource === "string"
        ? resource
        : resource instanceof Request
        ? resource.url
        : "";
    const sameOrigin =
      url.startsWith("/") || url.includes(window.location.host);
    if (sameOrigin) {
      if (config.headers == null) config.headers = {};
      const token = localStorage.getItem(TOKEN_KEY);
      if (token) {
        config.headers["Authorization"] = "Bearer " + token;
      } else {
        // Always send anon id when there's no token. Backend ignores when
        // a valid token is present, so it's safe to send both — but when
        // the user is logged in we omit it for cleaner logs.
        config.headers["X-Anon-Id"] = getAnonId();
      }
    }
    const res = await _origFetch(resource, config);
    if (
      res.status === 401 &&
      url.includes("/api/") &&
      !url.includes("/api/auth/login") &&
      !url.includes("/api/auth/oauth")
    ) {
      const mode = window._lcDeploymentMode;
      if (mode === "public" || mode === "unknown") {
        // public: anonymous calling a login-required endpoint. Page-level
        //   UI handles this (e.g. "Login to rate"); do not auto-redirect.
        // unknown: bootstrap probe still in flight (first-time visitor or
        //   cache cleared, with no `livechord_mode_hint` cached). If the
        //   site IS public, redirecting here causes an instant bounce
        //   from `/` → `/login` even though the visitor is allowed
        //   anonymous. Swallow the 401 silently; the next /api/ call after
        //   the probe resolves will route correctly.
        return res;
      }
      // beta / personal: stale token — clear and bounce. BUT not when we're
      // already on /login: setting `location.href = "/login"` from /login
      // causes a reload, auth.js re-runs, /api/auth/is_admin returns 401
      // again → infinite loop manifesting as a "two-page bounce" in the UI.
      // Login page handles its own auth gating.
      if (window.location.pathname === "/login") {
        return res;
      }
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USERNAME_KEY);
      window.location.href = "/login";
    }
    return res;
  };

  // ── deployment mode probe + bootstrap login redirect ─────────────────────
  // We always refresh the cached mode in the background. The first decision
  // (whether to redirect to /login) uses the cached hint synchronously to
  // avoid a flash; corrected below if the live answer differs.
  function _bootstrapAuth() {
    const cachedMode = getModeHint();
    const hasToken = !!localStorage.getItem(TOKEN_KEY);

    // If we have a token, no bootstrap redirect ever.
    if (hasToken) return;

    // We're on the login page itself — never auto-redirect to it (would
    // ping-pong on a 401). Refresh the mode hint quietly so login.html's
    // own UI logic (OAuth vs legacy form swap) sees a fresh value.
    if (window.location.pathname === "/login") {
      _refreshModeHint();
      if (cachedMode === "public") getAnonId();
      return;
    }

    // Fast path: cached mode is public → mint anon and stay.
    if (cachedMode === "public") {
      getAnonId();
      // still refresh the hint async in case backend was reconfigured
      _refreshModeHint();
      return;
    }

    // Cached mode is beta/personal/unknown → legacy: bounce on 401.
    // First confirm via /api/config/public so we don't bounce a public-mode
    // visitor whose cache is empty.
    fetch("/api/config/public")
      .then((r) => r.json())
      .then((cfg) => {
        const mode = (cfg && cfg.deployment_mode) || "personal";
        setModeHint(mode);
        if (mode === "public") {
          getAnonId();
          return;
        }
        // beta / personal: try is_admin (catches LAN bypass on personal),
        // else send to /login.
        return fetch("/api/auth/is_admin").then((r) => {
          if (!r.ok) window.location.href = "/login";
        });
      })
      .catch(() => {
        // /api/config/public failed — conservative fallback to /login,
        // preserves legacy behaviour for sites that aren't reachable.
        window.location.href = "/login";
      });
  }

  function _refreshModeHint() {
    fetch("/api/config/public")
      .then((r) => r.json())
      .then((cfg) => {
        if (cfg && cfg.deployment_mode) setModeHint(cfg.deployment_mode);
      })
      .catch(() => {});
  }

  // Expose for callers (rating prompts, etc.)
  window.LiveChordAuth = {
    getAnonId,
    // In personal mode there is no anonymous-user concept: LAN clients are
    // auto-admin via the backend's LAN bypass (auth_api.get_current_user),
    // and non-LAN callers without a token will be redirected to /login by
    // the 401 handler above. Treating the no-token-on-LAN case as
    // "anonymous" causes UI gates (e.g. favorite, rating) to short-circuit
    // with "sign in" toasts even though the backend would happily save —
    // bug surfaced on NUC 8800 when the favorite click did nothing.
    isAnonymous: () => {
      const mode = window._lcDeploymentMode || getModeHint();
      if (mode === "personal") return false;
      return !localStorage.getItem(TOKEN_KEY);
    },
    getMode: () => window._lcDeploymentMode || getModeHint(),
    refreshMode: _refreshModeHint,
    getDisplayName: () =>
      localStorage.getItem(DISPLAY_NAME_KEY) ||
      localStorage.getItem(USERNAME_KEY) ||
      "",
  };

  _bootstrapAuth();
  _backfillDisplayName();
})();
