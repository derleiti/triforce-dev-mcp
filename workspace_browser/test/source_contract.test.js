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
  assert.match(source, /permission === 'fileSystem'/);
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
  assert.match(source, /Quit executor/);
});
