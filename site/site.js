// Copy the install command.
document.querySelectorAll(".install").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const state = btn.querySelector(".copy-state");
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      state.textContent = "Copied";
    } catch {
      state.textContent = "Select and copy";
    }
    setTimeout(() => (state.textContent = "Copy"), 1800);
  });
});

// Hovering or focusing an extracted value highlights where it was read on the page, and the other way round.
const marks = [...document.querySelectorAll(".page mark")];
const rows = [...document.querySelectorAll(".json .row")];
function show(keys, on) {
  marks.forEach((m) => m.classList.toggle("on", on && keys.includes(m.dataset.k)));
  rows.forEach((r) => r.classList.toggle("on", on && r.dataset.k.split(" ").some((k) => keys.includes(k))));
}
rows.forEach((r) => {
  r.tabIndex = 0;
  const keys = r.dataset.k.split(" ");
  ["mouseenter", "focus"].forEach((e) => r.addEventListener(e, () => show(keys, true)));
  ["mouseleave", "blur"].forEach((e) => r.addEventListener(e, () => show(keys, false)));
});
marks.forEach((m) => {
  m.addEventListener("mouseenter", () => show([m.dataset.k], true));
  m.addEventListener("mouseleave", () => show([m.dataset.k], false));
});
