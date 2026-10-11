// App / site icons served by Python (resident/icons.py): the URL of one, and the swap when the real icon has arrived.
//   <img data-icon="app:chrome.exe" src="...">  - iconUrl("app", "chrome.exe") gives the src; until the real icon is
//   in the disk cache the server answers with a letter tile, and pushes `icon_ready {key}` when it has the real one.
//   wireIconReady(ld.on) swaps the src of every <img data-icon="<key>"> in the page (with a cache-buster).

/** "/icon/app/chrome.exe.png", "/icon/site/example.com.png", "/icon/letter/Spotify.svg" (+ ?v= when given). */
export function iconUrl(kind, name, version) {
  const ext = kind === "letter" ? "svg" : "png";
  const clean = kind === "letter" ? String(name) : String(name).toLowerCase();
  return `/icon/${kind}/${encodeURIComponent(clean)}.${ext}${version ? "?v=" + version : ""}`;
}

/** "app:chrome.exe" -> {kind: "app", name: "chrome.exe"} (the key of an icon_ready push and of data-icon). */
export function parseKey(key) {
  const i = String(key).indexOf(":");
  return i < 0 ? null : { kind: key.slice(0, i), name: key.slice(i + 1) };
}

export const iconKey = (kind, name) => `${kind}:${String(name).toLowerCase()}`;

/** <img> markup for an app / site icon (a string, for a page's first mount). */
export const iconImg = (kind, name, cls = "") =>
  `<img class="${cls}" data-icon="${iconKey(kind, name).replace(/"/g, "&quot;")}" src="${iconUrl(kind, name)}" alt="" loading="lazy" decoding="async" draggable="false">`;

/** Subscribe (ld.on) so a finished icon replaces its letter tile. Returns the unsubscribe function. */
export function wireIconReady(on, root = document, now = () => Date.now()) {
  return on("icon_ready", (payload) => {
    const parsed = payload && parseKey(payload.key);
    if (!parsed) return;
    const src = iconUrl(parsed.kind, parsed.name, now());
    for (const img of root.querySelectorAll("img[data-icon]")) {
      if (img.dataset.icon === payload.key) img.src = src;
    }
  });
}
