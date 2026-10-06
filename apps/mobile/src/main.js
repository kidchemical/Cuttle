import './setup.css';
import { App } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';
import { SafeArea, SystemBarsStyle } from '@capacitor-community/safe-area';
import mascotUrl from '../assets/icon/mascot.png';

/**
 * Connection settings UI for the native Cuttle mobile shell.
 * Mirrors Electron: the native WebView loads app_shell from the PC —
 * this page is only first-run / server settings, not a bookmark redirect.
 */

const PREFS = {
  host: 'cuttle_host',
  port: 'cuttle_port',
  https: 'cuttle_https',
  baseUrl: 'cuttle_base_url',
  notify: 'cuttle_notify_enabled',
  token: 'cuttle_mobile_token',
  recentHosts: 'cuttle_recent_hosts',
};

const DEFAULT_PORT_HTTP = 8000;
const DEFAULT_PORT_HTTPS = 8888;
const MAX_RECENT_HOSTS = 8;

const $ = (sel) => document.querySelector(sel);

function nativeShell() {
  return typeof window.CuttleShellNative !== 'undefined' ? window.CuttleShellNative : null;
}

export function cleanHost(host) {
  return String(host || '')
    .trim()
    .replace(/^https?:\/\//i, '')
    .split('/')[0]
    .split(':')[0]
    .trim();
}

export function buildBaseUrl(host, port, useHttps) {
  const scheme = useHttps ? 'https' : 'http';
  return `${scheme}://${cleanHost(host)}:${port}`;
}

export function cuttleEntryUrl(baseUrl) {
  const base = baseUrl.replace(/\/$/, '');
  return `${base}/app_shell.html`;
}

/**
 * Strict port parsing for user-entered ports. Returns an integer 1–65535,
 * or NaN when the input is not a valid port. Empty means unset (caller
 * applies the default); anything else malformed is rejected, never clamped
 * or silently replaced — clamping would connect to a port the user did not
 * choose.
 */
export function parsePortNumber(raw) {
  if (typeof raw === 'number') {
    return Number.isInteger(raw) ? raw : NaN;
  }
  const text = String(raw ?? '').trim();
  if (!text) return NaN;
  if (!/^\d+$/.test(text)) return NaN;
  return Number(text);
}

export function isValidPort(port) {
  return Number.isInteger(port) && port >= 1 && port <= 65535;
}

export function recentHostKey(entry) {
  const host = cleanHost(entry?.host);
  const port = Number(entry?.port) || DEFAULT_PORT_HTTP;
  const https = entry?.useHttps ? 1 : 0;
  return `${host}|${port}|${https}`;
}

export function upsertRecentHost(list, host, port, useHttps) {
  const cleaned = cleanHost(host);
  if (!cleaned) return Array.isArray(list) ? list.slice(0, MAX_RECENT_HOSTS) : [];
  const next = {
    host: cleaned,
    port: Number(port) || DEFAULT_PORT_HTTP,
    useHttps: !!useHttps,
    at: Date.now(),
  };
  const key = recentHostKey(next);
  const prior = Array.isArray(list) ? list : [];
  return [next, ...prior.filter((e) => recentHostKey(e) !== key)].slice(0, MAX_RECENT_HOSTS);
}

async function saveConfig(host, port, useHttps, baseUrl, notifyEnabled, token) {
  await Preferences.set({ key: PREFS.host, value: host });
  await Preferences.set({ key: PREFS.port, value: String(port) });
  await Preferences.set({ key: PREFS.https, value: useHttps ? '1' : '0' });
  await Preferences.set({ key: PREFS.notify, value: notifyEnabled ? '1' : '0' });
  await Preferences.set({ key: PREFS.token, value: token || '' });
  await Preferences.set({ key: PREFS.baseUrl, value: baseUrl });
}

async function loadConfig() {
  const [host, port, https, baseUrl, notify, token] = await Promise.all([
    Preferences.get({ key: PREFS.host }),
    Preferences.get({ key: PREFS.port }),
    Preferences.get({ key: PREFS.https }),
    Preferences.get({ key: PREFS.baseUrl }),
    Preferences.get({ key: PREFS.notify }),
    Preferences.get({ key: PREFS.token }),
  ]);
  // A missing stored port is a legacy default (8000). A present-but-invalid
  // stored port is returned as-is so the form shows it and every action
  // blocks until it is corrected — never silently swapped for another target
  // while the saved base URL still names the old one.
  const rawStored = port.value;
  const storedPort =
    rawStored == null || String(rawStored).trim() === ''
      ? DEFAULT_PORT_HTTP
      : parsePortNumber(rawStored);
  return {
    host: host.value || '',
    port: storedPort,
    useHttps: https.value === '1',
    baseUrl: baseUrl.value || '',
    notifyEnabled: notify.value !== '0',
    token: token.value || '',
  };
}

async function loadRecentHosts() {
  const raw = await Preferences.get({ key: PREFS.recentHosts });
  try {
    const list = JSON.parse(raw.value || '[]');
    if (!Array.isArray(list)) return [];
    return list
      .filter((e) => e && cleanHost(e.host))
      .map((e) => ({
        host: cleanHost(e.host),
        port: Number(e.port) || DEFAULT_PORT_HTTP,
        useHttps: !!e.useHttps,
        at: Number(e.at) || 0,
      }))
      .slice(0, MAX_RECENT_HOSTS);
  } catch {
    return [];
  }
}

async function saveRecentHosts(list) {
  await Preferences.set({
    key: PREFS.recentHosts,
    value: JSON.stringify((list || []).slice(0, MAX_RECENT_HOSTS)),
  });
}

async function rememberHost(host, port, useHttps) {
  const list = upsertRecentHost(await loadRecentHosts(), host, port, useHttps);
  await saveRecentHosts(list);
  return list;
}

async function removeRecentHost(entry) {
  const key = recentHostKey(entry);
  const list = (await loadRecentHosts()).filter((e) => recentHostKey(e) !== key);
  await saveRecentHosts(list);
  return list;
}

function formatRecentMeta(entry) {
  const scheme = entry.useHttps ? 'https' : 'http';
  return `${scheme} · port ${entry.port}`;
}

function renderRecentHosts(list) {
  const wrap = $('#recent-hosts');
  const el = $('#recent-hosts-list');
  if (!wrap || !el) return;
  el.innerHTML = '';
  if (!list || !list.length) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  for (const entry of list) {
    const row = document.createElement('div');
    row.className = 'recent-row';
    row.setAttribute('role', 'listitem');

    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'recent-pick';
    pick.title = `Use ${entry.host}`;
    pick.innerHTML =
      `<span class="recent-pick-host"></span>` +
      `<span class="recent-pick-meta"></span>`;
    pick.querySelector('.recent-pick-host').textContent = entry.host;
    pick.querySelector('.recent-pick-meta').textContent = formatRecentMeta(entry);
    pick.addEventListener('click', () => {
      populateForm({
        host: entry.host,
        port: entry.port,
        useHttps: entry.useHttps,
        notifyEnabled: $('#notify-enabled')?.checked !== false,
        token: ($('#mobile-token')?.value || '').trim(),
      });
      clearStatus();
      showStatus(`Filled ${entry.host} — tap Connect or Test.`, 'ok');
    });

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'recent-remove';
    remove.setAttribute('aria-label', `Remove ${entry.host}`);
    remove.textContent = '×';
    remove.addEventListener('click', async () => {
      const next = await removeRecentHost(entry);
      renderRecentHosts(next);
    });

    row.appendChild(pick);
    row.appendChild(remove);
    el.appendChild(row);
  }
}

function showStatus(message, kind = 'ok') {
  const el = $('#status');
  const text = $('#status-text');
  if (!el || !text) return;
  el.hidden = false;
  el.className = `status ${kind}`;
  text.textContent = message;
}

function clearStatus() {
  const el = $('#status');
  if (el) el.hidden = true;
}

function readForm() {
  const host = cleanHost($('#host')?.value || '');
  const preset = $('#port-preset')?.value || '8000';
  let port = DEFAULT_PORT_HTTP;
  let useHttps = $('#use-https')?.checked || false;

  if (preset === 'custom') {
    // Empty custom is unset, not 8000: callers reject it and ask for a port.
    // The 8000 preset remains the supported default.
    port = parsePortNumber($('#port-custom')?.value);
  } else {
    port = Number(preset);
  }

  if (port === DEFAULT_PORT_HTTPS) {
    useHttps = true;
  }

  return {
    host,
    port,
    useHttps,
    notifyEnabled: $('#notify-enabled')?.checked !== false,
    token: ($('#mobile-token')?.value || '').trim(),
  };
}

function syncSchemeUi() {
  const { port, useHttps } = readForm();
  const httpsBox = $('#use-https');
  const hint = $('#scheme-hint');
  const customWrap = $('#custom-port-wrap');
  const preset = $('#port-preset')?.value;

  if (customWrap) {
    customWrap.classList.toggle('hidden', preset !== 'custom');
  }

  if (httpsBox) {
    httpsBox.disabled = port === DEFAULT_PORT_HTTPS;
    if (port === DEFAULT_PORT_HTTPS) {
      httpsBox.checked = true;
    }
  }

  const scheme = useHttps || port === DEFAULT_PORT_HTTPS ? 'https' : 'http';
  if (hint) {
    hint.innerHTML =
      scheme === 'https'
        ? 'Using <strong>https</strong> — Android requires a trusted certificate matching the server address.'
        : 'Using <strong>http</strong> (LAN or Tailscale tunnel).';
  }
}

async function testConnection(baseUrl) {
  const pingUrl = `${baseUrl.replace(/\/$/, '')}/api/lan-ping`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(pingUrl, { signal: controller.signal });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    if (!data?.ok) {
      throw new Error('Unexpected response');
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function applyNativeNotifications() {
  const shell = nativeShell();
  try {
    if (shell?.startNotifications) shell.startNotifications();
    else if (window.cuttleMobile?.startNotifications) window.cuttleMobile.startNotifications();
  } catch {
    // Native listener watches Capacitor Preferences on both platforms.
  }
}

/**
 * Open Cuttle in the native WebView (Electron loadURL equivalent).
 * Never hand off to the system browser.
 */
async function openInNativeShell(host, port, useHttps, baseUrl, notifyEnabled, token) {
  await saveConfig(host, port, useHttps, baseUrl, notifyEnabled, token);
  const recent = await rememberHost(host, port, useHttps);
  renderRecentHosts(recent);
  applyNativeNotifications();
  const shell = nativeShell();
  if (shell?.saveAndLoad) {
    shell.saveAndLoad(host, String(port), useHttps ? '1' : '0', baseUrl);
    return;
  }
  if (shell?.loadServer) {
    shell.loadServer(baseUrl);
    return;
  }
  // Browser/dev fallback only
  window.location.href = cuttleEntryUrl(baseUrl);
}

function populateForm(config) {
  if (config.host) {
    const hostEl = $('#host');
    if (hostEl) hostEl.value = config.host;
  }
  const preset = $('#port-preset');
  if (preset) {
    if (config.port === DEFAULT_PORT_HTTP) {
      preset.value = '8000';
    } else if (config.port === DEFAULT_PORT_HTTPS) {
      preset.value = '8888';
    } else if (isValidPort(config.port)) {
      preset.value = 'custom';
      const custom = $('#port-custom');
      if (custom) custom.value = String(config.port);
    } else {
      // Corrupt saved port: show the custom slot with whatever is salvageable
      // (out-of-range numbers verbatim, non-numeric blank) so the user sees
      // what needs correcting. Actions stay blocked until then.
      preset.value = 'custom';
      const custom = $('#port-custom');
      if (custom) custom.value = Number.isInteger(config.port) ? String(config.port) : '';
    }
  }
  const httpsBox = $('#use-https');
  if (httpsBox) httpsBox.checked = config.useHttps;
  const notifyBox = $('#notify-enabled');
  if (notifyBox) notifyBox.checked = config.notifyEnabled !== false;
  const tokenEl = $('#mobile-token');
  if (tokenEl && config.token) tokenEl.value = config.token;
  syncSchemeUi();
}

function bindForm() {
  const form = $('#setup-form');
  const testBtn = $('#btn-test');
  const preset = $('#port-preset');
  const httpsBox = $('#use-https');

  preset?.addEventListener('change', syncSchemeUi);
  httpsBox?.addEventListener('change', syncSchemeUi);

  const updateBtn = $('#btn-update');
  updateBtn?.addEventListener('click', () => {
    clearStatus();
    const shell = nativeShell();
    if (shell?.checkUpdate) {
      showStatus('Checking the PC for a newer app…', 'ok');
      const started = Date.now();
      const timer = setTimeout(() => {
        const text = $('#status-text');
        if (text && /Checking the PC/.test(text.textContent || '') && Date.now() - started >= 20000) {
          showStatus('Still waiting on the PC. Same Wi‑Fi? Try Test connection, then check again.', 'warn');
        }
      }, 22000);
      const onResult = (e) => {
        const d = (e && e.detail) || {};
        if (d.status === 'checking' || d.status === 'downloading') {
          showStatus(d.message || 'Checking the PC for a newer app…', 'ok');
          return;
        }
        document.removeEventListener('cuttle-apk-update', onResult);
        clearTimeout(timer);
        if (d.status === 'error') {
          showStatus(d.message || 'Update check failed.', 'err');
          return;
        }
        if (d.status === 'available' || d.available) {
          showStatus(d.message || 'Update found — Android will ask you to install.', 'ok');
          return;
        }
        showStatus(d.message || 'Phone app is up to date.', 'ok');
      };
      document.addEventListener('cuttle-apk-update', onResult);
      shell.checkUpdate();
      return;
    }
    const host = cleanHost($('#host')?.value || '');
    const { port, useHttps } = readForm();
    if (!host) {
      showStatus('Save your PC address first, then try again.', 'warn');
      return;
    }
    if (!isValidPort(port)) {
      showStatus('Enter a valid port 1–65535. The saved address was not changed.', 'warn');
      return;
    }
    const baseUrl = buildBaseUrl(host, port, useHttps);
    showStatus(`Open this on the phone to install: ${baseUrl}/api/mobile/android/app-debug.apk`, 'ok');
  });

  testBtn?.addEventListener('click', async () => {
    clearStatus();
    const { host, port, useHttps } = readForm();
    if (!host) {
      showStatus('Enter your PC address (LAN IP or Tailscale).', 'warn');
      return;
    }
    if (!isValidPort(port)) {
      showStatus('Enter a valid port 1–65535.', 'warn');
      return;
    }
    const baseUrl = buildBaseUrl(host, port, useHttps);
    const btn = $('#btn-test');
    if (btn) btn.disabled = true;
    try {
      const data = await testConnection(baseUrl);
      const recent = await rememberHost(host, port, useHttps);
      renderRecentHosts(recent);
      showStatus(`Reachable — ${data.message || 'Cuttle OK'}`, 'ok');
    } catch (err) {
      const msg =
        err?.name === 'AbortError'
          ? 'Timed out. Same Wi‑Fi / Tailscale online? LAN access on in Cuttle Settings?'
          : `Could not reach ${baseUrl}. Check address/port/firewall.`;
      showStatus(msg, 'err');
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearStatus();
    const { host, port, useHttps, notifyEnabled, token } = readForm();
    if (!host) {
      showStatus('Enter your PC address (LAN IP or Tailscale).', 'warn');
      return;
    }
    if (!isValidPort(port)) {
      showStatus('Enter a valid port 1–65535. Nothing was saved or opened.', 'warn');
      return;
    }
    const baseUrl = buildBaseUrl(host, port, useHttps);
    const btn = $('#btn-connect');
    if (btn) btn.disabled = true;
    try {
      await testConnection(baseUrl);
      showStatus('Opening Cuttle…', 'ok');
      await openInNativeShell(host, port, useHttps, baseUrl, notifyEnabled, token);
    } catch (err) {
      const msg =
        err?.name === 'AbortError'
          ? 'Timed out. Try Test connection first.'
          : `Could not reach Cuttle at ${baseUrl}.`;
      showStatus(msg, 'err');
      if (btn) btn.disabled = false;
    }
  });
}

async function initNativeChrome() {
  if (!Capacitor.isNativePlatform()) return;
  try {
    await SafeArea.setSystemBarsStyle({ style: SystemBarsStyle.Dark });
  } catch {
    // optional
  }

  // Back on the settings screen exits (MainActivity handles Cuttle UI back).
  App.addListener('backButton', ({ canGoBack }) => {
    if (canGoBack) {
      window.history.back();
    } else {
      App.exitApp();
    }
  });
}

const SERVER_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>' +
  '<path d="M6 6h.01"/><path d="M6 18h.01"/></svg>';

function initHero() {
  const logo = $('#hero-logo');
  if (logo) logo.src = mascotUrl;
}

async function initFooter() {
  const footer = $('#app-footer');
  if (!footer || !Capacitor.isNativePlatform()) return;
  try {
    const info = await App.getInfo();
    footer.textContent = `Cuttle Mobile ${info.version} (${info.build}) · ${Capacitor.getPlatform()}`;
  } catch {
    // Footer is cosmetic.
  }
}

/**
 * Settings opened from the in-app Server button: show the saved server
 * with a live reachability check and a one-tap way back, so changing
 * nothing never means retyping the address.
 */
function initCurrentServer(config) {
  const card = $('#current-server');
  if (!card || !config.host || !isValidPort(config.port)) return;
  const baseUrl = config.baseUrl || buildBaseUrl(config.host, config.port, config.useHttps);
  card.hidden = false;
  $('#current-server-url').textContent = baseUrl.replace(/^https?:\/\//, '');
  const heading = $('#setup-heading');
  if (heading) heading.textContent = 'Change server';

  const pill = $('#current-server-state');
  const pillText = $('#current-server-state-text');
  const setState = (state, text) => {
    pill.className = `state-pill ${state}`;
    pillText.textContent = text;
  };
  testConnection(baseUrl)
    .then(() => setState('online', 'Online'))
    .catch(() => setState('offline', 'Unreachable'));

  $('#btn-back')?.addEventListener('click', () => {
    openInNativeShell(
      config.host, config.port, config.useHttps, baseUrl, config.notifyEnabled, config.token,
    );
  });
}

async function init() {
  await initNativeChrome();
  initHero();
  initFooter();

  const config = await loadConfig();
  populateForm(config);
  renderRecentHosts(await loadRecentHosts());
  // Seed history with the current saved host if present and usable.
  if (config.host && isValidPort(config.port)) {
    const seeded = await rememberHost(config.host, config.port, config.useHttps);
    renderRecentHosts(seeded);
  }
  bindForm();

  const params = new URLSearchParams(window.location.search);
  const isSettingsMode = params.get('setup') === '1';
  const isOfflineMode = params.get('offline') === '1';

  if (isSettingsMode) initCurrentServer(config);

  if (isOfflineMode || !isSettingsMode) {
    const fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'server-fab';
    fab.innerHTML = `${SERVER_ICON}<span>Server</span>`;
    fab.title = 'Cuttle server settings';
    fab.addEventListener('click', () => {
      window.location.href = 'https://localhost/index.html?setup=1';
    });
    document.body.appendChild(fab);
  }

  if (isOfflineMode) {
    document.body.classList.add('offline-mode');
    const offline = $('#offline-login');
    if (offline) offline.hidden = false;
    const form = $('#setup-form');
    if (form) form.hidden = true;
    const help = document.querySelector('.help');
    if (help) help.hidden = true;
    $('#btn-offline-retry')?.addEventListener('click', () => {
      const shell = nativeShell();
      if (shell?.reload) shell.reload();
      else window.location.href = 'https://localhost/index.html';
    });
    return;
  }

  // Cold start with saved server is handled natively in MainActivity
  // (Electron-style loadURL). Only stay here for first-run or explicit settings.
  if (!isSettingsMode && config.baseUrl && Capacitor.isNativePlatform()) {
    showStatus('Starting Cuttle…', 'ok');
  }
}

init();
