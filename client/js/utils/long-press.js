/**
 * Long-press helper: calls handler(uid) when a pointer stays down on an
 * element matching `selector` (without moving) for `ms`. Returns a function
 * that tells the click handler whether the click that follows the long press
 * should be ignored.
 */
export function onLongPress(container, selector, handler, ms = 450) {
  let timer = null, sx = 0, sy = 0, suppress = false;
  const cancel = () => { clearTimeout(timer); timer = null; };
  container.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const el = e.target.closest(selector);
    if (!el) return;
    sx = e.clientX; sy = e.clientY;
    timer = setTimeout(() => { cancel(); suppress = true; handler(el); }, ms);
  });
  container.addEventListener("pointermove", (e) => {
    if (timer && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) cancel();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(t => container.addEventListener(t, cancel));
  container.addEventListener("scroll", cancel, { passive: true });
  return () => { const s = suppress; suppress = false; return s; };
}
