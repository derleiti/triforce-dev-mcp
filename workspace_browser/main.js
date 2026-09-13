'use strict';

const { app, BrowserWindow, Menu, Tray, nativeImage, powerSaveBlocker, session, shell } = require('electron');

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
  ses.setPermissionCheckHandler((_webContents, permission, requestingOrigin) => {
    return permission === 'fileSystem' && isTrustedDocument(requestingOrigin);
  });

  ses.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    const requestingUrl = details?.requestingUrl || details?.requestingOrigin || '';
    callback(permission === 'fileSystem' && isTrustedDocument(requestingUrl));
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

module.exports = { safeTarget, targetFromArgv, isTrustedDocument };
