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
