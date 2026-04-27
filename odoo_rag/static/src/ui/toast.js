import { dom } from "../dom.js";

export function showToast(text, isError = false) {
  if (!dom.toast) return;
  dom.toast.textContent = text;
  dom.toast.hidden = false;
  dom.toast.classList.toggle("error", isError);
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    dom.toast.hidden = true;
  }, 4200);
}

