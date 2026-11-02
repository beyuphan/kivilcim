// Registers the service worker and warns when the device is offline.
(function () {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* the site works without it */ });
    });
  }

  var bar = null;
  function show(offline) {
    if (offline && !bar) {
      bar = document.createElement("div");
      bar.setAttribute("role", "status");
      bar.textContent = "Çevrimdışısınız. Ekrandaki bilgiler güncel olmayabilir; bağlantı gelince yenilenir. "
        + "Acil durumda 112'yi arayın.";
      bar.style.cssText = "position:sticky;top:0;z-index:2000;background:#b45309;color:#fff;padding:6px 12px;font-size:13px;text-align:center";
      document.body.insertBefore(bar, document.body.firstChild);
    } else if (!offline && bar) {
      bar.remove();
      bar = null;
    }
  }
  window.addEventListener("offline", function () { show(true); });
  window.addEventListener("online", function () { show(false); });
  if (navigator.onLine === false) window.addEventListener("DOMContentLoaded", function () { show(true); });
})();
