import { dom } from "../dom.js";

const COMPOSER_MIN_H = 48;
const COMPOSER_MAX_H_CAP = 280;

export function autoResizeComposer() {
  const el = dom.inputEl;
  if (!el) return;
  el.style.height = `${COMPOSER_MIN_H}px`;
  const maxH = Math.min(Math.round(window.innerHeight * 0.42), COMPOSER_MAX_H_CAP);
  const next = Math.min(Math.max(el.scrollHeight, COMPOSER_MIN_H), maxH);
  el.style.height = `${next}px`;
}

export function resetComposerHeight() {
  if (!dom.inputEl) return;
  dom.inputEl.style.height = "";
  autoResizeComposer();
}

export function attachComposerUX() {
  if (!dom.inputEl || !dom.composer) return;

  dom.inputEl.addEventListener("input", autoResizeComposer);
  dom.inputEl.addEventListener("paste", () => requestAnimationFrame(autoResizeComposer));
  window.addEventListener("resize", () => autoResizeComposer());

  dom.inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      dom.composer.requestSubmit();
    }
  });
}

