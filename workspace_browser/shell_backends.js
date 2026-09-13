'use strict';

/**
 * Platform-aware shell backends for the AILinux Loom desktop helper.
 *
 * Node port of local_workspace_client/shell_backends.py. Same contract, same
 * gate names: a backend must be available on this machine AND released by the
 * user before the AI is given a terminal. Unsandboxed backends are never
 * released implicitly.
 */

const { execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const SANDBOXED_BACKENDS = new Set(['bubblewrap', 'docker']);
const ENV_BACKEND = 'AILINUX_SHELL_BACKEND';
const ENV_DOCKER_IMAGE = 'AILINUX_SHELL_DOCKER_IMAGE';
const ENV_DOCKER_NETWORK = 'AILINUX_SHELL_DOCKER_NETWORK';

const BACKEND_LABELS = {
  bubblewrap: 'Linux console (bubblewrap sandbox)',
  linux: 'Linux console (native)',
  powershell: 'Windows PowerShell',
  cmd: 'Windows cmd.exe',
  macos: 'macOS console',
  docker: 'Docker environment',
};

function envValue(name) {
  return String(process.env[name] || '').trim();
}

function which(...names) {
  const exts = process.platform === 'win32' ? ['.exe', '.cmd', ''] : [''];
  for (const name of names) {
    if (!name) continue;
    if (name.includes(path.sep)) {
      try { fs.accessSync(name, fs.constants.X_OK); return name; } catch { continue; }
    }
    for (const dir of String(process.env.PATH || '').split(path.delimiter)) {
      if (!dir) continue;
      for (const ext of exts) {
        const candidate = path.join(dir, name + ext);
        try { fs.accessSync(candidate, fs.constants.X_OK); return candidate; } catch { /* next */ }
      }
    }
  }
  return '';
}

function backendAvailable(backend) {
  switch (backend) {
    case 'docker':
      if (!envValue(ENV_DOCKER_IMAGE)) return [false, `docker backend requires ${ENV_DOCKER_IMAGE}`];
      if (!which('docker')) return [false, 'docker executable not found'];
      return [true, `docker image ${envValue(ENV_DOCKER_IMAGE)}`];
    case 'bubblewrap':
      return which('bwrap') ? [true, 'bubblewrap sandbox'] : [false, 'bubblewrap (bwrap) not installed'];
    case 'powershell': {
      const found = which('pwsh', 'powershell');
      return found ? [true, `powershell at ${found}`] : [false, 'powershell not found'];
    }
    case 'cmd': {
      const found = envValue('COMSPEC') || which('cmd');
      return found ? [true, `cmd at ${found}`] : [false, 'cmd.exe not found'];
    }
    case 'macos': {
      const found = which('zsh', 'bash', 'sh');
      return found ? [true, `macOS shell at ${found}`] : [false, 'no zsh/bash/sh found'];
    }
    case 'linux': {
      const found = which('bash', 'sh');
      return found ? [true, `native shell at ${found}`] : [false, 'no bash/sh found'];
    }
    default:
      return [false, `unknown backend: ${backend}`];
  }
}

function detectBackend() {
  const pinned = envValue(ENV_BACKEND).toLowerCase();
  if (pinned) {
    if (!BACKEND_LABELS[pinned]) return ['', `unknown backend pinned via ${ENV_BACKEND}: ${pinned}`];
    const [ok, reason] = backendAvailable(pinned);
    return ok ? [pinned, reason] : ['', reason];
  }
  const order = [];
  if (envValue(ENV_DOCKER_IMAGE)) order.push('docker');
  if (process.platform === 'win32') order.push('powershell', 'cmd');
  else if (process.platform === 'darwin') order.push('macos');
  else order.push('bubblewrap', 'linux');

  let last = 'no shell backend available on this platform';
  for (const candidate of order) {
    const [ok, reason] = backendAvailable(candidate);
    if (ok) return [candidate, reason];
    last = reason;
  }
  return ['', last];
}

/** Release state lives in the main process; the renderer can never set it directly. */
const release = { granted: false, root: '', at: 0 };

function grantRelease(root) {
  release.granted = true;
  release.root = root ? path.resolve(root) : '';
  release.at = Date.now();
  return status();
}

function revokeRelease() {
  release.granted = false;
  release.root = '';
  release.at = 0;
  return status();
}

function status() {
  const [backend, detail] = detectBackend();
  const sandboxed = SANDBOXED_BACKENDS.has(backend);
  return {
    host: 'electron',
    platform: process.platform,
    backend,
    label: BACKEND_LABELS[backend] || '',
    available: Boolean(backend),
    sandboxed,
    released: Boolean(backend) && release.granted && Boolean(release.root),
    workspace: release.root,
    releasedAt: release.at,
    detail,
    releaseDetail: release.granted
      ? (release.root ? 'terminal released by the user' : 'released without a workspace folder')
      : 'terminal not released by the user',
  };
}

function posixArgs(shellPath, command) {
  const name = path.basename(shellPath);
  return (name === 'bash' || name === 'zsh')
    ? ['-o', 'pipefail', '-c', command]
    : ['-c', command];
}

function resolveWithin(root, candidate) {
  const base = path.resolve(root);
  const target = path.resolve(base, candidate || '.');
  const rel = path.relative(base, target);
  if (rel.startsWith('..') || path.isAbsolute(rel)) {
    throw new Error('path is outside the released workspace');
  }
  return { target, rel: rel.split(path.sep).filter(Boolean).join('/') };
}

function buildPlan(command, cwd) {
  const text = String(command || '').trim();
  if (!text) throw new Error('command is required');
  const state = status();
  if (!state.available) throw new Error(`no shell backend available: ${state.detail}`);
  if (!state.released) throw new Error(`shell not released: ${state.releaseDetail}`);

  const { target, rel } = resolveWithin(state.workspace, cwd);
  const workdir = '/workspace' + (rel ? `/${rel}` : '');

  if (state.backend === 'bubblewrap') {
    const argv = ['--die-with-parent', '--new-session', '--unshare-all',
      '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
      '--ro-bind', '/usr', '/usr', '--ro-bind', '/bin', '/bin', '--ro-bind', '/lib', '/lib'];
    if (fs.existsSync('/lib64')) argv.push('--ro-bind', '/lib64', '/lib64');
    argv.push('--bind', state.workspace, '/workspace', '--chdir', workdir,
      '--setenv', 'HOME', '/workspace', '--setenv', 'TMPDIR', '/tmp',
      '/bin/bash', '-o', 'pipefail', '-c', text);
    return { backend: 'bubblewrap', sandboxed: true, file: which('bwrap'), args: argv, cwd: undefined };
  }

  if (state.backend === 'docker') {
    const args = ['run', '--rm', '-i', '--network', envValue(ENV_DOCKER_NETWORK) || 'none',
      '-v', `${state.workspace}:/workspace`, '-w', workdir,
      envValue(ENV_DOCKER_IMAGE), '/bin/sh', '-c', text];
    return { backend: 'docker', sandboxed: true, file: 'docker', args, cwd: undefined };
  }

  if (state.backend === 'powershell') {
    const file = which('pwsh', 'powershell');
    return {
      backend: 'powershell', sandboxed: false, file,
      args: ['-NoLogo', '-NonInteractive', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', text],
      cwd: target,
    };
  }

  if (state.backend === 'cmd') {
    const file = envValue('COMSPEC') || which('cmd');
    return { backend: 'cmd', sandboxed: false, file, args: ['/d', '/c', text], cwd: target };
  }

  const file = state.backend === 'macos' ? which('zsh', 'bash', 'sh') : which('bash', 'sh');
  return { backend: state.backend, sandboxed: false, file, args: posixArgs(file, text), cwd: target };
}

function runShell({ command, cwd, timeout } = {}) {
  return new Promise((resolve) => {
    let plan;
    try {
      plan = buildPlan(command, cwd);
    } catch (error) {
      resolve({ ok: false, text: String(error.message || error), isError: true });
      return;
    }
    const limit = Math.max(1, Math.min(Number(timeout) || 120, 300)) * 1000;
    execFile(plan.file, plan.args, {
      cwd: plan.cwd, timeout: limit, maxBuffer: 4 * 1024 * 1024, windowsHide: true,
    }, (error, stdout, stderr) => {
      const parts = [];
      if (stdout) parts.push(`stdout:\n${stdout}`);
      if (stderr) parts.push(`stderr:\n${stderr}`);
      const code = error && typeof error.code === 'number' ? error.code : (error ? 1 : 0);
      if (error && error.killed) parts.push(`command timed out after ${limit / 1000}s`);
      parts.push(`exit_code=${code} backend=${plan.backend} sandboxed=${plan.sandboxed}`);
      const text = parts.join('\n').slice(0, 12000);
      resolve({ ok: code === 0, text, isError: code !== 0 });
    });
  });
}

module.exports = {
  SANDBOXED_BACKENDS, BACKEND_LABELS, ENV_BACKEND, ENV_DOCKER_IMAGE, ENV_DOCKER_NETWORK,
  detectBackend, backendAvailable, status, grantRelease, revokeRelease,
  buildPlan, runShell, resolveWithin, posixArgs, which,
};
