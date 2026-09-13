'use strict';

const { app, BrowserWindow, Menu, Notification, Tray, dialog, ipcMain, nativeImage, powerSaveBlocker, session, shell } = require('electron');
const path = require('node:path');
const shellBackends = require('./shell_backends');

const APP_NAME = 'AILinux Workspace';
const START_URL = 'https://api.ailinux.me/v1/mcp';
const ALLOWED_ORIGIN = new URL(START_URL).origin;
const SESSION_PARTITION = 'persist:ailinux-workspace';
const PROTOCOL = 'ailinux-workspace';
const startHidden = process.argv.includes('--background');
const PLATFORM_LABEL = process.platform === 'darwin' ? 'macOS' : process.platform === 'win32' ? 'Windows' : 'Linux';

let window = null;
let tray = null;
let quitting = false;
let powerBlockerId = null;
let pendingDeepLink = null;

function safeTarget(value) {
  try {
    const url = new URL(value || START_URL);
    if (url.origin !== ALLOWED_ORIGIN) return START_URL;
    if (!url.pathname.startsWith('/v1/mcp')) return START_URL;
    return url.toString();
  } catch {
    return START_URL;
  }
}

function targetFromArgv(argv) {
  for (const arg of argv) {
    if (typeof arg !== 'string' || !arg.startsWith(`${PROTOCOL}://`)) continue;
    try {
      const deepLink = new URL(arg);
      const requested = deepLink.searchParams.get('url');
      const pairCode = deepLink.searchParams.get('pair_code') || deepLink.searchParams.get('code');
      if (requested) return safeTarget(requested);
      if (pairCode) {
        const url = new URL(START_URL);
        url.searchParams.set('pair_code', pairCode.trim().toUpperCase());
        return url.toString();
      }
    } catch {
      return START_URL;
    }
  }
  return null;
}

function isTrustedDocument(urlValue) {
  try {
    return new URL(urlValue).origin === ALLOWED_ORIGIN;
  } catch {
    return false;
  }
}

function configureSession(ses) {
  const trustedPermissions = new Set(['fileSystem', 'clipboard-sanitized-write', 'screen-wake-lock']);
  ses.setPermissionCheckHandler((_webContents, permission, requestingOrigin, details) => {
    const requestingUrl = requestingOrigin || details?.requestingUrl || details?.requestingOrigin || '';
    return trustedPermissions.has(permission) && isTrustedDocument(requestingUrl);
  });

  ses.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    const requestingUrl = details?.requestingUrl || details?.requestingOrigin || '';
    callback(trustedPermissions.has(permission) && isTrustedDocument(requestingUrl));
  });
}

function trayImage() {
  const svg = encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
      <rect width="32" height="32" rx="7" fill="#16181d"/>
      <path d="M8 9h16v4H18v11h-4V13H8z" fill="#8bd450"/>
    </svg>`);
  return nativeImage.createFromDataURL(`data:image/svg+xml;charset=utf-8,${svg}`);
}

function showWindow() {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

async function navigate(target) {
  if (!window || window.isDestroyed()) return;
  const url = safeTarget(target);
  await window.loadURL(url);
  showWindow();
}

function createTray() {
  tray = new Tray(trayImage());
  tray.setToolTip(`${APP_NAME} · ${PLATFORM_LABEL} workspace helper`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open AILinux Workspace', click: showWindow },
    { label: `Platform: ${PLATFORM_LABEL}`, enabled: false },
    { label: 'Reconnect workspace', click: () => window?.webContents.reloadIgnoringCache() },
    { type: 'separator' },
    { label: 'Open MCP URL in default browser', click: () => shell.openExternal(START_URL) },
    { type: 'separator' },
    {
      label: 'Quit AILinux Workspace',
      click: () => {
        quitting = true;
        app.quit();
      },
    },
  ]));
  tray.on('click', showWindow);
}

function createWindow() {
  const ses = session.fromPartition(SESSION_PARTITION);
  configureSession(ses);

  window = new BrowserWindow({
    width: 980,
    height: 820,
    minWidth: 720,
    minHeight: 560,
    show: false,
    title: `${APP_NAME} · ${PLATFORM_LABEL}`,
    backgroundColor: '#0d0f12',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      partition: SESSION_PARTITION,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false,
      devTools: false,
      spellcheck: false,
    },
  });

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, target) => {
    if (!isTrustedDocument(target)) event.preventDefault();
  });
  window.webContents.on('will-redirect', (event, target) => {
    if (!isTrustedDocument(target)) event.preventDefault();
  });

  window.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    window.hide();
  });

  window.once('ready-to-show', () => { if (!startHidden) window.show(); });
  window.loadURL(safeTarget(pendingDeepLink || START_URL));
  pendingDeepLink = null;
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    const target = targetFromArgv(argv);
    if (target) navigate(target).catch(() => {});
    else showWindow();
  });

  app.on('open-url', (event, url) => {
    event.preventDefault();
    const target = targetFromArgv([url]);
    if (!target) return;
    if (window) navigate(target).catch(() => {});
    else pendingDeepLink = target;
  });

  app.whenReady().then(() => {
    app.setName(APP_NAME);
    app.setAsDefaultProtocolClient(PROTOCOL);
    pendingDeepLink = targetFromArgv(process.argv) || pendingDeepLink;
    powerBlockerId = powerSaveBlocker.start('prevent-app-suspension');
    registerNativeBridge();
    createWindow();
    createTray();
  });

  app.on('window-all-closed', (event) => {
    if (process.platform !== 'darwin' && !quitting) event?.preventDefault?.();
  });

  app.on('activate', showWindow);

  app.on('before-quit', () => {
    quitting = true;
    if (powerBlockerId !== null && powerSaveBlocker.isStarted(powerBlockerId)) {
      powerSaveBlocker.stop(powerBlockerId);
    }
  });
}

/**
 * Native bridge for the web app.
 *
 * The renderer may ask for a terminal; it can never grant one. Every release
 * goes through an explicit dialog plus a folder the user picks by hand, and
 * commands are confined to that folder.
 */
function fromTrustedFrame(event) {
  try {
    return isTrustedDocument(event.senderFrame.url);
  } catch {
    return false;
  }
}

function broadcastShellState(state) {
  try {
    window?.webContents?.send('ailinux:shell-changed', state);
  } catch { /* window already gone */ }
  refreshTrayState(state);
}

function refreshTrayState(state) {
  if (!tray) return;
  const line = state && state.released
    ? `Terminal: ${state.label} \u00b7 ${state.workspace}`
    : 'Terminal: not released';
  tray.setToolTip(`${APP_NAME} \u00b7 ${PLATFORM_LABEL}\n${line}`);
}

function showConnectionNotification(details) {
  if (!Notification.isSupported()) return false;
  const state = shellBackends.status();
  const lines = [];
  if (details && details.workspace) lines.push(`Workspace: ${details.workspace}`);
  if (details && details.mode) lines.push(`Access: ${details.mode}`);
  if (details && details.pairCode) lines.push(`Pair ID: ${details.pairCode}`);
  lines.push(state.released ? `Terminal: ${state.label}` : 'Terminal: not released');
  new Notification({ title: `${APP_NAME} connected`, body: lines.join('\n'), silent: true }).show();
  return true;
}

function registerNativeBridge() {
  ipcMain.handle('ailinux:shell-status', (event) => {
    if (!fromTrustedFrame(event)) return { error: 'untrusted frame' };
    return shellBackends.status();
  });

  ipcMain.handle('ailinux:shell-release', async (event) => {
    if (!fromTrustedFrame(event)) return { error: 'untrusted frame' };
    const state = shellBackends.status();
    if (!state.available) return state;

    const confirmed = await dialog.showMessageBox(window, {
      type: 'warning',
      buttons: ['Cancel', 'Choose folder and release'],
      defaultId: 0,
      cancelId: 0,
      title: 'Release terminal to the AI',
      message: 'Give the AI a terminal on this computer?',
      detail: `Backend: ${state.label}\n`
        + `${state.sandboxed ? 'Commands run inside a sandbox.' : 'Commands run with your user account, without a sandbox.'}\n\n`
        + 'You pick the workspace folder next. Commands are confined to it, and you can revoke access from the tray at any time.',
    });
    if (confirmed.response !== 1) return shellBackends.status();

    const picked = await dialog.showOpenDialog(window, {
      title: 'Workspace folder for the released terminal',
      properties: ['openDirectory'],
    });
    if (picked.canceled || !picked.filePaths.length) return shellBackends.status();

    const granted = shellBackends.grantRelease(picked.filePaths[0]);
    broadcastShellState(granted);
    showConnectionNotification({ workspace: granted.workspace, mode: 'terminal released' });
    return granted;
  });

  ipcMain.handle('ailinux:shell-revoke', (event) => {
    if (!fromTrustedFrame(event)) return { error: 'untrusted frame' };
    const state = shellBackends.revokeRelease();
    broadcastShellState(state);
    return state;
  });

  ipcMain.handle('ailinux:shell-run', async (event, payload) => {
    if (!fromTrustedFrame(event)) return { ok: false, isError: true, text: 'untrusted frame' };
    return shellBackends.runShell(payload || {});
  });

  ipcMain.handle('ailinux:notify-connection', (event, details) => {
    if (!fromTrustedFrame(event)) return false;
    return showConnectionNotification(details || {});
  });
}

module.exports = { safeTarget, targetFromArgv, isTrustedDocument, fromTrustedFrame };

