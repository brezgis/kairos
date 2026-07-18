// Kairos localStorage -> backend sync bridge.
// Injected at serve time by the FastAPI backend; the app bundle stays untouched.
// Captures all `kairos:*` localStorage keys (insights, prefs, per-day check-ins)
// and POSTs them to /sync so they persist in SQLite.
(function () {
  // One-shot reset: open /?reset=1 to wipe local Kairos data (no console needed).
  if (location.search.indexOf("reset=1") !== -1) {
    Object.keys(localStorage)
      .filter(function (k) { return k.indexOf("kairos:") === 0; })
      .forEach(function (k) { localStorage.removeItem(k); });
    location.replace("/");
    return;
  }

  // Oracle bridge: provide window.claude.complete so the app's generateOracle()
  // routes through the Kairos backend (agent-generate: Claude Code -> local LLM).
  if (!window.claude) window.claude = {};
  if (!window.claude.complete) {
    window.claude.complete = function (prompt) {
      var d = new Date();
      var day = d.getFullYear() + "-" +
        String(d.getMonth() + 1).padStart(2, "0") + "-" +
        String(d.getDate()).padStart(2, "0");
      // the app stores the day's entry under a non-zero-padded key (kairos:YYYY-M-D)
      var dKey = "kairos:" + d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
      var entry = null;
      try { entry = JSON.parse(localStorage.getItem(dKey) || "null"); } catch (e) {}
      return fetch("/oracle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ day: day, prompt: prompt, entry: entry }),
      }).then(function (r) {
        if (!r.ok) throw new Error("oracle " + r.status);
        return r.json();
      }).then(function (j) { return j.text; });
    };
  }

  function collect() {
    var out = {};
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (k && k.indexOf("kairos:") === 0) out[k] = localStorage.getItem(k);
    }
    return out;
  }

  function sync() {
    var data = collect();
    if (!Object.keys(data).length) return;
    try {
      fetch("/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: data }),
        keepalive: true,
      }).catch(function () {});
    } catch (e) {}
  }

  setTimeout(sync, 3000);     // once the app has hydrated
  setInterval(sync, 60000);   // periodically while open
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) sync();
  });
  window.addEventListener("beforeunload", function () {
    try {
      navigator.sendBeacon(
        "/sync",
        new Blob([JSON.stringify({ entries: collect() })], { type: "application/json" })
      );
    } catch (e) {
      sync();
    }
  });
})();
