import CONFIG from "./config.js";

const fmt = (n) => Number(n || 0).toLocaleString("en-US");
const today = new Intl.DateTimeFormat("en-CA", { timeZone: CONFIG.timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

function progress(date) {
  try {
    const s = JSON.parse(localStorage.getItem(`mangadle:day:${date}`));
    if (!s || !s.titles) return null;
    const done = s.titles.filter((t) => t.correct !== null).length;
    const points = s.titles.reduce((a, t) => a + (t.points || 0), 0);
    const correct = s.titles.filter((t) => t.correct === true).length;
    return { done, total: s.titles.length, points, correct };
  } catch {
    return null;
  }
}

function prettyDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric", year: "numeric" });
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

async function main() {
  const list = document.getElementById("archive-list");
  let days = [];
  try {
    const res = await fetch("days/index.json", { cache: "no-cache" });
    if (res.ok) days = await res.json();
  } catch { /* handled below */ }
  days = days.filter((d) => d.date <= today).sort((a, b) => (a.date < b.date ? 1 : -1));

  if (!days.length) {
    list.replaceChildren(el("li", "muted", "No puzzles yet."));
    return;
  }

  let played = 0, finished = 0, totalPoints = 0;
  list.replaceChildren(...days.map((d) => {
    const p = progress(d.date);
    if (p && p.done) played += 1;
    if (p && p.done === p.total) { finished += 1; totalPoints += p.points; }

    const row = el("a", "archive-row");
    row.href = d.date === today ? "./" : `./?date=${d.date}`;
    row.append(el("span", "archive-num", `#${d.number}`));
    const mid = el("span", "archive-date", prettyDate(d.date));
    if (d.date === today) mid.append(el("span", "today-tag", "Today"));
    row.append(mid);
    let status;
    if (!p || !p.done) status = el("span", "archive-status muted", "Play");
    else if (p.done < p.total) status = el("span", "archive-status partial", `${p.done}/${p.total} done`);
    else status = el("span", "archive-status done", `🏆 ${fmt(p.points)} · ${p.correct}/${p.total}`);
    row.append(status);
    const li = el("li");
    li.append(row);
    return li;
  }));

  const summary = document.getElementById("archive-summary");
  const box = (label, value) => { const b = el("div"); b.append(el("b", null, value), el("span", "muted small", label)); return b; };
  summary.replaceChildren(
    box("puzzles", fmt(days.length)),
    box("finished", fmt(finished)),
    box("avg score", finished ? fmt(Math.round(totalPoints / finished)) : "n/a"),
  );
}

main();
