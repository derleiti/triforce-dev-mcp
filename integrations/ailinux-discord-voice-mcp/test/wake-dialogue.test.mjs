import test from 'node:test';
import assert from 'node:assert/strict';
import {WakeDialogue, stripWakeWord} from '../wake_dialogue.mjs';

test('wake-only does not create a fake question, next Discord clip carries the question', () => {
  let now = 2000;
  const dialog = new WakeDialogue({now: () => now});
  assert.equal(stripWakeWord('Nova'), '');
  assert.deepEqual(dialog.interpret('Nova'), {kind: 'armed', seconds: 18});
  now += 1300;
  assert.deepEqual(dialog.interpret('Warum ist der Himmel blau?'), {
    kind: 'question', text: 'Warum ist der Himmel blau?', source: 'armed'
  });
  assert.equal(dialog.status().armed, false);
});

test('real questions containing Nova are forwarded without a dummy greeting', () => {
  const dialog = new WakeDialogue();
  assert.deepEqual(dialog.interpret('Hey Nova, was ist 8 mal 9?'), {
    kind: 'question', text: 'was ist 8 mal 9?', source: 'wake'
  });
});

test('follow-up questions are accepted briefly after a reply, then expire', () => {
  let now=5000;
  const dialog=new WakeDialogue({now:()=>now,followupMs:10000});
  assert.equal(dialog.interpret('Was haben wir gerade gefragt?').kind,'ignore');
  dialog.interpret('Nova, rechne 12 plus 4');
  dialog.markReplied();
  now+=1000;
  assert.deepEqual(dialog.interpret('Und davon die Hälfte?'),{
    kind:'question',text:'Und davon die Hälfte?',source:'followup'
  });
  dialog.markReplied();
  now+=10001;
  assert.equal(dialog.interpret('Das soll nicht beantwortet werden').kind,'ignore');
});

test('arm timeout and manual pause prevent uninvited replies', () => {
  let now=0;
  const dialog=new WakeDialogue({now:()=>now,armMs:1500});
  dialog.interpret('Nova');
  now+=1501;
  assert.equal(dialog.interpret('Nach Ablauf der Aktivierung').kind,'ignore');
  dialog.interpret('Nova');
  dialog.reset();
  assert.equal(dialog.interpret('Nach dem Ausschalten').kind,'ignore');
});
