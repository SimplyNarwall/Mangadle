// mangadle API (Cloudflare Worker + D1)
//
//   POST /result            { date, playerId, scores: [int], correct: [bool] }   one result per player per day (resubmits replace)
//   GET  /stats?date=...    per-title % correct and average points, plus a histogram of day totals
//   POST /suggest           { anilistId, note? }                                 queue a title suggestion
//   GET  /suggestions       (Authorization: Bearer ADMIN_TOKEN) unused suggestions, oldest first
//   POST /suggestions/used  (Authorization: Bearer ADMIN_TOKEN) { ids: [int] }   mark suggestions as used

const MAX_TITLES = 20;
const POINTS_PER_TITLE = 1000;
const BIN_SIZE = 500;

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    const url = new URL(request.url);
    try {
      let res;
      if (url.pathname === "/result" && request.method === "POST") res = await postResult(request, env);
      else if (url.pathname === "/stats" && request.method === "GET") res = await getStats(url, env);
      else if (url.pathname === "/suggest" && request.method === "POST") res = await postSuggest(request, env);
      else if (url.pathname === "/suggestions" && request.method === "GET") res = await getSuggestions(request, env);
      else if (url.pathname === "/suggestions/used" && request.method === "POST") res = await markUsed(request, env);
      else res = json({ error: "not found" }, 404);
      for (const [k, v] of Object.entries(cors)) res.headers.set(k, v);
      return res;
    } catch (err) {
      return json({ error: err.message || "server error" }, err.status || 500, cors);
    }
  },
};

function corsHeaders(request, env) {
  const allowed = (env.ALLOWED_ORIGIN || "*").split(",").map((s) => s.trim());
  const origin = request.headers.get("Origin") || "";
  const allow = allowed.includes("*") ? "*" : allowed.includes(origin) ? origin : allowed[0];
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Vary": "Origin",
  };
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function bad(message) {
  const e = new Error(message);
  e.status = 400;
  return e;
}

async function readJson(request) {
  try {
    return await request.json();
  } catch {
    throw bad("invalid JSON");
  }
}

function checkDate(date) {
  if (typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw bad("date must be YYYY-MM-DD");
  return date;
}

function requireAdmin(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (!env.ADMIN_TOKEN || auth !== `Bearer ${env.ADMIN_TOKEN}`) {
    const e = new Error("unauthorized");
    e.status = 401;
    throw e;
  }
}

async function postResult(request, env) {
  const body = await readJson(request);
  const date = checkDate(body.date);
  const playerId = String(body.playerId || "");
  if (!/^[A-Za-z0-9-]{8,64}$/.test(playerId)) throw bad("bad playerId");
  const { scores, correct } = body;
  if (!Array.isArray(scores) || !Array.isArray(correct) || scores.length !== correct.length ||
      scores.length < 1 || scores.length > MAX_TITLES) throw bad("scores/correct must be same-length arrays");
  scores.forEach((s, i) => {
    if (!Number.isInteger(s) || s < 0 || s > POINTS_PER_TITLE) throw bad("each score must be 0 to 1000");
    if (typeof correct[i] !== "boolean") throw bad("correct must be booleans");
    if (!correct[i] && s !== 0) throw bad("a wrong answer scores 0");
  });
  const total = scores.reduce((a, b) => a + b, 0);

  // One row per player per day; a resubmission (e.g. after claiming "close enough") replaces it.
  const result = await env.DB.prepare(
    `INSERT INTO results (date, player_id, total, scores, correct, created_at) VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT (date, player_id) DO UPDATE SET total = excluded.total, scores = excluded.scores, correct = excluded.correct`
  ).bind(date, playerId, total, JSON.stringify(scores), JSON.stringify(correct), new Date().toISOString()).run();

  const recorded = (result.meta && result.meta.changes) > 0;
  return json({ ok: true, recorded });
}

async function getStats(url, env) {
  const date = checkDate(url.searchParams.get("date"));
  const { results } = await env.DB.prepare(
    "SELECT total, scores, correct FROM results WHERE date = ?"
  ).bind(date).all();

  const players = results.length;
  const titleCount = players ? Math.max(...results.map((r) => JSON.parse(r.scores).length)) : 0;
  const titles = Array.from({ length: titleCount }, () => ({ correct: 0, points: 0, plays: 0 }));
  const maxTotal = titleCount * POINTS_PER_TITLE;
  const bins = new Array(Math.floor(maxTotal / BIN_SIZE) + 1).fill(0);

  for (const r of results) {
    const scores = JSON.parse(r.scores);
    const correct = JSON.parse(r.correct);
    scores.forEach((s, i) => {
      titles[i].plays += 1;
      titles[i].points += s;
      if (correct[i]) titles[i].correct += 1;
    });
    bins[Math.min(bins.length - 1, Math.floor(r.total / BIN_SIZE))] += 1;
  }

  return json({
    date,
    players,
    binSize: BIN_SIZE,
    histogram: bins,
    averageTotal: players ? Math.round(results.reduce((a, r) => a + r.total, 0) / players) : 0,
    titles: titles.map((t) => ({
      correctPct: t.plays ? Math.round((100 * t.correct) / t.plays) : 0,
      avgPoints: t.plays ? Math.round(t.points / t.plays) : 0,
    })),
  });
}

async function postSuggest(request, env) {
  const body = await readJson(request);
  const id = Number(body.anilistId);
  if (!Number.isInteger(id) || id < 1 || id > 10_000_000) throw bad("anilistId must be a positive integer");
  const note = String(body.note || "").slice(0, 200);

  const existing = await env.DB.prepare(
    "SELECT id FROM suggestions WHERE anilist_id = ? AND used = 0"
  ).bind(id).first();
  if (existing) return json({ ok: true, queued: false, reason: "already suggested" });

  await env.DB.prepare(
    "INSERT INTO suggestions (anilist_id, note, created_at, used) VALUES (?, ?, ?, 0)"
  ).bind(id, note, new Date().toISOString()).run();
  return json({ ok: true, queued: true });
}

async function getSuggestions(request, env) {
  requireAdmin(request, env);
  const { results } = await env.DB.prepare(
    "SELECT id, anilist_id, note, created_at FROM suggestions WHERE used = 0 ORDER BY id"
  ).all();
  return json({ suggestions: results });
}

async function markUsed(request, env) {
  requireAdmin(request, env);
  const body = await readJson(request);
  const ids = (body.ids || []).filter((x) => Number.isInteger(x));
  for (const id of ids) {
    await env.DB.prepare("UPDATE suggestions SET used = 1 WHERE id = ?").bind(id).run();
  }
  return json({ ok: true, marked: ids.length });
}
