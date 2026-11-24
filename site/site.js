// Headline: reveal word by word, once, on load.
document.querySelectorAll(".reveal").forEach((el) => {
  // Split on spaces outside tags, so a logo-with-name (.bl) stays one word.
  const list = el.innerHTML.trim().replace(/\s+/g, " ").split(/ (?![^<]*>)/);
  el.setAttribute("aria-label", el.textContent.trim());
  el.innerHTML = list
    .map((w, i) => `<span class="w" aria-hidden="true" style="animation-delay:${0.08 + i * 0.07}s">${w}${i < list.length - 1 ? " " : ""}</span>`)
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
  const conf = ["high", "high", "high", "low"];
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

// Resources menu: open on click (touch), close on outside click or Escape.
document.querySelectorAll(".has-menu").forEach((m) => {
  const btn = m.querySelector(".menu-btn");
  btn.addEventListener("click", () => {
    const open = m.classList.toggle("open");
    btn.setAttribute("aria-expanded", open);
  });
  document.addEventListener("click", (e) => { if (!m.contains(e.target)) { m.classList.remove("open"); btn.setAttribute("aria-expanded", false); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { m.classList.remove("open"); btn.setAttribute("aria-expanded", false); } });
});

// Live GitHub star count on the star buttons (silently skipped if the API is unreachable).
fetch("https://api.github.com/repos/arpitpatel25/ReadAnyDoc")
  .then((r) => (r.ok ? r.json() : null))
  .then((d) => {
    if (!d || typeof d.stargazers_count !== "number" || d.stargazers_count < 1) return;
    const n = d.stargazers_count >= 1000 ? (d.stargazers_count / 1000).toFixed(1) + "k" : String(d.stargazers_count);
    document.querySelectorAll("[data-stars]").forEach((el) => { el.textContent = n; el.hidden = false; });
  })
  .catch(() => {});

// One gentle star prompt per visitor: after most of the page is read, never again once closed or clicked.
(() => {
  const card = document.getElementById("star-card");
  if (!card) return;
  let seen = false;
  try { seen = localStorage.getItem("rad-star-card") === "1"; } catch (e) {}
  if (seen) return;
  const remember = () => { try { localStorage.setItem("rad-star-card", "1"); } catch (e) {} };
  const onScroll = () => {
    const read = (window.scrollY + window.innerHeight) / document.documentElement.scrollHeight;
    if (read > 0.6) { card.hidden = false; remember(); window.removeEventListener("scroll", onScroll); }
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  card.querySelectorAll("[data-star-close]").forEach((el) => el.addEventListener("click", () => { card.hidden = true; }));
})();

// Extraction walkthrough: one real run (ADP 10-Q, 43 pages, 4 parts, 12 field groups), one step at a time.
(() => {
  const root = document.querySelector(".xflow");
  if (!root) return;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const PARTS = [[1, 12], [13, 24], [25, 36], [37, 43]];
  const pages = root.querySelector(".xf-pages");
  for (let n = 1; n <= 43; n++) {
    const i = document.createElement("i");
    const part = PARTS.findIndex(([a, b]) => n >= a && n <= b) + 1;
    i.className = "p" + part + (n === 3 ? " hit" : "");
    pages.appendChild(i);
  }
  const groups = root.querySelector(".xf-groups");
  for (let g = 1; g <= 12; g++) groups.insertAdjacentHTML("beforeend", `<span>Group ${g}<small>up to 40 fields</small></span>`);
  const matrix = root.querySelector(".xf-matrix");
  PARTS.forEach((_, p) => {
    matrix.insertAdjacentHTML("beforeend", `<em>Part ${p + 1}</em>`);
    for (let g = 0; g < 12; g++) matrix.insertAdjacentHTML("beforeend", `<i class="${g === 0 ? "w" : "r"}" data-p="${p}" data-g="${g}"></i>`);
  });
  const tiles = [...pages.children], cells = [...matrix.querySelectorAll("i")];
  const tabs = [...root.querySelectorAll('[role="tab"]')], panes = [...root.querySelectorAll('[role="tabpanel"]')];
  let timers = [];
  const later = (fn, ms) => timers.push(setTimeout(fn, still ? 0 : ms));
  const clear = () => { timers.forEach(clearTimeout); timers = []; };

  function enter(step) {
    clear();
    if (step === 1) {  // pages are read 16 at a time
      tiles.forEach((t) => t.classList.remove("read"));
      tiles.forEach((t, k) => later(() => t.classList.add("read"), 120 + Math.floor(k / 16) * 520 + (k % 16) * 18));
    } else tiles.forEach((t) => t.classList.add("read"));
    if (step === 4) {  // the first call of each part writes the cache, then the rest go together
      cells.forEach((c) => c.classList.remove("on"));
      cells.filter((c) => c.dataset.g === "0").forEach((c, k) => later(() => c.classList.add("on"), 150 + k * 60));
      cells.filter((c) => c.dataset.g !== "0").forEach((c) => later(() => c.classList.add("on"), 900 + +c.dataset.g * 45 + +c.dataset.p * 25));
    } else cells.forEach((c) => c.classList.add("on"));
  }
  function show(step, focus) {
    root.dataset.step = step;
    tabs.forEach((t) => {
      const on = +t.dataset.s === step;
      t.setAttribute("aria-selected", on); t.tabIndex = on ? 0 : -1;
      if (on && focus) t.focus();
    });
    panes.forEach((p, k) => (p.hidden = k + 1 !== step));
    enter(step);
  }
  let playing = null;
  const play = root.querySelector(".xf-play");
  function stop() { clearInterval(playing); playing = null; play.textContent = "Play all steps"; }
  tabs.forEach((t) => {
    t.addEventListener("click", () => { stop(); show(+t.dataset.s); });
    t.addEventListener("keydown", (e) => {
      const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (k) { e.preventDefault(); stop(); show(((+t.dataset.s - 1 + k + 6) % 6) + 1, true); }
      if (e.key === "Home") { e.preventDefault(); stop(); show(1, true); }
      if (e.key === "End") { e.preventDefault(); stop(); show(6, true); }
    });
  });
  play.addEventListener("click", () => {
    if (playing) return stop();
    let s = 1; show(s); play.textContent = "Pause";
    playing = setInterval(() => { s += 1; if (s > 6) return stop(); show(s); }, 3400);
  });
  show(1);
  // the page reading plays once, when the walkthrough first comes into view
  if ("IntersectionObserver" in window && !still) {
    tiles.forEach((t) => t.classList.remove("read")); clear();
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { io.disconnect(); if (root.dataset.step === "1") enter(1); }
    }, { threshold: 0.35 });
    io.observe(root);
  }
})();
