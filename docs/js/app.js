import { loadTitleList, buildIndex, search, highlightParts, MAX_RESULTS, MIN_QUERY_LENGTH } from "./picker.js";
import CONFIG from "./config.js";

// ------------------------------------------------------------------ setup --
const START_POINTS = 1000;
const MAX_GUESSES = 3;
const NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"];
const HINTS = [
  { key: "cover", label: "Cover", emoji: "🖼️", cost: 150 },
  { key: "summary", label: "Summary", emoji: "📖", cost: 100 },
  { key: "tags", label: "Tags", emoji: "🏷️", cost: 50 },
  { key: "creators", label: "Author & artist", emoji: "✏️", cost: 50 },
  { key: "characters", label: "Characters", emoji: "😀", cost: 75 },
  { key: "review0", label: "Review 1", emoji: "🗣️", cost: 25 },
  { key: "review1", label: "Review 2", emoji: "🗣️", cost: 25 },
  { key: "review2", label: "Review 3", emoji: "🗣️", cost: 25 },
];
const ORIGIN_LABEL = { manga: "Manga (Japan)", manhwa: "Manhwa (Korea)", manhua: "Manhua (China)" };

const $ = (sel) => document.querySelector(sel);
const fmt = (n) => Number(n || 0).toLocaleString("en-US");

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function todayIn(timezone) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

function playerId() {
  let id = localStorage.getItem("mangadle:player");
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());
    localStorage.setItem("mangadle:player", id);
  }
  return id;
}

// ------------------------------------------------------------------ state --
let day = null;          // the day file
let titleIndex = [];     // searchable answer list for the guess dropdown
let state = null;        // the player's progress for that day
let current = 0;         // index of the title on screen, or "results"
const params = new URLSearchParams(location.search);
const date = params.get("date") || todayIn(CONFIG.timezone);
const storageKey = `mangadle:day:${date}`;
// Dev helpers: ?api=http://localhost:8787 uses a local worker; ?reset wipes this day's progress.
const WORKER_URL = (params.has("api") ? params.get("api") : CONFIG.workerUrl || "").replace(/\/$/, "");
if (params.has("reset")) {
  localStorage.removeItem(storageKey);
  params.delete("reset");
  history.replaceState(null, "", location.pathname + (params.toString() ? `?${params}` : ""));
}

function freshState(n) {
  return {
    titles: Array.from({ length: n }, () => ({ revealed: [], spoilers: [], guesses: [], guess: null, correct: null, points: null })),
    submitted: false,
  };
}

// Identifies which titles a day file holds, so progress resets if the day gets rebuilt.
const dayFingerprint = () => day.titles.map((t) => t.anilist_id).join(",");

function loadState() {
  try {
    const s = JSON.parse(localStorage.getItem(storageKey));
    // Progress only carries over if it was saved for exactly these titles. Progress saved
    // before fingerprints existed can't be checked, so it starts over once.
    if (s && s.titles && s.fingerprint === dayFingerprint()) return s;
  } catch { /* fall through */ }
  return { ...freshState(day.titles.length), fingerprint: dayFingerprint() };
}

function saveState() {
  localStorage.setItem(storageKey, JSON.stringify(state));
}

const isDone = (i) => state.titles[i].correct !== null;
// Titles unlock in order: you can revisit answered ones and play the first unanswered one.
const firstOpen = () => { const k = state.titles.findIndex((s) => s.correct === null); return k === -1 ? state.titles.length : k; };
const isLocked = (i) => i > firstOpen();
const allDone = () => state.titles.every((_, i) => isDone(i));

// ------------------------------------------------------------------ hints --
function hintInfo(title, key) {
  const hints = title.hints || {};
  if (key.startsWith("review")) {
    const r = (hints.reviews || [])[Number(key.slice(6))];
    return { available: !!r, cost: r ? r.cost ?? 25 : 25, data: r };
  }
  const data = hints[key];
  const def = HINTS.find((x) => x.key === key);
  let available = false;
  if (key === "cover") available = !!(data && data.url);
  else if (key === "summary") available = !!(data && data.text);
  else if (key === "tags" || key === "creators") available = !!(data && data.items && data.items.length);
  else if (key === "characters") available = !!(data && ((data.main || []).length || (data.supporting || []).length));
  return { available, cost: (data && data.cost) ?? def.cost, data };
}

function pointsLeft(i) {
  const t = day.titles[i];
  const spent = state.titles[i].revealed.reduce((sum, key) => sum + hintInfo(t, key).cost, 0);
  return Math.max(0, START_POINTS - spent);
}

function revealHint(i, key) {
  const s = state.titles[i];
  if (isDone(i) || s.revealed.includes(key)) return;
  s.revealed.push(key);
  saveState();
  render();
}

// ---------------------------------------------------------------- guessing --
// Up to MAX_GUESSES guesses per title. A wrong guess only uses up a guess; points come
// from hints alone. Out of guesses (or giving up) ends the title at 0.
// Returns a message for the guess form, or null when the title is finished.
// pick = { id, name } chosen from the dropdown. A guess is right when it's the same AniList entry.
function submitGuess(i, pick, giveUp = false) {
  const s = state.titles[i];
  if (isDone(i)) return null;
  s.guesses = s.guesses || [];
  s.guessIds = s.guessIds || [];
  if (giveUp) {
    s.correct = false;
    s.points = 0;
  } else {
    if (s.guessIds.includes(pick.id)) return `You already guessed “${pick.name}”.`;
    s.guesses.push(pick.name);
    s.guessIds.push(pick.id);
    s.guess = pick.name;
    if (pick.id === day.titles[i].anilist_id) {
      s.correct = true;
      s.points = pointsLeft(i);
    } else if (s.guesses.length >= MAX_GUESSES) {
      s.correct = false;
      s.points = 0;
    }
  }
  saveState();
  if (allDone()) submitResult();
  render();
  return null;
}

// ---------------------------------------------------------------- rendering --
function render() {
  renderTopbar();
  renderNav();
  const app = $("#app");
  if (current !== "results" && isLocked(current)) current = firstOpen();
  app.replaceChildren(current === "results" ? renderResults() : renderTitle(current));
}

function renderTopbar() {
  const earned = state.titles.reduce((a, s) => a + (s.points || 0), 0);
  $("#puzzle-meta").textContent = `#${day.number} · ${date}`;
  $("#total-score").textContent = `🏆 ${fmt(earned)}`;
}

function renderNav() {
  const nav = $("#title-nav");
  nav.replaceChildren(
    ...day.titles.map((_, i) => {
      const s = state.titles[i];
      const status = s.correct === true ? "right" : s.correct === false ? "wrong" : "";
      const locked = isLocked(i);
      return h("button", {
        class: `nav-pill ${status} ${current === i ? "active" : ""} ${locked ? "locked" : ""}`,
        "aria-label": locked ? `Title ${i + 1} (locked until you answer title ${firstOpen() + 1})` : `Title ${i + 1}`,
        title: locked ? "Answer the current title first" : null,
        disabled: locked,
        onclick: () => { current = i; render(); },
      }, s.correct === true ? "✓" : s.correct === false ? "✗" : String(i + 1));
    }),
    h("button", {
      class: `nav-pill results-pill ${current === "results" ? "active" : ""}`,
      disabled: !allDone(),
      title: allDone() ? "Results" : "Finish all titles to see results",
      onclick: () => { current = "results"; render(); },
    }, h("span", { class: "long" }, "Results"), h("span", { class: "short", "aria-hidden": "true" }, "🏆")),
  );
}

function infoItem(label, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return null;
  return h("div", { class: "info-item" }, h("span", { class: "info-label" }, label), h("span", { class: "info-value" }, Array.isArray(value) ? value.join(", ") : value));
}

function renderTitle(i) {
  const t = day.titles[i];
  const s = state.titles[i];
  const r = t.revealed || {};
  const done = isDone(i);

  const header = h("div", { class: "card-head" },
    h("div", {}, h("div", { class: "muted small" }, `Title ${i + 1} of ${day.titles.length}`),
      h("h2", { class: "card-title" }, done ? displayTitle(t) : "Which series is this?")),
    h("div", { class: "worth" }, h("span", { class: "worth-num" }, fmt(done ? s.points : pointsLeft(i))),
      h("span", { class: "muted small" }, done ? "points earned" : "points available")),
  );

  const info = h("div", { class: "info-grid" },
    infoItem("Origin", ORIGIN_LABEL[r.origin] || r.origin),
    infoItem("Format", r.format === "oneshot" ? "Oneshot" : "Series"),
    infoItem("Released", r.release),
    infoItem("Popularity", r.popularity ? `${fmt(r.popularity)} AniList users` : null),
    infoItem("Score", r.average_score ? `${r.average_score}%` : null),
    infoItem("Demographic", r.demographic),
    infoItem("Genres", r.genres),
  );

  const hintButtons = h("div", { class: "hint-buttons" },
    HINTS.map((hd) => {
      const info2 = hintInfo(t, hd.key);
      const revealed = s.revealed.includes(hd.key);
      if (revealed) return null;
      return h("button", {
        class: "hint-btn",
        disabled: !info2.available || done,
        onclick: () => revealHint(i, hd.key),
        title: info2.available ? `Costs ${info2.cost} points` : "Not available for this title",
      }, h("span", {}, `${hd.emoji} ${hd.label}`), h("span", { class: "cost" }, info2.available ? `-${info2.cost}` : "n/a"));
    }),
  );

  // Once answered, the cover moves into the answer box, so skip the hint copy.
  const order = HINTS.map((x) => x.key).filter((k) => s.revealed.includes(k) && !(done && k === "cover"));
  const hintPanels = h("div", { class: "hint-panels" }, order.map((key) => renderHint(i, key)));

  return h("article", { class: "card" }, header, info,
    hintPanels,
    done ? null : h("div", { class: "hint-area" }, h("h3", { class: "section-label" }, "Hints"), hintButtons),
    done ? renderAnswer(i) : renderGuessForm(i),
  );
}

function displayTitle(t) {
  return t.answer.title || t.answer.romaji;
}

function renderHint(i, key) {
  const t = day.titles[i];
  const s = state.titles[i];
  const { data } = hintInfo(t, key);
  const def = HINTS.find((x) => x.key === key);
  const box = (...kids) => h("section", { class: `hint hint-${key.replace(/\d/, "")}` }, h("h4", {}, `${def.emoji} ${def.label}`), ...kids);

  if (key === "cover") {
    return box(h("div", { class: "cover-wrap" }, h("img", { src: data.url, alt: "Blurred cover", class: isDone(i) ? "cover" : "cover blurred", loading: "lazy" })));
  }
  if (key === "summary") return box(h("p", { class: "prewrap" }, data.text));
  if (key === "tags") {
    return box(h("div", { class: "chips" }, data.items.map((tag, n) => {
      if (tag.spoiler && !s.spoilers.includes(n) && !isDone(i)) {
        return h("button", { class: "chip spoiler", onclick: () => { s.spoilers.push(n); saveState(); render(); } }, "Spoiler, click to show");
      }
      return h("span", { class: `chip ${tag.spoiler ? "chip-spoiler" : ""}`, title: `${tag.rank}% relevance` }, `${tag.name} `, h("span", { class: "rank" }, `${tag.rank}%`));
    })));
  }
  if (key === "creators") {
    return box(h("ul", { class: "plain" }, data.items.map((c) => h("li", {}, h("b", {}, c.name), c.native ? ` (${c.native})` : "", h("span", { class: "muted" }, ` · ${c.role}`)))));
  }
  if (key === "characters") {
    const group = (label, list) => list && list.length ? h("div", { class: "char-group" }, h("div", { class: "muted small" }, label), h("div", { class: "chips" }, list.map((c) => h("span", { class: "chip" }, c.name)))) : null;
    return box(group("Main", data.main), group("Supporting", data.supporting));
  }
  if (key.startsWith("review")) {
    return box(
      data.summary ? h("p", { class: "review-summary" }, `“${data.summary}”`) : null,
      data.excerpt ? h("p", { class: "prewrap muted" }, data.excerpt) : null,
      data.score ? h("div", { class: "small muted" }, `Reviewer score: ${data.score}/100`) : null,
    );
  }
  return box();
}

const ORIGIN_SHORT = { manga: "Manga", manhwa: "Manhwa", manhua: "Manhua" };

// Guess box: type part of a title, pick it from the dropdown, then press Guess.
// Only titles in the list can be guessed, so there's no typo matching to worry about.
function renderGuessForm(i) {
  const s = state.titles[i];
  const used = (s.guesses || []).length;
  const left = MAX_GUESSES - used;
  let selected = null;       // { id, name }
  let results = [];
  let active = -1;           // highlighted row for keyboard use

  const listId = `guess-list-${i}`;
  const input = h("input", {
    type: "text", placeholder: "Search for a title", autocomplete: "off", spellcheck: "false",
    "aria-label": "Search for a title", role: "combobox", "aria-autocomplete": "list",
    "aria-controls": listId, "aria-expanded": "false",
  });
  const list = h("ul", { class: "picker-list", id: listId, role: "listbox", hidden: true });
  const guessBtn = h("button", { type: "submit", class: "btn primary", disabled: true }, "Guess");
  const note = h("span", { class: "muted", role: "status" },
    `${left} ${left === 1 ? "guess" : "guesses"} left. Wrong guesses don't cost points.`);

  const choose = (k) => {
    const hit = results[k];
    if (!hit) return;
    selected = { id: hit.entry.i, name: hit.entry.n };
    input.value = hit.entry.n;
    guessBtn.disabled = false;
    closeList();
  };
  const closeList = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const highlight = (k) => {
    active = k;
    [...list.children].forEach((li, n) => li.classList.toggle("active", n === k));
    const li = list.children[k];
    if (li) { input.setAttribute("aria-activedescendant", li.id); li.scrollIntoView({ block: "nearest" }); }
  };
  const showResults = () => {
    const q = input.value;
    results = search(titleIndex, q, MAX_RESULTS);
    active = -1;
    if (q.trim().length < MIN_QUERY_LENGTH) { closeList(); return; }
    list.replaceChildren(...(results.length ? results.map((hit, k) => {
      const e = hit.entry;
      // highlight what the typed words matched, in the name and in the line under it
      const marked = (text) => highlightParts(text, q).map((part) => part.hit ? h("mark", {}, part.text) : part.text);
      const subParts = [];
      if (e.r && e.r !== e.n) subParts.push(marked(e.r));
      if (hit.matched && hit.matched !== e.n && hit.matched !== e.r) subParts.push(["aka ", ...marked(hit.matched)]);
      const sub = subParts.flatMap((part, n) => (n ? [" · ", ...part] : part));
      const meta = [ORIGIN_SHORT[e.o], e.y].filter(Boolean).join(", ");
      return h("li", {
        id: `${listId}-${k}`, role: "option", class: "picker-item",
        onmousedown: (ev) => { ev.preventDefault(); choose(k); },
      },
      h("div", { class: "picker-name" }, ...marked(e.n), meta ? h("span", { class: "picker-meta" }, ` ${meta}`) : null),
      sub.length ? h("div", { class: "picker-sub" }, ...sub) : null);
    }) : [h("li", { class: "picker-empty" }, "No matching titles")]));
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  };

  input.addEventListener("input", () => { selected = null; guessBtn.disabled = true; showResults(); });
  input.addEventListener("focus", () => { if (!selected && input.value.trim()) showResults(); });
  // close on blur, unless focus came straight back (e.g. typing again right after pressing Guess)
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) closeList(); }, 100));
  input.addEventListener("keydown", (e) => {
    if (list.hidden || !results.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); highlight(Math.min(results.length - 1, active + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(Math.max(0, active - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(active >= 0 ? active : 0); }
    else if (e.key === "Escape") { closeList(); }
  });

  const form = h("form", {
    class: "guess-form",
    onsubmit: (e) => {
      e.preventDefault();
      if (!selected) { note.textContent = "Pick a title from the list first."; input.focus(); return; }
      const msg = submitGuess(i, selected);
      if (msg) { note.textContent = msg; input.select(); }
    },
  },
  used ? h("div", { class: "past-guesses" }, (s.guesses || []).map((g) => h("span", { class: "past-guess" }, `✗ ${g}`))) : null,
  h("div", { class: "picker" }, h("div", { class: "row" }, input, guessBtn), list),
  h("div", { class: "row spread small" },
    note,
    h("button", { type: "button", class: "link-btn", onclick: () => submitGuess(i, null, true) }, "Give up")),
  );
  setTimeout(() => input.focus({ preventScroll: true }), 0);
  return form;
}

function renderAnswer(i) {
  const t = day.titles[i];
  const s = state.titles[i];
  const a = t.answer;
  const next = nextUnfinished();
  return h("div", { class: `answer ${s.correct ? "right" : "wrong"}` },
    h("div", { class: "answer-banner" },
      s.correct ? `Correct${(s.guesses || []).length > 1 ? ` on guess ${s.guesses.length}` : ""}! +${fmt(s.points)}`
        : (s.guesses || []).length >= MAX_GUESSES ? `Out of guesses. Last guess: “${s.guess}”.`
        : s.guess ? `You gave up. Last guess: “${s.guess}”.` : "You gave up on this one."),
    h("div", { class: "answer-body" },
      a.cover ? h("img", { class: "answer-cover", src: a.cover, alt: `Cover of ${displayTitle(t)}` }) : null,
      h("div", {},
        h("div", { class: "answer-title" }, displayTitle(t)),
        a.romaji && a.romaji !== displayTitle(t) ? h("div", { class: "muted" }, a.romaji) : null,
        a.native ? h("div", { class: "muted" }, a.native) : null,
        a.url ? h("a", { class: "btn small-btn", href: a.url, target: "_blank", rel: "noopener" }, "View on AniList") : null,
      ),
    ),
    h("button", {
      class: "btn primary next-btn",
      onclick: () => { current = next === null ? "results" : next; render(); window.scrollTo({ top: 0, behavior: "smooth" }); },
    }, next === null ? (allDone() ? "See results" : "Back") : `Next title (${next + 1})`),
  );
}

function nextUnfinished() {
  const k = firstOpen();
  return k < day.titles.length ? k : null;
}

// ----------------------------------------------------------------- results --
function shareText() {
  const total = state.titles.reduce((a, s) => a + (s.points || 0), 0);
  const lines = [`mangadle #${day.number}  🏆 ${fmt(total)} pts`];
  state.titles.forEach((s, i) => {
    const used = HINTS.filter((hd) => s.revealed.includes(hd.key)).map((hd) => hd.emoji).join("");
    const label = NUMBER_EMOJI[i] || `${i + 1}.`;
    const mark = "✅";
    const tries = (s.guesses || []).length;
    const tryNote = s.correct && tries > 1 ? ` (${tries}/${MAX_GUESSES})` : "";
    lines.push(s.correct ? `${label} ${mark} ${fmt(s.points)}${tryNote}${used ? "  " + used : ""}` : `${label} ❌`);
  });
  lines.push(CONFIG.siteUrl || location.origin + location.pathname);
  return lines.join("\n");
}

function renderResults() {
  const total = state.titles.reduce((a, s) => a + (s.points || 0), 0);
  const text = shareText();
  const copyBtn = h("button", {
    class: "btn primary",
    onclick: async () => {
      try { await navigator.clipboard.writeText(text); copyBtn.textContent = "Copied!"; }
      catch { copyBtn.textContent = "Copy failed, select the text"; }
      setTimeout(() => (copyBtn.textContent = "Copy results"), 2000);
    },
  }, "Copy results");

  const statsBox = h("div", { class: "stats", id: "stats" }, h("p", { class: "muted" }, WORKER_URL ? "Loading everyone's results..." : "Shared stats aren't set up yet."));
  if (WORKER_URL) loadStats(statsBox, total);

  return h("article", { class: "card" },
    h("div", { class: "card-head" },
      h("div", {}, h("div", { class: "muted small" }, `mangadle #${day.number}`), h("h2", { class: "card-title" }, "Today's results")),
      h("div", { class: "worth" }, h("span", { class: "worth-num" }, fmt(total)), h("span", { class: "muted small" }, `of ${fmt(day.titles.length * START_POINTS)}`))),
    statsBox,
    // Share text last: it's long, and the stats are what you want to see first.
    h("h3", { class: "section-label share-label" }, "Share"),
    h("pre", { class: "share" }, text),
    h("div", { class: "row" }, copyBtn, h("span", { class: "muted small", id: "countdown" }, countdownText())),
  );
}

async function submitResult() {
  if (!WORKER_URL || state.submitted) return;
  try {
    const res = await fetch(`${WORKER_URL}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        date, playerId: playerId(),
        scores: state.titles.map((s) => s.points || 0),
        correct: state.titles.map((s) => !!s.correct),
      }),
    });
    if (res.ok) { state.submitted = true; saveState(); }
  } catch { /* stats are optional; try again on next load */ }
}

async function loadStats(box, myTotal) {
  try {
    if (!state.submitted) await submitResult();
    const res = await fetch(`${WORKER_URL}/stats?date=${date}`);
    if (!res.ok) throw new Error(res.status);
    const st = await res.json();
    box.replaceChildren(
      h("h3", { class: "section-label" }, `Everyone today (${fmt(st.players)} ${st.players === 1 ? "player" : "players"}, average ${fmt(st.averageTotal)})`),
      histogram(st, myTotal),
      h("table", { class: "stat-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Title"), h("th", {}, "Got it"), h("th", {}, "Avg pts"), h("th", {}, "You"))),
        h("tbody", {}, day.titles.map((t, i) => {
          const ts = st.titles[i] || { correctPct: 0, avgPoints: 0 };
          const s = state.titles[i];
          return h("tr", {},
            h("td", {}, i + 1),
            h("td", {}, t.answer.url ? h("a", { href: t.answer.url, target: "_blank", rel: "noopener" }, displayTitle(t)) : displayTitle(t)),
            h("td", {}, h("div", { class: "bar" }, h("span", { style: `width:${ts.correctPct}%` })), `${ts.correctPct}%`),
            h("td", {}, fmt(ts.avgPoints)),
            h("td", { class: s.correct ? "right-text" : "wrong-text" }, s.correct ? fmt(s.points) : "0"));
        }))),
    );
  } catch {
    box.replaceChildren(h("p", { class: "muted" }, "Couldn't load shared stats right now."));
  }
}

function histogram(st, myTotal) {
  const bins = st.histogram || [];
  const W = 640, H = 170, padL = 28, padB = 26, padT = 10;
  const max = Math.max(1, ...bins);
  const bw = (W - padL) / bins.length;
  const myBin = Math.min(bins.length - 1, Math.floor(myTotal / st.binSize));
  const svgNS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "histogram", role: "img", "aria-label": "Distribution of today's total scores" });
  bins.forEach((count, k) => {
    const bh = ((H - padB - padT) * count) / max;
    const rect = el("rect", { x: padL + k * bw + 1, y: H - padB - bh, width: Math.max(1, bw - 2), height: bh, rx: 2, class: k === myBin ? "bar-me" : "bar-all" });
    rect.append(el("title", {}, `${fmt(k * st.binSize)} to ${fmt((k + 1) * st.binSize - 1)}: ${count}`));
    svg.append(rect);
  });
  const maxTotal = (bins.length - 1) * st.binSize;
  [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
    const v = Math.round(maxTotal * f);
    svg.append(el("text", { x: padL + (v / st.binSize) * bw + bw / 2, y: H - 8, "text-anchor": "middle", class: "axis" }, v >= 1000 ? `${v / 1000}k` : String(v)));
  });
  svg.append(el("text", { x: 4, y: padT + 10, class: "axis" }, String(max)));
  svg.append(el("line", { x1: padL, x2: W, y1: H - padB, y2: H - padB, class: "axis-line" }));
  return h("div", { class: "hist-wrap" }, svg, h("div", { class: "muted small legend" }, h("span", { class: "swatch me" }), " your score  ", h("span", { class: "swatch all" }), " everyone"));
}

function countdownText() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: CONFIG.timezone, hour: "numeric", minute: "numeric", hourCycle: "h23" })
    .formatToParts(new Date()).map((p) => [p.type, p.value]));
  const mins = 24 * 60 - (Number(parts.hour) * 60 + Number(parts.minute));
  return `Next puzzle in ${Math.floor(mins / 60)}h ${mins % 60}m`;
}

// -------------------------------------------------------------- suggestions --
function setupSuggestions() {
  const form = $("#suggest-form");
  const status = $("#suggest-status");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = $("#suggest-input").value.trim();
    const m = raw.match(/anilist\.co\/manga\/(\d+)/) || raw.match(/^(\d+)$/);
    if (!m) { status.textContent = "Paste an AniList manga link (anilist.co/manga/...) or its number."; return; }
    if (!WORKER_URL) { status.textContent = "Suggestions aren't set up yet."; return; }
    const id = Number(m[1]);
    status.textContent = "Checking AniList...";
    try {
      const q = `query($id:Int){Media(id:$id,type:MANGA){id isAdult format title{english romaji}}}`;
      const res = await fetch("https://graphql.anilist.co", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ query: q, variables: { id } }) });
      const media = (await res.json()).data?.Media;
      if (!media) { status.textContent = "Couldn't find that manga on AniList."; return; }
      if (media.isAdult) { status.textContent = "Adult titles aren't included in mangadle."; return; }
      if (media.format === "NOVEL") { status.textContent = "Light novels aren't included, only manga, manhwa, and manhua."; return; }
      const name = media.title.english || media.title.romaji;
      const sres = await fetch(`${WORKER_URL}/suggest`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ anilistId: id }) });
      const out = await sres.json();
      status.textContent = out.queued ? `Thanks! “${name}” is in the queue. Suggestions show up a week or more after they're sent.` : `“${name}” is already in the queue.`;
      $("#suggest-input").value = "";
    } catch {
      status.textContent = "Something went wrong. Try again later.";
    }
  });
}

// --------------------------------------------------------------------- boot --
async function boot() {
  $("#help-btn").addEventListener("click", () => $("#help").showModal());
  if (!localStorage.getItem("mangadle:seen-help")) {
    localStorage.setItem("mangadle:seen-help", "1");
    $("#help").showModal();
  }
  setupSuggestions();

  // Future dates are locked on the live site; local testing (localhost) can open any date.
  const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
  if (date > todayIn(CONFIG.timezone) && !params.has("preview") && !isLocal) {
    $("#app").replaceChildren(h("div", { class: "card empty" }, h("h2", {}, "Not out yet"),
      h("p", { class: "muted" }, `The puzzle for ${date} isn't out yet. `, h("a", { href: "./" }, "Play today's puzzle"), ".")));
    return;
  }
  try {
    const res = await fetch(`days/${date}.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    day = await res.json();
  } catch {
    $("#app").replaceChildren(h("div", { class: "card empty" }, h("h2", {}, "No puzzle yet"), h("p", { class: "muted" }, `There's no puzzle for ${date}. `, h("a", { href: "archive.html" }, "See past days"), ".")));
    return;
  }
  titleIndex = buildIndex(await loadTitleList(), day);
  state = loadState();
  saveState();  // persist the fingerprint right away, so a later rebuild is always detected
  const first = state.titles.findIndex((s) => s.correct === null);
  current = first === -1 ? "results" : first;
  if (allDone() && !state.submitted) submitResult();
  render();
}

boot();
