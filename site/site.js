// Headline: reveal word by word, once, on load.
document.querySelectorAll(".reveal").forEach((el) => {
  const words = el.textContent.trim().split(/\s+/);
  el.setAttribute("aria-label", el.textContent.trim());
  el.innerHTML = words
    .map((w, i) => `<span class="w" aria-hidden="true" style="animation-delay:${0.08 + i * 0.07}s">${w}${i < words.length - 1 ? " " : ""}</span>`)
    .join("");
});

// Copy the install command.
document.querySelectorAll(".install").forEach((button) => {
  button.addEventListener("click", async () => {
    const state = button.querySelector(".copy-state");
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      state.textContent = "Copied";
    } catch {
      state.textContent = "Select to copy";
    }
    setTimeout(() => (state.textContent = "Copy"), 1800);
  });
});

// Hovering a value outlines where it was read on the page, and the other way round.
const linked = [...document.querySelectorAll("[data-k]")];
linked.forEach((el) => {
  const toggle = (on) => linked.forEach((x) => x.classList.toggle("on", on && x.dataset.k === el.dataset.k));
  el.addEventListener("mouseenter", () => toggle(true));
  el.addEventListener("mouseleave", () => toggle(false));
});
