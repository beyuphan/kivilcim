// On a phone the panel is a sheet over the map with three heights: peek (the search and a summary),
// half, and full. Drag the handle or tap it. On a wide screen it is just a side panel and this does nothing.

const SNAPS = ["peek", "half", "full"];
const narrow = matchMedia("(max-width: 899px)");

export function initSheet(panel, handle, onChange = () => {}) {
  let snap = "half";
  const set = (next) => {
    snap = next;
    panel.dataset.snap = next;
    handle.setAttribute("aria-expanded", String(next !== "peek"));
    handle.setAttribute("aria-label", next === "full" ? "Paneli küçült" : "Paneli büyüt");
    onChange(next);
  };
  set(snap);

  handle.addEventListener("click", () => {
    if (handle.dataset.dragged === "true") return;
    set(SNAPS[(SNAPS.indexOf(snap) + 1) % SNAPS.length]);
  });

  let startY = 0;
  let startTop = 0;
  handle.addEventListener("pointerdown", (e) => {
    if (!narrow.matches) return;
    handle.setPointerCapture(e.pointerId);
    startY = e.clientY;
    startTop = panel.getBoundingClientRect().top;
    handle.dataset.dragged = "false";
    panel.dataset.dragging = "true";
  });
  handle.addEventListener("pointermove", (e) => {
    if (panel.dataset.dragging !== "true") return;
    const dy = e.clientY - startY;
    if (Math.abs(dy) > 4) handle.dataset.dragged = "true";
    const stageTop = panel.parentElement.getBoundingClientRect().top;
    const offset = Math.max(0, startTop - stageTop + dy);
    panel.style.transform = `translateY(${offset}px)`;
  });
  const end = (e) => {
    if (panel.dataset.dragging !== "true") return;
    panel.dataset.dragging = "false";
    const stage = panel.parentElement.getBoundingClientRect();
    const visible = stage.bottom - panel.getBoundingClientRect().top;
    panel.style.transform = "";
    if (handle.dataset.dragged === "true") {
      const targets = { peek: 188, half: window.innerHeight * 0.54, full: stage.height };
      const closest = SNAPS.reduce((best, s) => (Math.abs(targets[s] - visible) < Math.abs(targets[best] - visible) ? s : best), "half");
      set(closest);
      setTimeout(() => { handle.dataset.dragged = "false"; }, 0);
    }
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);

  return { set, get: () => snap };
}
