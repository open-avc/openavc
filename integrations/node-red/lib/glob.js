"use strict";

// fnmatch-style globs, the same matcher the OpenAVC bus and state store use:
// `*` matches any run of characters (dots included), `?` matches one.
function globToRegExp(pattern) {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`^${escaped}$`);
}

// A predicate over a list of patterns. No patterns matches nothing.
function compile(patterns) {
  const regexps = patterns.map(globToRegExp);
  return (name) => regexps.some((re) => re.test(name));
}

// A pattern list as a node config field holds it: a comma-separated string
// (or already a list). Blanks dropped, duplicates folded, order kept.
function parsePatterns(raw) {
  const items = Array.isArray(raw) ? raw : String(raw || "").split(",");
  const seen = new Set();
  const out = [];
  for (const item of items) {
    const trimmed = String(item).trim();
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed);
      out.push(trimmed);
    }
  }
  return out;
}

module.exports = { globToRegExp, compile, parsePatterns };
