'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const backends = require('../shell_backends');

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'loom-shell-'));
}

test('a terminal is closed until the user releases it', () => {
  backends.revokeRelease();
  const state = backends.status();
  assert.equal(state.released, false);
  assert.match(state.releaseDetail, /not released/);
});

test('detection finds a usable backend on this platform', () => {
  const [backend, detail] = backends.detectBackend();
  assert.ok(backend, `no backend detected: ${detail}`);
  assert.ok(backends.BACKEND_LABELS[backend]);
});

test('an unknown pinned backend is rejected', () => {
  process.env[backends.ENV_BACKEND] = 'nonsense';
  const [backend, reason] = backends.detectBackend();
  delete process.env[backends.ENV_BACKEND];
  assert.equal(backend, '');
  assert.match(reason, /unknown backend/);
});

test('building a plan without a release fails', () => {
  backends.revokeRelease();
  assert.throws(() => backends.buildPlan('echo hi', '.'), /not released/);
});

test('an empty command is rejected', () => {
  backends.grantRelease(tempRoot());
  assert.throws(() => backends.buildPlan('   ', '.'), /command is required/);
  backends.revokeRelease();
});

test('paths outside the released workspace are refused', () => {
  const root = tempRoot();
  assert.throws(() => backends.resolveWithin(root, '../../etc'), /outside the released workspace/);
  const inside = backends.resolveWithin(root, 'pkg/sub');
  assert.equal(inside.rel, 'pkg/sub');
});

test('pipefail is only used for bash and zsh', () => {
  assert.deepEqual(backends.posixArgs('/bin/bash', 'true'), ['-o', 'pipefail', '-c', 'true']);
  assert.deepEqual(backends.posixArgs('/bin/sh', 'true'), ['-c', 'true']);
});

test('a released terminal executes inside the chosen folder', async () => {
  const root = tempRoot();
  const state = backends.grantRelease(root);
  assert.equal(state.released, true);
  assert.equal(state.workspace, fs.realpathSync(root) === root ? root : state.workspace);

  const result = await backends.runShell({ command: 'echo loom-desktop-ok', timeout: 30 });
  backends.revokeRelease();
  assert.equal(result.isError, false, result.text);
  assert.match(result.text, /loom-desktop-ok/);
  assert.match(result.text, /exit_code=0 backend=/);
});

test('revoking closes the terminal again', () => {
  backends.grantRelease(tempRoot());
  const state = backends.revokeRelease();
  assert.equal(state.released, false);
  assert.equal(state.workspace, '');
});

test('available backends can be selected without releasing the terminal', () => {
  backends.revokeRelease();
  const available = backends.availableBackends();
  assert.ok(available.length > 0);
  for (const item of available) {
    assert.ok(item.name);
    assert.ok(item.label);
    assert.equal(typeof item.sandboxed, 'boolean');
    assert.ok(item.detail);
  }
  const selected = backends.setBackend(available[0].name);
  assert.equal(selected.backend, available[0].name);
  assert.equal(selected.released, false);
  assert.equal(selected.workspace, '');
});

test('runtime backend selection takes precedence over the environment pin', () => {
  const available = backends.availableBackends();
  assert.ok(available.length > 0);
  const target = available[0].name;
  process.env[backends.ENV_BACKEND] = 'nonsense';
  try {
    const selected = backends.setBackend(target);
    assert.equal(selected.backend, target);
    const [detected] = backends.detectBackend();
    assert.equal(detected, target);
  } finally {
    delete process.env[backends.ENV_BACKEND];
  }
});
