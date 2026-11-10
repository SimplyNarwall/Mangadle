// Fuzzy title matching, per the spec:
//   - normalize: lowercase, strip accents, symbols -> spaces, collapse spaces
//   - edit distance counts insert / delete / substitute / adjacent swap
//   - an alias allows floor(length / 4) edits, so aliases under 4 chars need an exact match
//   - a close-enough guess is simply accepted (no "did you mean" prompt)

export function normalize(text) {
  return String(text || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")        // accents and macrons: ō -> o
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")       // every symbol becomes a space
    .replace(/\s+/g, " ")
    .trim();
}

// Optimal string alignment distance (Damerau-Levenshtein with adjacent swaps).
// Stops early once the distance is guaranteed to exceed maxDist.
export function editDistance(a, b, maxDist = Infinity) {
  if (Math.abs(a.length - b.length) > maxDist) return maxDist + 1;
  const rows = a.length + 1, cols = b.length + 1;
  let prev2 = null;
  let prev = Array.from({ length: cols }, (_, j) => j);
  for (let i = 1; i < rows; i++) {
    const cur = new Array(cols);
    cur[0] = i;
    let rowMin = cur[0];
    for (let j = 1; j < cols; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        v = Math.min(v, prev2[j - 2] + 1);
      }
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > maxDist) return maxDist + 1;
    prev2 = prev;
    prev = cur;
  }
  return prev[cols - 1];
}

export function allowedEdits(normalizedAlias) {
  return Math.floor(normalizedAlias.length / 4);
}

// Returns { correct, matched } where matched is the alias that accepted the guess.
export function checkGuess(guess, aliases) {
  const g = normalize(guess);
  if (!g) return { correct: false, matched: null };
  let best = null;
  for (const alias of aliases || []) {
    const a = normalize(alias);
    if (!a) continue;
    if (a === g) return { correct: true, matched: alias };
    const limit = allowedEdits(a);
    if (limit === 0) continue;
    const d = editDistance(g, a, limit);
    if (d <= limit && (!best || d < best.d)) best = { d, alias };
  }
  return best ? { correct: true, matched: best.alias } : { correct: false, matched: null };
}
