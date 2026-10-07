/* Run under real Electron. Private profiles, loopback fixture backend, no CLIs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');
const cp = require('node:child_process');
const { app, BrowserWindow, session, Tray, Menu, nativeImage } = require('electron');
const mode = process.argv.includes('--smoke-client') ? 'client' : 'host';
const root = path.resolve(__dirname, '../..');
fs.mkdirSync(process.env.CUTTLE_SMOKE_TEMP || path.join(root, 'temp'), {recursive:true});
const profile = fs.mkdtempSync(path.join(process.env.CUTTLE_SMOKE_TEMP || path.join(root, 'temp'), 'cuttle-electron-smoke-'));
const setPath = app.setPath.bind(app);
app.setPath = (key, value) => setPath(key, key === 'userData' ? profile : value);
setPath('userData', profile);
// The production main must never start a daemon, worker, shell or installer.
let deniedSpawns = 0;
cp.spawn = cp.exec = cp.execFile = () => { deniedSpawns++; throw new Error('Smoke test forbids subprocess execution'); };
let ports;
cp.spawnSync = (executable, args) => {
  assert.deepEqual(args, ['-m', 'api.server_ports']);
  return { status: 0, stdout: JSON.stringify(ports), stderr: '' };
};
process.env.CUTTLE_HOSTED_BY_DAEMON = '1';
const errors = [];
app.on('web-contents-created', (_, contents) => {
  contents.on('render-process-gone', (_, detail) => errors.push(detail.reason));
});
const web = path.join(root, 'src/web');
function serve(req, res) {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/')) {
    let body = { success: true };
    if (url.pathname === '/api/health') body = {success:true, service:'cuttle', status:'healthy', components:{}};
    if (url.pathname === '/api/auth/me') body = { success: false };
    if (url.pathname === '/api/agents') body = { success: true, agents: [] };
    if (url.pathname === '/api/projects') body = { success: true, projects: [] };
    if (url.pathname === '/api/workers') body = { success: true, workers: [], online_total: 0 };
    if (url.pathname === '/api/desktop/electron') body = { ok: false };
    if (url.pathname === '/api/experimental') body = { success: true, flags: [] };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
    return;
  }
  const target = path.resolve(web, '.' + decodeURIComponent(url.pathname));
  if (!target.startsWith(web + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
    res.writeHead(404); res.end(); return;
  }
  const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' };
  res.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream' });
  fs.createReadStream(target).pipe(res);
}
const servers = [http.createServer(serve), http.createServer(serve)];
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, description) {
  for (let n = 0; n < 100; n++) { if (await predicate()) return; await delay(100); }
  throw new Error('Timed out: ' + description);
}
const watchdog = setTimeout(() => { console.error('SMOKE_TIMEOUT'); app.exit(1); }, 45000);
(async () => {
  await Promise.all(servers.map(listen));
  ports = { http: servers[0].address().port, https: servers[1].address().port, phone_https: 1 };
  fs.writeFileSync(path.join(profile, 'desktop-config.json'), JSON.stringify({ workerMode: false }));
  process.argv.push('--mode=' + mode);
  if (mode === 'client') process.argv.push('--host=http://127.0.0.1:' + ports.http);
  app.whenReady().then(() => {
    session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
      const url = new URL(details.url);
      callback({ cancel: !['127.0.0.1', 'localhost'].includes(url.hostname) && url.protocol !== 'file:' });
    });
  });
  require('../main.js');
  await app.whenReady();
  await until(() => BrowserWindow.getAllWindows().some(w => /app_shell\.html/.test(w.webContents.getURL())), 'shell window');
  const main = BrowserWindow.getAllWindows().find(w => /app_shell\.html/.test(w.webContents.getURL()));
  await until(() => main.webContents.executeJavaScript('!!window.electron?.desktop'), 'real preload bridge');
  const config = await main.webContents.executeJavaScript('window.electron.desktop.getConfig()');
  assert.equal(config.clientMode, mode === 'client');
  assert.equal(config.httpPort, ports.http);
  assert.equal(await main.webContents.executeJavaScript('typeof require'), 'undefined');
  assert.equal(main.webContents.getLastWebPreferences().contextIsolation, true);
  assert.equal(main.webContents.getLastWebPreferences().nodeIntegration, false);
  await main.webContents.executeJavaScript('window.electron.setZoomPercent(120)');
  assert.equal(await main.webContents.executeJavaScript('window.electron.getZoomPercent()'), 120);
  await main.webContents.executeJavaScript('window.electron.resetZoom()');
  const status = await main.webContents.executeJavaScript('window.electron.desktop.updateStatus()');
  assert.equal(status.available, false);
  await main.webContents.executeJavaScript('window.electron.gizmos.syncPopouts([{id:"smoke-gizmo",title:"Smoke"}])');
  await until(() => BrowserWindow.getAllWindows().length === 2, 'gizmo popout');
  const popout = BrowserWindow.getAllWindows().find(w => w !== main);
  await until(() => /gizmo_popout\.html/.test(popout.webContents.getURL()), 'popout navigation');
  assert.match(popout.webContents.getURL(), /gizmo_popout\.html/);
  await until(() => popout.isVisible() && popout.isAlwaysOnTop(), 'visible always-on-top popout');
  await main.webContents.executeJavaScript('window.electron.gizmos.syncPopouts([])');
  await until(() => BrowserWindow.getAllWindows().length === 1, 'popout close');
  // Exercise Electron's native tray/menu API without a shared lifecycle tray.
  const icon = nativeImage.createFromPath(path.join(root, 'src/img/cuttle-logo.png'));
  const tray = new Tray(icon.isEmpty() ? nativeImage.createFromBuffer(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0nkAAAAASUVORK5CYII=', 'base64')) : icon);
  tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Smoke', click() {} }]));
  tray.destroy();
  assert.equal(deniedSpawns, 0);
  assert.deepEqual(errors, []);
  console.log('SMOKE_OK ' + JSON.stringify({mode, electron:process.versions.electron, preload:true, popout:true, tray:true, profilePrivate:true}));
  clearTimeout(watchdog);
  servers.forEach(s => s.close());
  app.exit(0);
})().catch(err => { console.error(err.stack || err); clearTimeout(watchdog); app.exit(1); });
