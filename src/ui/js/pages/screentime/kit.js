// Small pieces the Screen Time tabs share (no DOM work at import, so node can test them).

/** Keep `host`'s children equal to `rows` (keyed): a row that stays is reused and only updated, a new one is built, a gone
 *  one removed, and a node is moved only when it is not where it belongs. build(row) -> node, update(node, row). */
export function syncRows(host, rows, build, update) {
  const old = host._rows || new Map();
  const next = new Map();
  let prev = null;
  for (const r of rows) {
    let node = old.get(r.key);
    if (node) old.delete(r.key); else node = build(r);
    update(node, r);
    next.set(r.key, node);
    const want = prev ? prev.nextSibling : host.firstChild;
    if (node !== want) host.insertBefore(node, want);
    prev = node;
  }
  for (const node of old.values()) node.remove();
  host._rows = next;
}

/** A legend row `<span style="--k:colour"><i></i>name <b>value</b></span>`, kept and updated in place by legendUpdate. */
export function legendItem(valued = false) {
  const node = document.createElement("span");
  node._label = document.createTextNode("");
  node.append(document.createElement("i"), node._label);
  if (valued) {
    node._value = document.createElement("b");
    node.append(node._value);
  }
  return node;
}

/** Write a legend row from {color, name, value?}; only what changed. */
export function legendUpdate(node, r) {
  setVar(node, "--k", r.color);
  if (node._label.data !== r.name) node._label.data = r.name;
  if (node._value && node._value.textContent !== (r.value || "")) node._value.textContent = r.value || "";
}

/** Set a node's CSS variable only when it changed. */
export function setVar(node, name, value) {
  if (node.style.getPropertyValue(name) !== value) node.style.setProperty(name, value);
}
