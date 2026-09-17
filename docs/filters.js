// Title filter. Mirrors collector/filters.py exactly; tests/filter_cases.json keeps them in sync.
// A keyword of 6+ characters matches anywhere in the title; a shorter one must start a word.

const LONG_KEYWORD = 6;

export function fold(text) {
  return (text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function keywordMatches(foldedTitle, keyword) {
  const kw = fold(keyword).trim();
  if (!kw) return false;
  if (kw.length >= LONG_KEYWORD) return foldedTitle.includes(kw);
  return new RegExp("(^|[^a-z0-9])" + escapeRegex(kw)).test(foldedTitle);
}

export function anyMatch(title, keywords) {
  const folded = fold(title);
  return (keywords || []).some((k) => keywordMatches(folded, k));
}

export function isSuitable(title, filters, extraInclude = []) {
  const include = [...(filters.include || []), ...extraInclude];
  return anyMatch(title, include) && !anyMatch(title, filters.exclude || []);
}

export function isAirOrOceanOnly(title, filters) {
  return anyMatch(title, filters.air_ocean || []) && !anyMatch(title, filters.land || []);
}
