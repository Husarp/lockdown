// A page whose load() fails, for the error card (window.__broken counts mounts and loads).
const log = (window.__broken = { mounts: 0, loads: 0 });
export function mount(el) { log.mounts++; el.innerHTML = '<div class="page"><h1>Broken</h1></div>'; }
export async function load() { log.loads++; throw new Error("the data could not be read"); }
