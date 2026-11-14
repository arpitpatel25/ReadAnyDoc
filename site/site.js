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

// Hero: a live example that walks through three documents, field by field.
(() => {
  const page = document.getElementById("live-page");
  if (!page) return;
  const docs = [
    { title: ["Northwind Freight Ltd.", "INVOICE"],
      lines: ["Invoice no. {INV-2041}", "Issued {01 Mar 2026}", "Bill to {Harbor Supply Co.}", "Freight to Bremen 1,200.00", "Total due {£1,575.00}"],
      fields: [["number", '"INV-2041"', 2], ["issued", '"2026-03-01"', 3], ["customer", '"Harbor Supply Co."', 4], ["total", "1575.0", 6]] },
    { title: ["First Harbor Bank", "STATEMENT"],
      lines: ["Account {4471 0093}", "Period {1 to 31 Jan 2026}", "Opening balance $12,480.22", "Card payments -$2,608.82", "Closing balance {$9,871.40}"],
      fields: [["account", '"4471 0093"', 2], ["period", '"2026-01"', 3], ["closing_balance", "9871.4", 6]] },
    { title: ["Vendor registration", "FORM V-2"],
      lines: ["Legal name {Alder & Moss LLC}", "Tax ID {84-1927736}", "Entity type ☑ {Limited liability company}", "Contact Jo Park, accounts", "Signed {12 Feb 2026}"],
      fields: [["legal_name", '"Alder & Moss LLC"', 2], ["tax_id", '"84-1927736"', 3], ["entity_type", '"LLC"', 4], ["signed", '"2026-02-12"', 6]] },
  ];
  const tabs = [...document.querySelectorAll(".live-tabs span")];
  const out = document.querySelector(".live-out");
  const key = document.getElementById("live-key"), val = document.getElementById("live-val"), cite = document.getElementById("live-cite");
  const conf = ["high", "high", "high", "medium"];
  let d = 0, f = 0;
  function render() {
    const doc = docs[d];
    let n = 0;
    page.innerHTML = `<div class="t"><span>${doc.title[0]}</span><span>${doc.title[1]}</span></div>` +
      doc.lines.map((l) => "<div>" + l.replace(/\{([^}]*)\}/g, (_, x) => `<span class="f" data-i="${n++}">${x}</span>`) + "</div>").join("");
    tabs.forEach((t, i) => t.classList.toggle("on", i === d));
  }
  function step() {
    const doc = docs[d], [k, v, line] = doc.fields[f];
    page.querySelectorAll(".f").forEach((el) => el.classList.toggle("on", +el.dataset.i === f));
    out.classList.add("swap");
    setTimeout(() => {
      key.textContent = k; val.textContent = v;
      cite.textContent = `page 1, line ${line}, confidence ${conf[(d + f) % conf.length]}`;
      out.classList.remove("swap");
    }, 220);
  }
  function tick() {
    f++;
    if (f >= docs[d].fields.length) { f = 0; d = (d + 1) % docs.length; render(); }
    step();
  }
  render(); step();
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) setInterval(tick, 2200);
})();
