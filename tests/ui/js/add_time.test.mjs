import test from 'node:test';
import assert from 'node:assert/strict';
import { addTime, addTimeBody, addTimeItem, extraMinutes } from '../../../src/ui/js/pages/blocking/add_time.js';
import { closeDialog, isOpen } from '../../../src/ui/js/dialog.js';
import { ld } from '../../../src/ui/js/bridge.js';

const options = { name: 'Video', until: '2026-11-04T03:00:00', extra_today: '15m extra today',
  note: 'Extra time only applies to the time limit; this item is blocked by scheduled blocked hours until 05:00.' };

test('menu disables Add time with a hint when the owner has no daily limit', () => {
  assert.equal(addTimeItem({id: 1, add_time: true}, false).label, 'Add time');
  for (const entry of [{id: 1, add_time: false}, {id: 1}]) {
    const action = addTimeItem(entry, false);
    assert.equal(action.disabled, true);
    assert.match(action.label, /no daily limit/);
  }
});

test('dialog has all presets, custom minutes, expiry, existing grants and unaffected block reasons', () => {
  const html = addTimeBody(options);
  for (const [value, label] of [[15, '15 min'], [30, '30 min'], [60, '1 hour'], [120, '2 hours'], ['custom', 'Custom minutes']]) {
    assert.ok(html.includes(`<option value="${value}">${label}</option>`));
  }
  assert.match(html, /2026-11-04 03:00/);
  assert.match(html, /weekly and monthly limits still apply/);
  assert.match(html, /15m extra today/);
  assert.match(html, /scheduled blocked hours until 05:00/);
  assert.ok(addTimeBody({...options, note: '<img onerror="bad">'}).includes('&lt;img'));
});

test('custom minutes must be positive whole minutes within a day', () => {
  for (const n of ['15', '30', '60', '120']) assert.equal(extraMinutes(n, ''), Number(n));
  assert.equal(extraMinutes('custom', ' 47 '), 47);
  assert.equal(extraMinutes('custom', '1440'), 1440);
  for (const value of ['', '0', '-1', '1.5', '1e2', '1441', 'NaN']) assert.equal(extraMinutes('custom', value), null);
});

// Minimal DOM for the real dialog's event handlers; no browser or alternate Chrome instance is started.
class Node {
  constructor() { this.events = new Map(); this.children = []; this.parts = new Map(); this.dataset = {}; this.value = ''; this.hidden = false; }
  addEventListener(name, fn) { const rows = this.events.get(name) || []; rows.push(fn); this.events.set(name, rows); }
  removeEventListener(name, fn) { this.events.set(name, (this.events.get(name) || []).filter(f => f !== fn)); }
  fire(name) { for (const fn of this.events.get(name) || []) fn({preventDefault() {}}); }
  append(n) { n.parentNode = this; this.children.push(n); }
  remove() { this.parentNode.children = this.parentNode.children.filter(n => n !== this); }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.fire('close'); }
  set innerHTML(html) {
    this.html = html; this.parts.clear(); this.children = [];
    for (const key of ['.dlg-b', '.dlg-f', '[data-close]']) if (html.includes(key === '.dlg-b' ? 'dlg-b' : key === '.dlg-f' ? 'dlg-f' : 'data-close')) this.parts.set(key, new Node());
    for (const name of ['duration', 'custom', 'minutes', 'error']) if (html.includes(`data-${name}`)) this.parts.set(`[data-${name}]`, new Node());
    if (this.parts.has('[data-duration]')) this.parts.get('[data-duration]').value = '15';
    if (this.parts.has('[data-custom]')) this.parts.get('[data-custom]').hidden = true;
    if (this.parts.has('[data-error]')) this.parts.get('[data-error]').hidden = true;
  }
  querySelector(key) { return this.parts.get(key) || null; }
}

function setup(t, call) {
  const dlg = new Node(), toast = new Node(), body = new Node();
  const previous = { document: globalThis.document, cancelAnimationFrame: globalThis.cancelAnimationFrame, call: ld.call };
  globalThis.document = { body, getElementById: name => name === 'dlg' ? dlg : toast,
    createElement: name => name === 'template' ? {set innerHTML(html) { this.content = {firstElementChild: new Node()}; }} : new Node() };
  globalThis.cancelAnimationFrame = () => {};
  ld.call = call;
  t.after(() => {
    if (isOpen()) closeDialog();
    globalThis.document = previous.document;
    globalThis.cancelAnimationFrame = previous.cancelAnimationFrame;
    ld.call = previous.call;
  });
  return {dlg, body, fields: () => dlg.querySelector('.dlg-b'), submit: () => dlg.querySelector('.dlg-f').children[1].fire('click')};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('menu opens the group dialog, rejects invalid custom input, and submits one grant even on double click', async t => {
  const calls = [], done = [];
  let complete;
  const ui = setup(t, async (method, ...args) => {
    calls.push([method, ...args]);
    return method === 'blocking_add_time_options' ? options : new Promise(resolve => { complete = resolve; });
  });
  await addTimeItem({id: 7, add_time: true}, true, r => done.push(r)).onClick();
  const fields = ui.fields(), duration = fields.querySelector('[data-duration]'), minutes = fields.querySelector('[data-minutes]');
  duration.value = 'custom'; fields.fire('change');
  assert.equal(fields.querySelector('[data-custom]').hidden, false);
  minutes.value = '1.5'; ui.submit();
  assert.equal(fields.querySelector('[data-error]').hidden, false);
  assert.equal(calls.length, 1);
  minutes.value = '47'; ui.submit(); ui.submit();
  assert.deepEqual(calls, [['blocking_add_time_options', 7, true], ['blocking_add_time', 7, 47, true]]);
  assert.equal(done.length, 0);
  complete({text: '47 min added'}); await settle();
  assert.equal(done.length, 1);
  assert.equal(isOpen(), false);
});

test('guarded reply opens the normal challenge and cancelling leaves Add time open without success', async t => {
  const calls = [], done = [];
  const ui = setup(t, async (method, ...args) => {
    calls.push([method, ...args]);
    if (method === 'blocking_add_time_options') return options;
    if (method === 'blocking_add_time') return {needs_challenge: true, ticket: 'grant-ticket'};
    if (method === 'challenge_begin') return new Promise(() => {});
    return {};
  });
  await addTime(4, false, r => done.push(r));
  ui.submit(); await settle();
  assert.equal(isOpen('challenge:grant-ticket'), true);
  assert.equal(done.length, 0);
  closeDialog(); await settle();
  assert.ok(calls.some(c => c[0] === 'challenge_cancel' && c[1] === 'grant-ticket'));
  assert.equal(isOpen('add-time'), true);
  assert.equal(done.length, 0);
});

test('server refusal keeps the dialog open and shows the error without announcing success', async t => {
  const done = [];
  const ui = setup(t, async method => {
    if (method === 'blocking_add_time_options') return options;
    throw new Error('The daily limit has reset. Start Add time again.');
  });
  await addTime(1, false, r => done.push(r));
  ui.submit(); await settle();
  assert.equal(isOpen('add-time'), true);
  assert.match(ui.fields().querySelector('[data-error]').textContent, /reset/);
  assert.equal(done.length, 0);
});
