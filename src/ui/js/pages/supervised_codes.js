// Keys stay in Python; the page receives only current codes and labels.
import { ld } from "../bridge.js";
import { openDialog, confirmDialog } from "../dialog.js";
import { esc, toast } from "../ui.js";

export function supervisedSection() {
  return `<section class="card"><header class="card-h"><h3>Codes I supervise</h3></header>
    <div class="card-b"><p>Keep other people's supervisor keys here and share a code when you approve their change. Keys stay on this PC and are excluded from backups.</p>
    <button class="btn" type="button" data-sc="add">Add a key</button>
    <div data-sc-list></div></div></section>`;
}

export function codeRows(data) {
  return data.entries.map((r) => `<div class="field"><div class="field-l"><b>${esc(r.name)}</b>
    <div><span class="sx-key">${esc(r.code)}</span> <span class="small muted">${data.remaining}s left</span></div></div>
    ${["copy", "rename", "delete"].map((act) => `<button class="btn btn-sm" type="button" data-sc="${act}" data-id="${esc(r.id)}">${act[0].toUpperCase() + act.slice(1)}</button>`).join("")}</div>`).join("") || '<p class="muted">No supervised keys yet.</p>';
}

export function wireSupervised(root, page) {
  let busy = false, loading = false;
  const list = root.querySelector('[data-sc-list]');
  const paint = (data) => { list.innerHTML = codeRows(data); };
  async function refresh() {
    if (loading) return;
    loading = true;
    try { paint(await ld.call("supervised_codes_get")); }
    catch (_) { list.textContent = "Couldn't load codes. Retrying…"; }
    finally { loading = false; }
  }
  root.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-sc]");
    if (!b || busy) return;
    busy = true;
    try {
      const act = b.dataset.sc, id = b.dataset.id || "";
      if (act === "copy") {
        const fresh = await ld.call("supervised_codes_get");
        paint(fresh);
        const row = fresh.entries.find((r) => r.id === id);
        if (!row) throw new Error("That supervised key is unavailable.");
        await navigator.clipboard.writeText(row.code);
        toast("Copied");
      } else if (act === "delete") {
        if (await confirmDialog({ key: "sc-delete", title: "Delete supervised key?", text: "You will need their key again to generate more codes.", ok: "Delete", danger: true }))
          paint(await ld.call("supervised_codes_change", "delete", "", "", id));
      } else {
        const row = (await ld.call("supervised_codes_get")).entries.find((r) => r.id === id);
        let sending = false;
        const h = openDialog({ key: "sc-edit", title: act === "add" ? "Add someone else's supervisor key" : "Rename supervised key",
          body: `<label class="sx-l">Name<input class="input" data-name maxlength="100" value="${esc(row ? row.name : "")}" autocomplete="off"></label>
            ${act === "add" ? '<label class="sx-l">Base32 key or otpauth:// URI<input class="input" data-key autocomplete="off" spellcheck="false"></label>' : ""}
            <p class="sx-err" data-error role="alert"></p>`,
          buttons: [{ label: "Cancel", value: false }, { label: "Save", kind: "primary", onClick: () => { save(); return false; } }],
        });
        if (!h) return;
        h.body.querySelector("[data-name]").focus();
        async function save() {
          if (sending) return;
          sending = true;
          try {
            const name = h.body.querySelector("[data-name]").value;
            const key = h.body.querySelector("[data-key]");
            paint(await ld.call("supervised_codes_change", act, name, key ? key.value : "", id));
            if (key) key.value = "";
            h.close(true);
          } catch (err) { h.body.querySelector("[data-error]").textContent = err.message || "Couldn't save key."; }
          finally { sending = false; }
        }
        await h.done;
      }
    } catch (err) { toast(err.message || "Couldn't copy the code."); }
    finally { busy = false; }
  });
  page.every(refresh);
  refresh();
}
