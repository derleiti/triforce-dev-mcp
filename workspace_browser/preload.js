'use strict';

/**
 * The only bridge between the AILinux Loom web app and the native host.
 *
 * The renderer never receives Node APIs. It can ask for a terminal, but the
 * grant itself happens in the main process behind an explicit user prompt.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ailinuxNative', {
  host: 'electron',
  platform: process.platform,
  version: process.env.AILINUX_HELPER_VERSION || '',
  shellStatus: () => ipcRenderer.invoke('ailinux:shell-status'),
  setShellBackend: (name) => ipcRenderer.invoke('ailinux:shell-backend', name),
  releaseShell: () => ipcRenderer.invoke('ailinux:shell-release'),
  revokeShell: () => ipcRenderer.invoke('ailinux:shell-revoke'),
  runShell: (payload) => ipcRenderer.invoke('ailinux:shell-run', payload),
  notifyConnection: (details) => ipcRenderer.invoke('ailinux:notify-connection', details),
  onShellChanged: (callback) => {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('ailinux:shell-changed', (_event, state) => callback(state));
  },
});
