// Title search for the guess dropdown.
//
// The list is every "possible" title (titles.json) plus the day's own choices (its
// not-possible and random titles). Each entry:
//   { i: anilistId, n: display name, r?: romaji, a?: [other names], p: popularity, y?: year, o?: origin }

import { normalize } from "./match.js";

export const MAX_RESULTS = 50;
export const MIN_QUERY_LENGTH = 2;

let titleListPromise = null;

export function loadTitleList() {
  if (!titleListPromise) {
    titleListPromise = fetch("titles.json", { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : []))
      .catch(() => []);
  }
  return titleListPromise;
}

// Builds the searchable list for a day: the shared list + the day's choices.
// Days built before choices existed fall back to their own titles that aren't in the list.
export function buildIndex(titleList, day) {
  const byId = new Map();
  for (const e of titleList) byId.set(e.i, e);
  const extras = day.choices || day.titles
    .filter((t) => !byId.has(t.anilist_id))
    .map((t) => ({
      i: t.anilist_id,
      n: t.answer.title || t.answer.romaji,
      r: t.answer.romaji !== (t.answer.title || t.answer.romaji) ? t.answer.romaji : undefined,
      a: t.answer.accepted_aliases || [],
      p: (t.revealed && t.revealed.popularity) || 0,
      o: t.revealed && t.revealed.origin,
    }));
  for (const e of extras) if (!byId.has(e.i)) byId.set(e.i, e);
  return [...byId.values()].map((e) => {
    const main = [e.n, e.r].filter(Boolean);
    const names = [...main, ...(e.a || [])];
    return { ...e, names, mainCount: main.length, norms: names.map((n) => normalize(n)) };
  });
}

// Score how well a normalized query matches one normalized name (0 = no match).
//   100  exact name
//    90  the name's first words match the typed words in order ("sol lev" -> "Solo Leveling")
//    85  the name starts with the typed text
//    70  every typed word starts some word of the name, in order
//    60  ... in any order ("titan attack" -> "Attack on Titan")
//    50  the typed text appears anywhere
function scoreName(q, qWords, name) {
  if (!name) return 0;
  if (name === q) return 100;
  const words = name.split(" ");
  if (qWords.length <= words.length && qWords.every((w, k) => words[k].startsWith(w))) return 90;
  if (name.startsWith(q)) return 85;
  // every query word starts some word of the name (in any order): "titan attack" finds "Attack on Titan"
  if (qWords.every((w) => words.some((nw) => nw.startsWith(w)))) {
    const inOrder = qWords.every((w, k) => k === 0 || name.indexOf(w) > name.indexOf(qWords[k - 1]));
    return inOrder ? 70 : 60;
  }
  if (name.includes(q)) return 50;
  return 0;
}

// Returns up to MAX_RESULTS { entry, matched } sorted by match quality, then popularity.
export function search(index, query, limit = MAX_RESULTS) {
  const q = normalize(query);
  if (q.replace(/ /g, "").length < MIN_QUERY_LENGTH) return [];
  const qWords = q.split(" ");
  const hits = [];
  for (const e of index) {
    let best = 0;
    let matched = null;
    let onMain = false;   // matched the shown name or romaji, not an alternate name
    e.norms.forEach((nm, k) => {
      const sc = scoreName(q, qWords, nm);
      const isMain = k < e.mainCount;
      if (sc > best || (sc === best && sc && isMain && !onMain)) { best = sc; matched = e.names[k]; onMain = isMain; }
    });
    if (best) hits.push({ entry: e, matched, score: best, onMain });
  }
  // best match first; then matches on the main name over alternate names; then more popular
  hits.sort((a, b) => b.score - a.score || (b.onMain - a.onMain) || (b.entry.p || 0) - (a.entry.p || 0));
  return hits.slice(0, limit);
}

// ------------------------------------------------------------------ highlight --
// Splits text into [{ text, hit }] pieces marking the parts the typed words matched, so the
// dropdown can show why a title came up. Works on the original text (accents, symbols and
// case kept); each typed word marks one word of the text, preferring the start of a word.

const foldChar = (c) => c.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

export function highlightParts(text, query) {
  const qWords = normalize(query).split(" ").filter(Boolean);
  if (!text || !qWords.length) return [{ text: text || "", hit: false }];

  // words of the original text with their folded form and a map back to original offsets
  const tokens = [];
  for (const m of text.matchAll(/[\p{L}\p{N}]+/gu)) {
    let folded = "";
    const ends = [];  // ends[j] = original offset just after the char that produced folded[j]
    [...m[0]].forEach((ch, k, arr) => {
      const f = foldChar(ch);
      const end = m.index + arr.slice(0, k + 1).join("").length;
      for (let n = 0; n < f.length; n++) { folded += f[n]; ends.push(end); }
    });
    tokens.push({ start: m.index, folded, ends });
  }

  const marks = [];
  const used = new Set();
  let after = -1;  // prefer matching the typed words in order
  for (const w of qWords) {
    const candidates = [
      (t, k) => k > after && t.folded.startsWith(w),
      (t) => t.folded.startsWith(w),
      (t) => t.folded.includes(w),
    ];
    let found = null;
    for (const test of candidates) {
      const k = tokens.findIndex((t, idx) => !used.has(idx) && test(t, idx));
      if (k !== -1) { found = k; break; }
    }
    if (found === null) continue;
    const t = tokens[found];
    const at = t.folded.indexOf(w);
    const from = at === 0 ? t.start : t.ends[at - 1];
    const to = t.ends[at + w.length - 1];
    marks.push([from, to]);
    used.add(found);
    after = Math.max(after, found);
  }

  marks.sort((a, b) => a[0] - b[0]);
  const parts = [];
  let pos = 0;
  for (const [from, to] of marks) {
    if (from < pos) continue;
    if (from > pos) parts.push({ text: text.slice(pos, from), hit: false });
    parts.push({ text: text.slice(from, to), hit: true });
    pos = to;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), hit: false });
  return parts;
}
