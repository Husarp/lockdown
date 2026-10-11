// The look, live and without a reload: theme (dark / amoled / light / system), accent colour and interface size.
// The colour maths is plain functions (tested in node); applyLook() only sets one attribute and a few CSS variables.

export const THEMES = ["dark", "amoled", "light", "system"];
export const SCALES = { auto: 1, 100: 1, 90: 0.9, 80: 0.8, 70: 0.7 };
export const DEFAULT_ACCENT = "#DB5126";      // Orange (ui_theme.ACCENTS)
export const CACHE_KEY = "ld.look";           // cosmetic cache read by the inline script in index.html

// What the soft tint and the hover colour are mixed into, per resolved theme (the theme's --surface).
const SURFACE = { light: "#FFFFFF", dark: "#232221", amoled: "#0E0D0D" };

export function parseHex(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function toHex([r, g, b]) {
  const h = (v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0");
  return ("#" + h(r) + h(g) + h(b)).toUpperCase();
}

/** Relative luminance (WCAG). */
export function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `t` of `b` mixed into `a`. */
export function mix(a, b, t) {
  return a.map((v, i) => v + (b[i] - v) * t);
}

/**
 * The accent as the tokens need it: --brand (the accent itself), --brand-hover, --brand-soft (a quiet tint of it on
 * the surface), --on-brand (text on the accent: white if that reads, else near black) and --on-brand-soft (text on
 * the tint, pushed light or dark until it reads at 4.5:1).
 */
export function deriveAccent(hex, theme) {
  const accent = parseHex(hex) || parseHex(DEFAULT_ACCENT);
  const dark = theme !== "light";
  const white = [255, 255, 255], black = [0, 0, 0], ink = parseHex("#1A1918");
  const surface = parseHex(SURFACE[theme] || SURFACE.dark);
  const soft = mix(surface, accent, dark ? 0.24 : 0.14);
  let onSoft = accent, t = 0;
  while (contrast(onSoft, soft) < 4.5 && t < 1) {
    t += 0.05;
    onSoft = mix(accent, dark ? white : black, t);
  }
  return {
    "--brand": toHex(accent),
    "--brand-hover": toHex(mix(accent, dark ? white : black, 0.14)),
    "--brand-soft": toHex(soft),
    "--on-brand": toHex(contrast(white, accent) >= 3.5 ? white : ink),
    "--on-brand-soft": toHex(onSoft),
  };
}

export function scaleValue(scale) {
  const v = SCALES[String(scale).toLowerCase()];
  return v === undefined ? 1 : v;
}

/** "system" follows the OS; everything else is itself. The result is what data-theme carries. */
export function resolveTheme(pref, systemDark) {
  if (pref === "system") return systemDark ? "dark" : "light";
  return THEMES.includes(pref) ? pref : "dark";
}

const state = { pref: "dark", accent: DEFAULT_ACCENT, scale: "auto" };
let mediaWin = null;

function systemDark(win) {
  return !!(win.matchMedia && win.matchMedia("(prefers-color-scheme: dark)").matches);
}

function writeCache(win) {
  const vars = {};
  for (const t of ["dark", "amoled", "light"]) vars[t] = deriveAccent(state.accent, t);
  try {
    win.localStorage.setItem(CACHE_KEY, JSON.stringify({ pref: state.pref, vars, s: String(scaleValue(state.scale)) }));
  } catch (_) { /* a blocked storage only costs the first-paint colours next time */ }
}

/** Apply whatever of {theme, accent, scale, motion} is given. Live: attributes and CSS variables only. */
export function applyLook(look = {}, win = window) {
  const doc = win.document, root = doc.documentElement;
  if (look.theme !== undefined) state.pref = THEMES.includes(look.theme) ? look.theme : "dark";
  if (look.accent !== undefined && parseHex(look.accent)) state.accent = look.accent.toUpperCase();
  if (look.scale !== undefined) state.scale = String(look.scale).toLowerCase();
  if (look.motion !== undefined) { if (look.motion) root.dataset.motion = "off"; else delete root.dataset.motion; }   // Reduce motion (css/app.css)
  const theme = resolveTheme(state.pref, systemDark(win));
  root.dataset.theme = theme;
  for (const [k, v] of Object.entries(deriveAccent(state.accent, theme))) root.style.setProperty(k, v);
  const s = scaleValue(state.scale);
  const changed = root.style.getPropertyValue("--s") !== String(s);
  root.style.setProperty("--s", String(s));
  if (changed) win.dispatchEvent(new win.Event("ld:scale"));       // lists measure their row height again
  writeCache(win);
  if (mediaWin !== win && win.matchMedia) {                         // "System" follows the OS, live
    mediaWin = win;
    win.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.pref === "system" && applyLook({}, win));
  }
  return { theme, ...state };
}

export const currentLook = () => ({ ...state });
