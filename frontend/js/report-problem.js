/**
 * Shared in-site problem report modal for public pages that do not load the
 * full player UI. auth.js injects Authorization or X-Anon-Id for /api calls.
 */
(function () {
  "use strict";

  const SUPPORT_EMAIL = "livechordcookie@gmail.com";

  function _t(key, vars, fallback) {
    if (window.LiveChordI18n && window.LiveChordI18n.t) {
      const out = window.LiveChordI18n.t(key, vars);
      if (out && out !== key) return out;
    }
    return fallback || key;
  }

  function _status(text, kind) {
    const el = document.getElementById("siteReportStatus");
    if (!el) return;
    el.textContent = text || "";
    el.dataset.kind = kind || "";
  }

  function _reportPayload() {
    return {
      category: document.getElementById("siteReportCategory").value || "other",
      description: document.getElementById("siteReportDescription").value.trim(),
      contact: document.getElementById("siteReportContact").value.trim(),
      website: document.getElementById("siteReportWebsite").value.trim(),
      page_url: window.location.href,
      browser_info: navigator.userAgent || "",
    };
  }

  async function _submitReport() {
    const btn = document.getElementById("siteReportSubmit");
    const payload = _reportPayload();
    if (!payload.description) {
      _status(_t("toast.bug.describe_first", null, "Please describe the issue"), "error");
      return;
    }
    btn.disabled = true;
    _status(_t("report.submit_sending", null, "Sending..."), "busy");
    try {
      const res = await fetch("/api/feedback/bug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = "";
        try {
          const data = await res.json();
          detail = data && data.detail ? String(data.detail) : "";
        } catch (_) {}
        throw new Error(detail || `${res.status} ${res.statusText}`);
      }
      _status(_t("toast.bug.thanks", null, "Thanks for the report!"), "ok");
      document.getElementById("siteReportDescription").value = "";
      document.getElementById("siteReportContact").value = "";
      if (window.LiveChordAnalytics) {
        window.LiveChordAnalytics.track("report_problem_submit", {
          category: payload.category,
          source: "site",
        });
      }
      setTimeout(_closeReport, 900);
    } catch (e) {
      _status(_t("toast.bug.submit_failed", { err: e.message }, `Report submission failed: ${e.message}`), "error");
    } finally {
      btn.disabled = false;
    }
  }

  function _copyEmail() {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(SUPPORT_EMAIL)
        .then(() => _status(_t("toast.email_copied", null, "Email copied"), "ok"))
        .catch(() => _status(SUPPORT_EMAIL, "ok"));
      return;
    }
    _status(SUPPORT_EMAIL, "ok");
  }

  function _ensureModal() {
    let modal = document.getElementById("siteReportDialog");
    if (modal) return modal;
    document.body.insertAdjacentHTML("beforeend", `
      <div id="siteReportDialog" class="site-report-dialog" style="display:none">
        <div class="site-report-box" role="dialog" aria-modal="true" aria-labelledby="siteReportTitle">
          <button id="siteReportClose" class="site-report-close" type="button" data-i18n-attr="aria-label=common.close" aria-label="Close">&times;</button>
          <h2 id="siteReportTitle" data-i18n="player.bug.title">Report a problem</h2>
          <select id="siteReportCategory" class="site-report-input">
            <option value="ui" data-i18n="player.bug.category.ui">UI issue</option>
            <option value="upload" data-i18n="player.bug.category.upload">Upload problem</option>
            <option value="login" data-i18n="player.bug.category.login">Login problem</option>
            <option value="accuracy" data-i18n="player.bug.category.accuracy">Chord accuracy</option>
            <option value="performance" data-i18n="player.bug.category.performance">Performance</option>
            <option value="feature_request" data-i18n="player.bug.category.feature_request">Feature request</option>
            <option value="other" data-i18n="player.bug.category.other">Other</option>
          </select>
          <textarea id="siteReportDescription" class="site-report-input" rows="5" placeholder="Describe the issue or suggestion..." data-i18n-attr="placeholder=player.bug.description_placeholder"></textarea>
          <input id="siteReportContact" class="site-report-input" type="text" autocomplete="email" inputmode="email" placeholder="Email or contact (optional)" data-i18n-attr="placeholder=player.bug.contact_placeholder">
          <input id="siteReportWebsite" class="site-report-honeypot" type="text" tabindex="-1" autocomplete="off" aria-hidden="true">
          <div class="site-report-fallbacks">
            <button id="siteReportCopyEmail" type="button" class="site-report-link" data-i18n="player.bug.copy_email">Copy email</button>
            <a class="site-report-link" href="https://mail.google.com/mail/?view=cm&fs=1&to=livechordcookie@gmail.com&su=LiveChord%20bug%20report" target="_blank" rel="noopener noreferrer" data-i18n="player.bug.open_gmail">Open Gmail</a>
            <a class="site-report-link" href="https://outlook.live.com/mail/0/deeplink/compose?to=livechordcookie@gmail.com&subject=LiveChord%20bug%20report" target="_blank" rel="noopener noreferrer" data-i18n="player.bug.open_outlook">Open Outlook web</a>
          </div>
          <div id="siteReportStatus" class="site-report-status" aria-live="polite"></div>
          <div class="site-report-actions">
            <button id="siteReportSubmit" class="site-report-submit" type="button" data-i18n="player.bug.submit">Submit</button>
            <button id="siteReportCancel" class="site-report-cancel" type="button" data-i18n="player.bug.cancel">Cancel</button>
          </div>
        </div>
      </div>
    `);
    modal = document.getElementById("siteReportDialog");
    document.getElementById("siteReportClose").addEventListener("click", _closeReport);
    document.getElementById("siteReportCancel").addEventListener("click", _closeReport);
    document.getElementById("siteReportSubmit").addEventListener("click", _submitReport);
    document.getElementById("siteReportCopyEmail").addEventListener("click", _copyEmail);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) _closeReport();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal.style.display !== "none") _closeReport();
    });
    if (window.LiveChordI18n) window.LiveChordI18n.applyDom(modal);
    return modal;
  }

  function _openReport(category) {
    const modal = _ensureModal();
    const cat = document.getElementById("siteReportCategory");
    if (category && cat.querySelector(`option[value="${category}"]`)) cat.value = category;
    _status("", "");
    modal.style.display = "flex";
    setTimeout(() => document.getElementById("siteReportDescription").focus(), 0);
  }

  function _closeReport() {
    const modal = document.getElementById("siteReportDialog");
    if (modal) modal.style.display = "none";
  }

  document.addEventListener("click", (e) => {
    const trigger = e.target && e.target.closest ? e.target.closest("[data-report-problem]") : null;
    if (!trigger) return;
    e.preventDefault();
    _openReport(trigger.getAttribute("data-report-category") || "");
  });
  document.addEventListener("livechord:langchange", () => {
    const modal = document.getElementById("siteReportDialog");
    if (modal && window.LiveChordI18n) window.LiveChordI18n.applyDom(modal);
  });

  window.LiveChordReportProblem = { open: _openReport, close: _closeReport };
})();
