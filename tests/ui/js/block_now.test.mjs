import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeBlockNow } from '../../../src/ui/js/pages/blocking/block_now.js';

const now = Date.UTC(2026, 10, 3, 12);
function editorRail(pending, ticked = true) {
  let loaded = pending, loads = 0, refreshed = false;
  return {
    editor: () => ({ check: () => ({ rule: pending }), load: rule => { loaded = rule; loads++; } }),
    ticked: () => ticked ? ['temporary'] : [],
    tick: type => { assert.equal(type, 'temporary'); ticked = true; },
    refresh: () => { refreshed = true; },
    result: () => ({ loaded, loads, refreshed, ticked }),
  };
}
for (const [label, pending] of [
  ['duration draft', {rule_type:'temporary', duration_min:300}],
  ['dated draft', {rule_type:'temporary', temp_until:'2026-11-03 17:00:00'}],
]) {
  test(`Block now preserves a longer ${label}`, () => {
    const rail = editorRail(pending);
    mergeBlockNow(rail, '2026-11-03T13:00:00', now);
    assert.deepEqual(rail.result(), {loaded:pending, loads:0, refreshed:true, ticked:true});
  });
}
for (const [label, pending, ticked] of [
  ['shorter duration', {rule_type:'temporary', duration_min:30}, true],
  ['shorter end', {rule_type:'temporary', temp_until:'2026-11-03 12:30:00'}, true],
  ['invalid draft', null, true],
  ['unticked draft', {rule_type:'temporary', duration_min:300}, false],
]) {
  test(`Block now merges a saved end over a ${label}`, () => {
    const rail = editorRail(pending, ticked);
    mergeBlockNow(rail, '2026-11-03T13:00:00', now);
    assert.deepEqual(rail.result(), {loaded:{rule_type:'temporary',temp_until:'2026-11-03 13:00:00'},loads:1,refreshed:true,ticked:true});
  });
}
