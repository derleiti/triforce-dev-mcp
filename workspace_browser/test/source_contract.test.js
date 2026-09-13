'use strict';

const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');

test('renderer is isolated from Node and keeps background timers active', () => {
  assert.match(source, /nodeIntegration:\s*false/);
  assert.match(source, /contextIsolation:\s*true/);
  assert.match(source, /sandbox:\s*true/);
  assert.match(source, /backgroundThrottling:\s*false/);
});

test('workspace browser uses a persistent profile and app suspension blocker', () => {
  assert.match(source, /persist:ailinux-workspace/);
  assert.match(source, /prevent-app-suspension/);
});

test('filesystem permission is restricted to the trusted origin', () => {
  assert.match(source, /'fileSystem'/);
  assert.match(source, /https:\/\/api\.ailinux\.me\/v1\/mcp/);
  assert.match(source, /isTrustedDocument/);
});

test('external navigation and popups are denied', () => {
  assert.match(source, /setWindowOpenHandler\(\(\) => \(\{ action: 'deny' \}\)\)/);
  assert.match(source, /will-navigate/);
  assert.match(source, /will-redirect/);
});

test('closing the window hides it instead of stopping the executor', () => {
  assert.match(source, /event\.preventDefault\(\)/);
  assert.match(source, /window\.hide\(\)/);
  assert.match(source, /Quit AILinux Workspace/);
});


test('desktop helper identifies platform and keeps a clean tray lifecycle', () => {
  assert.match(source, /PLATFORM_LABEL/);
  assert.match(source, /Open AILinux Workspace/);
  assert.match(source, /Reconnect workspace/);
  assert.match(source, /Quit AILinux Workspace/);
});


test('trusted MCP origin can write clipboard and request wake lock', () => {
  assert.match(source, /clipboard-sanitized-write/);
  assert.match(source, /screen-wake-lock/);
  assert.match(source, /trustedPermissions\.has\(permission\) && isTrustedDocument/);
});

test('the native bridge is preloaded and gated behind a user prompt', () => {
  assert.match(source, /preload: path\.join\(__dirname, 'preload\.js'\)/);
  assert.match(source, /registerNativeBridge/);
  assert.match(source, /ailinux:shell-release/);
  assert.match(source, /dialog\.showMessageBox/);
  assert.match(source, /dialog\.showOpenDialog/);
  assert.match(source, /fromTrustedFrame/);
});

test('connection details surface as a notification and in the tray', () => {
  assert.match(source, /showConnectionNotification/);
  assert.match(source, /new Notification\(/);
  assert.match(source, /tray\.setToolTip/);
});

test('backend selection is exposed through preload and trusted IPC', () => {
  const preload = fs.readFileSync(path.join(__dirname, '..', 'preload.js'), 'utf8');
  assert.match(preload, /setShellBackend:\s*\(name\)\s*=>\s*ipcRenderer\.invoke\('ailinux:shell-backend', name\)/);
  assert.match(source, /ipcMain\.handle\('ailinux:shell-backend'/);
  assert.match(source, /if \(!fromTrustedFrame\(event\)\) return \{ error: 'untrusted frame' \}/);
  assert.match(source, /shellBackends\.setBackend\(name\)/);
  assert.match(source, /broadcastShellState\(state\)/);
});
