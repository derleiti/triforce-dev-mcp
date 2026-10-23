import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

test('remote control refuses unapproved operation', () => {
  const p = spawnSync('/usr/bin/python3', ['remote_client.py'], {
    cwd: new URL('..', import.meta.url).pathname,
    input: JSON.stringify({op:'exec',command:'whoami'}),
    encoding:'utf8',
    timeout:5000
  });
  assert.notEqual(p.status, 0);
  assert.equal(JSON.parse(p.stdout).ok,false);
});

test('remote control refuses invalid Discord user IDs before socket access', () => {
  const p = spawnSync('/usr/bin/python3', ['remote_client.py'], {
    cwd: new URL('..', import.meta.url).pathname,
    input: JSON.stringify({op:'join_user',user_id:'unknown'}),
    encoding:'utf8',
    timeout:5000
  });
  assert.notEqual(p.status, 0);
  assert.match(JSON.parse(p.stdout).error,/invalid user_id/);
});

test('README explains signed webhook delivery', () => {
  const body=readFileSync(new URL('../README.md',import.meta.url),'utf8');
  assert.match(body,/interactions endpoint/i);
});
