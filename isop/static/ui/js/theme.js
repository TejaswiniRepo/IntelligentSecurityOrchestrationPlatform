// Applies the saved theme (or the OS preference) before first paint, so dark mode never flashes white.
// Loaded as a classic blocking script from <head>; the toggle itself lives in app.js.
(function () {
  var saved = null;
  try { saved = localStorage.getItem("isop.theme"); } catch (e) { /* storage blocked: follow the OS */ }
  var dark = saved ? saved === "dark" : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
