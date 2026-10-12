// What a group gives ONE member besides the group's rules (Blocking > Groups):
//   openCustomizeMember({name, overrides, groupRules, now, onDone})   "Extra limits for X": an own scheduled / limit /
//                          opening limit / temporary block for this member, ON TOP of the group's - the group's rules still
//                          apply and its time still fills the group's shared limits. OK checks the blockers with Python
//                          (rules_validate) and hands back the rule dicts Python read: onDone({rule_type: rule}).
//   openMemberWay({name, kind, hosted, groupWay, itemWay, way, onDone})   "How X is blocked": a member's own way in this
//                          group - an app's replaces the group's (Close, Minimize or Grey it ...), a site's is added on top, a
//                          program file is closed or minimised only. onDone(way string | null).
// Whether a lighter way or a removed / relaxed extra limit LOOSENS anything is not decided here: the page only sends what
// was chosen with Done, and Python (antibypass) asks for the challenge.
import { openDialog } from "./dialog.js";
import { createStack } from "./rules/rail.js";
import * as S from "./rules/summary.js";
import { el, esc } from "./ui.js";
import { createWays } from "./ways.js";

// ---------------------------------------------------------------- a member's own way

/** "Its own way" for a member. `groupWay`: what the group has chosen for its members of this kind (null: not chosen);
 *  `itemWay`: the member's own setting (what applies while the group has not chosen). */
export function openMemberWay({ name, kind, hosted, groupWay, itemWay, way, onDone }) {
  const base = groupWay != null ? groupWay : itemWay;
  const isApp = kind !== "site";
  const group = !isApp ? "site" : hosted ? "file" : "app";           // (a program file: Close / Minimize only)
  const ways = createWays({ groups: [group], compact: true, locked: isApp ? {} : { site: String(base || "dns").split(",").filter(Boolean) } });
  ways.set(group, isApp ? way || base || "close" : way);
  const note = isApp
    ? `This way replaces the group's${groupWay != null ? "" : " (the group hasn't chosen: the way it was set when it was added)"}.${hosted ? " A program file is closed or minimised only." : ""}`
    : `Greyed: ${groupWay != null ? "the group's way, which always applies" : "the way it was set when it was added (the group hasn't chosen)"}. Tick more to make it stricter for this member only.`;
  const body = el(`<div class="stack-sm"><p class="muted small" style="margin:0">${esc(note)}</p><p class="rule-err" data-k="err" role="alert" hidden></p></div>`);
  body.insertBefore(ways.el, body.querySelector("[data-k=err]"));
  openDialog({
    key: "member-way", title: `How ${name} is blocked`, body,
    buttons: [{ label: "Cancel" }, {
      label: "OK", kind: "primary",
      onClick: (h) => {
        const value = ways.value(group);
        if (isApp && !value) {
          const err = body.querySelector("[data-k=err]");
          err.textContent = "Tick Close app, Minimize, Grey it or Block internet for its own way.";
          err.hidden = false;
          return false;
        }
        h.close({ way: value });
        return false;
      },
    }],
    onClose: (result) => { if (result && typeof result === "object" && "way" in result) onDone(result.way); },
  });
}

// ---------------------------------------------------------------- extra limits

const NOTE = "The group's rules always apply to this member too, and its time still counts towards the group's shared limits. " +
  "These can only make it stricter: blocked hours add to the group's, allowed hours narrow them, and the first limit to run out blocks it.";

/**
 * "Extra limits for X". `overrides`: {rule_type: rule dict} the member has now; `groupRules`: the group's rules as the editor
 * has them right now (a starting point when a switch is turned on: the same values as the group's); `now`: () => the trusted time in
 * ms (only for "Keep (2h 05m left)"). onDone({rule_type: rule}) with what Python read from the forms.
 */
export function openCustomizeMember({ name, overrides = {}, groupRules = [], now, onDone }) {
  const stack = createStack({
    types: S.CUSTOMIZABLE, name, now,
    title: (t) => `Own ${S.RULE_NAMES[t].toLowerCase()} for this member`,
    onChange: paint,
  });
  const empty = el('<p class="muted small cm-empty" style="margin:0"></p>');
  const body = el(`<div class="stack"><p class="muted small" style="margin:0">${esc(NOTE)}</p></div>`);
  body.append(stack.el, empty);
  stack.load(Object.values(overrides));
  for (const r of groupRules) {                          // a switch turned on starts from the group's own values
    if (S.CUSTOMIZABLE.includes(r.rule_type) && !overrides[r.rule_type]) stack.editor(r.rule_type).load(r);
  }
  function paint() {
    const n = stack.ticked().length;
    const text = n ? "" : "No own limits: only the group's rules apply to this member.";
    if (empty.textContent !== text) empty.textContent = text;
  }
  paint();
  let busy = false;
  openDialog({
    key: "customize-member", title: `Extra limits for ${name}`, wide: true, body,
    buttons: [{ label: "Cancel" }, {
      label: "OK", kind: "primary",
      onClick: (h) => {
        if (busy) return false;
        busy = true;
        stack.validate().then((res) => {
          busy = false;
          if (res.valid) h.close({ rules: res.rules });
        }, () => { busy = false; });
        return false;
      },
    }],
    onClose: (result) => {
      if (result && result.rules) onDone(Object.fromEntries(result.rules.map((r) => [r.rule_type, r])));
    },
  });
  return stack;
}
