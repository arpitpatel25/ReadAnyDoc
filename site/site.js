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

// Hovering a value in the output outlines where it was read on the page, and the other way round.
const linked = [...document.querySelectorAll("[data-k]")];
linked.forEach((el) => {
  const key = el.dataset.k;
  const toggle = (on) => linked.forEach((x) => x.classList.toggle("on", on && x.dataset.k === key));
  el.addEventListener("mouseenter", () => toggle(true));
  el.addEventListener("mouseleave", () => toggle(false));
});
