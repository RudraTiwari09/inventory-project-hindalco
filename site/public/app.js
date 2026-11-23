(function () {
  var appUrl = "__APP_URL__";
  var box = document.getElementById("status");
  var text = document.getElementById("status-text");
  function set(state, msg) { box.setAttribute("data-state", state); text.textContent = msg; }
  if (!window.fetch) { set("unknown", "Use the button above to open the app."); return; }
  var timer = setTimeout(function () {
    set("slow", "The app is waking up or slow to respond. Try opening it, the first load can take up to a minute.");
  }, 12000);
  // no-cors: an opaque response still proves the host is reachable through the proxy.
  fetch(appUrl + "/_stcore/health", { mode: "no-cors", cache: "no-store" })
    .then(function () { clearTimeout(timer); set("ok", "The app is reachable from your network."); })
    .catch(function () { clearTimeout(timer); set("blocked", "The app host could not be reached. It may be blocked by your network or still starting up."); });
})();
