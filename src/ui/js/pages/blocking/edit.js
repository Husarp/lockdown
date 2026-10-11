// Blocking > Add tab, edit mode: the bar over the form when an item is opened for editing - its Alerts menu, Disable / Enable and
// a two-click Remove - and the words around it ("Also in groups: ..."). add.js builds the form itself (it is the same form as
// for adding); this file is only what is different when editing.
//
//   const head = createEditHead({ onAlerts(value), onDisable(), onRemove() });
//   head.el          the buttons (hidden until show())
//   head.show(item)  an item from Api `item_get` -> fills Alerts / Disable / Remove
//   head.hide()
//   head.setNotify(v) / head.setDisabled(bool)
//
// Each button asks the draft through add.js's callbacks (js/draft.js): what loosens - Disable, Remove, a looser Save - asks
// Python for the challenge, never this file.
import { openMenu } from "../../menu.js";
import { confirmButton, el, ic, setText } from "../../ui.js";

export const ALERT_CHOICES = [[null, "Default"], ["on", "On"], ["off", "Off"]];

/** "Default" / "On" / "Off" for an item's notify value. */
export const alertWord = (v) => (ALERT_CHOICES.find(([x]) => x === (v ?? null)) || ALERT_CHOICES[0])[1];

/** The grey line under the title: the groups the item is in, and where a group's own way applies instead of the item's. */
export function groupsLine(item) {
  const groups = item.groups || [];
  if (!groups.length) return "";
  const chose = groups.filter((g) => g.chose).map((g) => g.name);
  const note = chose.length ? ` - when ${chose.join(", ")} blocks it, that group's way of blocking applies` : "";
  return `Also in groups: ${groups.map((g) => g.name).join(", ")} (edit those in the Groups tab)${note}`;
}

export const title = (item) => `Edit ${item.name}`;

export function createEditHead({ onAlerts, onDisable, onRemove }) {
  const root = el(`<div class="ad-editacts" hidden>
    <button class="btn btn-sm" type="button" data-alerts aria-haspopup="menu" title="Blocked-visit alerts for this item">${ic("bell")}<span class="muted">Alerts:</span><span data-word>Default</span></button>
    <button class="btn btn-sm" type="button" data-disable>Disable</button>
  </div>`);
  const alerts = root.querySelector("[data-alerts]"), word = root.querySelector("[data-word]"), disable = root.querySelector("[data-disable]");
  const remove = confirmButton({ label: "Remove", confirmLabel: "Click again to remove", cls: "btn btn-sm btn-danger", ms: 4000, onConfirm: () => onRemove() });
  root.append(remove);
  let notify = null;

  alerts.addEventListener("click", () => openMenu(alerts, [{ heading: "Blocked-visit alerts" }, ...ALERT_CHOICES.map(([v, label]) => ({
    label, checked: v === notify, onClick: () => { if (v !== notify) onAlerts(v); },
  }))]));
  disable.addEventListener("click", () => onDisable());

  const api = {
    el: root,
    show(item) {
      api.setNotify(item.notify ?? null);
      api.setDisabled(!!item.disabled);
      root.hidden = false;
    },
    hide() { root.hidden = true; remove.classList.remove("armed"); remove.textContent = "Remove"; },
    setNotify(v) { notify = v ?? null; setText(word, alertWord(notify)); },
    setDisabled(on) { setText(disable, on ? "Enable" : "Disable"); disable.dataset.on = on ? "1" : ""; },
    get disabled() { return disable.dataset.on === "1"; },
  };
  return api;
}
