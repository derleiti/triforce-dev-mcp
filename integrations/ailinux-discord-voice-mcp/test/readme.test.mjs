import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
test('no secrets checked into package', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url)));
  assert.equal(manifest.private, true);
  assert.equal(manifest.dependencies['@discordjs/voice'], '0.19.2');
});
