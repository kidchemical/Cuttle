'use strict';
// Pure routing decision for window.open requests from the Cuttle shell.
// Returns:
//   'in-app'   — same-origin app page (query log inspector, reports) or
//                harmless non-navigating targets; open as an Electron window.
//   'external' — anything else over http(s); open in the OS default browser.
//   'deny'     — unparseable URL; open nothing.
function routeWindowOpen(url, appOrigin) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch (_) {
    return 'deny';
  }
  if (parsed.protocol === 'about:') return 'in-app';
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return 'external';
  try {
    if (appOrigin && parsed.origin === new URL(appOrigin).origin) return 'in-app';
  } catch (_) {}
  return 'external';
}

module.exports = { routeWindowOpen };
