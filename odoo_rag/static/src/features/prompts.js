import { dom } from "../dom.js";
import { autoResizeComposer } from "../ui/composer.js";

function applyModuleSelection(moduleId, label) {
  document
    .querySelectorAll(".module-item")
    .forEach((b) => b.classList.toggle("active", b.dataset.module === moduleId));
  const crumb = document.getElementById("moduleCrumb");
  if (crumb && label) crumb.textContent = label;
}

export function attachPrompts() {
  document.querySelectorAll(".module-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const moduleId = btn.dataset.module;
      const prompt = btn.dataset.prompt || "";
      applyModuleSelection(moduleId, btn.textContent.trim());
      if (prompt) {
        dom.inputEl.value = prompt;
        autoResizeComposer();
        dom.inputEl.focus();
      }
    });
  });

  document.querySelectorAll(".quick-prompt").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = btn.dataset.prompt || "";
      if (!p) return;
      dom.inputEl.value = p;
      autoResizeComposer();
      dom.inputEl.focus();
    });
  });
}

