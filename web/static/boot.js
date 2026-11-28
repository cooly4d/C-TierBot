// The loading screen: the first page of a visit (once per browser tab session) shows the spinning tesseract for
// a moment, then the glass clears. Runs in <head> before the first paint, so the screen never flashes in late.
// Nothing shows with reduced motion, when storage is off, or without JavaScript; it never stays up past 2.4 s.
(function () {
  var root = document.documentElement;
  // "Detailed" (the sidebar checkbox), set before the first paint so rows don't change after the page appears.
  try {
    if (window.localStorage.getItem("ctb-detailed") === "1") root.classList.add("detailed");
  } catch (e) { /* storage off: compact */ }
  try {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (window.sessionStorage.getItem("ctb-seen")) return;
    window.sessionStorage.setItem("ctb-seen", "1");
  } catch (e) {
    return;
  }
  root.classList.add("booting");
  new Image().src = "/static/tesseract.webp"; // start fetching now; the CSS background uses the same file
  var start = Date.now(), finished = false;
  function finish() {
    if (finished) return;
    finished = true;
    setTimeout(function () {
      root.classList.add("booted"); // the glass clears (site.css)
      setTimeout(function () { root.classList.remove("booting", "booted"); }, 560);
    }, Math.max(0, 1100 - (Date.now() - start)));
  }
  if (document.readyState === "complete") finish();
  else window.addEventListener("load", finish);
  setTimeout(finish, 1300); // a slow image or font never holds the page back
})();
