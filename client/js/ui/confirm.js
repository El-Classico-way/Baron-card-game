/**
 * In-page confirmation dialog. window.confirm() is unreliable (some embedded
 * viewers and many mobile webviews suppress it and return false), so every
 * "are you sure?" goes through this instead.
 *
 *   if (await confirmDialog({ title: "Opustit hru?", text: "…", ok: "Opustit" })) …
 */
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function confirmDialog({ title, text = "", ok = "Ano", cancel = "Zrušit", danger = false }) {
  const root = document.getElementById("confirm-dialog");
  if (!root) return Promise.resolve(false);
  return new Promise((resolve) => {
    const previous = document.activeElement;
    root.innerHTML = `
      <div class="cd__backdrop" data-answer="no"></div>
      <div class="cd__panel" role="document">
        <h2 id="cd-title">${esc(title)}</h2>
        ${text ? `<p>${esc(text)}</p>` : ""}
        <div class="cd__buttons">
          <button class="btn btn--ghost" type="button" data-answer="no">${esc(cancel)}</button>
          <button class="btn${danger ? " btn--danger" : ""}" type="button" data-answer="yes">${esc(ok)}</button>
        </div>
      </div>`;
    root.setAttribute("aria-labelledby", "cd-title");
    root.hidden = false;
    const finish = (answer) => {
      root.hidden = true;
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey, true);
      previous?.focus?.({ preventScroll: true });
      resolve(answer);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
    };
    root.onclick = (e) => {
      const a = e.target.closest("[data-answer]");
      if (a) finish(a.dataset.answer === "yes");
    };
    document.addEventListener("keydown", onKey, true);
    root.querySelector('[data-answer="no"].btn')?.focus({ preventScroll: true });
  });
}
