(function () {
  var GA_ID = 'G-BWKF9LQM6T';
  var UTM_KEY = 'livechord_attribution';
  var UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];
  var host = location.hostname;
  var isProd = host === 'livechord.org' || host === 'www.livechord.org';

  function readStoredAttribution() {
    try {
      return JSON.parse(sessionStorage.getItem(UTM_KEY) || localStorage.getItem(UTM_KEY) || '{}') || {};
    } catch (_e) {
      return {};
    }
  }

  function writeAttribution(attribution) {
    try { sessionStorage.setItem(UTM_KEY, JSON.stringify(attribution)); } catch (_e) {}
    try { localStorage.setItem(UTM_KEY, JSON.stringify(attribution)); } catch (_e) {}
  }

  function captureAttribution() {
    var params = new URLSearchParams(location.search || '');
    var found = false;
    var attribution = {};
    UTM_KEYS.forEach(function (key) {
      var value = params.get(key);
      if (value) {
        attribution[key] = value.slice(0, 120);
        found = true;
      }
    });
    var ref = document.referrer || '';
    if (ref) attribution.referrer = ref.slice(0, 300);
    attribution.landing_path = (location.pathname + location.search).slice(0, 300);
    attribution.landing_ts = Date.now();

    if (found || ref) {
      writeAttribution(attribution);
      return attribution;
    }
    return readStoredAttribution();
  }

  function withAttribution(payload) {
    var out = {};
    var attr = readStoredAttribution();
    Object.keys(attr).forEach(function (key) { out[key] = attr[key]; });
    Object.keys(payload || {}).forEach(function (key) {
      var value = payload[key];
      if (value === undefined || value === null) return;
      out[key] = typeof value === 'string' ? value.slice(0, 300) : value;
    });
    return out;
  }

  window.LiveChordAnalytics = {
    attribution: captureAttribution(),
    track: function (eventName, payload) {
      if (!eventName) return;
      var data = withAttribution(payload || {});
      if (typeof window.gtag === 'function') {
        window.gtag('event', eventName, data);
      }
    }
  };

  document.addEventListener('click', function (e) {
    var link = e.target && e.target.closest ? e.target.closest('a[href*="github.com/JJ110112/LiveChord"]') : null;
    if (!link || !window.LiveChordAnalytics) return;
    window.LiveChordAnalytics.track('github_click', {
      link_url: link.href || '',
      link_text: (link.textContent || '').trim(),
      page_path: location.pathname
    });
  }, true);

  if (!isProd) return;

  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_ID;
  document.head.appendChild(s);

  window.dataLayer = window.dataLayer || [];
  function gtag(){ dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag('js', new Date());
  gtag('config', GA_ID, withAttribution({ anonymize_ip: true }));
})();
