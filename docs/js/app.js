import { checkGuess } from "./match.js";
import CONFIG from "./config.js";

// ------------------------------------------------------------------ setup --
const START_POINTS = 1000;
const NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"];
const HINTS = [
  { key: "cover", label: "Cover", emoji: "🖼️", cost: 50 },
  { key: "summary", label: "Summary", emoji: "📖", cost: 100 },
  { key: "tags", label: "Tags", emoji: "🏷️", cost: 75 },
  { key: "creators", label: "Author & artist", emoji: "✏️", cost: 50 },
  { key: "characters", label: "Characters", emoji: "😀", cost: 75 },
  { key: "review0", label: "Review 1", emoji: "🗣️", cost: 50 },
  { key: "review1", label: "Review 2", emoji: "🗣️", cost: 50 },
  { key: "review2", label: "Review 3", emoji: "🗣️", cost: 50 },
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
    titles: Array.from({ length: n }, () => ({ revealed: [], spoilers: [], guess: null, correct: null, points: null })),
    submitted: false,
  };
}

function loadState() {
  try {
    const s = JSON.parse(localStorage.getItem(storageKey));
    if (s && s.titles && s.titles.length === day.titles.length) return s;
  } catch { /* fall through */ }
  return freshState(day.titles.length);
}

function saveState() {
  localStorage.setItem(storageKey, JSON.stringify(state));
}

const isDone = (i) => state.titles[i].correct !== null;
const allDone = () => state.titles.every((_, i) => isDone(i));

// ------------------------------------------------------------------ hints --
function hintInfo(title, key) {
  const hints = title.hints || {};
  if (key.startsWith("review")) {
    const r = (hints.reviews || [])[Number(key.slice(6))];
    return { available: !!r, cost: r ? r.cost ?? 50 : 50, data: r };
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
function submitGuess(i, guess, giveUp = false) {
  const s = state.titles[i];
  if (isDone(i)) return;
  const t = day.titles[i];
  const result = giveUp ? { correct: false } : checkGuess(guess, t.answer.accepted_aliases);
  s.guess = giveUp ? null : guess;
  s.correct = !!result.correct;
  s.points = result.correct ? pointsLeft(i) : 0;
  saveState();
  if (allDone()) submitResult();
  render();
}

// ---------------------------------------------------------------- rendering --
function render() {
  renderTopbar();
  renderNav();
  const app = $("#app");
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
      return h("button", {
        class: `nav-pill ${status} ${current === i ? "active" : ""}`,
        "aria-label": `Title ${i + 1}`,
        onclick: () => { current = i; render(); },
      }, s.correct === true ? "✓" : s.correct === false ? "✗" : String(i + 1));
    }),
    h("button", {
      class: `nav-pill results-pill ${current === "results" ? "active" : ""}`,
      disabled: !allDone(),
      title: allDone() ? "Results" : "Finish all titles to see results",
      onclick: () => { current = "results"; render(); },
    }, "Results"),
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

function renderGuessForm(i) {
  const input = h("input", { type: "text", placeholder: "Type the title (English or romaji)", autocomplete: "off", "aria-label": "Your guess" });
  const form = h("form", {
    class: "guess-form",
    onsubmit: (e) => {
      e.preventDefault();
      const g = input.value.trim();
      if (!g) { input.focus(); return; }
      submitGuess(i, g);
    },
  },
  h("div", { class: "row" }, input, h("button", { type: "submit", class: "btn primary" }, "Guess")),
  h("div", { class: "row spread small" },
    h("span", { class: "muted" }, "One guess only. Small typos are fine."),
    h("button", { type: "button", class: "link-btn", onclick: () => { if (confirm("Give up on this title? It will score 0.")) submitGuess(i, null, true); } }, "Give up")),
  );
  setTimeout(() => input.focus({ preventScroll: true }), 0);
  return form;
}

function renderAnswer(i) {
  const t = day.titles[i];
  const s = state.titles[i];
  const a = t.answer;
  const next = nextUnfinished(i);
  return h("div", { class: `answer ${s.correct ? "right" : "wrong"}` },
    h("div", { class: "answer-banner" }, s.correct ? `Correct! +${fmt(s.points)}` : s.guess ? `Not quite. You guessed “${s.guess}”.` : "You gave up on this one."),
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

function nextUnfinished(from) {
  const n = day.titles.length;
  for (let k = 1; k <= n; k++) {
    const j = (from + k) % n;
    if (!isDone(j)) return j;
  }
  return null;
}

// ----------------------------------------------------------------- results --
function shareText() {
  const total = state.titles.reduce((a, s) => a + (s.points || 0), 0);
  const lines = [`mangadle #${day.number}  🏆 ${fmt(total)} pts`];
  state.titles.forEach((s, i) => {
    const used = HINTS.filter((hd) => s.revealed.includes(hd.key)).map((hd) => hd.emoji).join("");
    const label = NUMBER_EMOJI[i] || `${i + 1}.`;
    lines.push(s.correct ? `${label} ✅ ${fmt(s.points)}${used ? "  " + used : ""}` : `${label} ❌`);
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
    h("pre", { class: "share" }, text),
    h("div", { class: "row" }, copyBtn, h("span", { class: "muted small", id: "countdown" }, countdownText())),
    statsBox,
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
      status.textContent = out.queued ? `Thanks! “${name}” is in the queue.` : `“${name}” is already in the queue.`;
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
  state = loadState();
  const first = state.titles.findIndex((s) => s.correct === null);
  current = first === -1 ? "results" : first;
  if (allDone() && !state.submitted) submitResult();
  render();
}

boot();
