// The version rule: what a version string reads as, and which of two is newer.
//
// The IDE's copy of the server's `openavc/utils/version_rule.py`. Both run the
// corpus in `tests/fixtures/version_rule_cases.json` (`versionRule.test.ts`
// here, `tests/test_version_rule.py` there), so a release candidate reads the
// same in the browser as on the server: `0.40.0-rc2` and `0.40.0rc2` are one
// version, older than `0.40.0`; `0.41.0.dev1` is between 0.40.0 and 0.41.0;
// `0.22` is `0.22.0`; `rc10` follows `rc9`. Anything else (a `+build` suffix,
// a post-release, any other pre-release word) is not a version and reads as
// null; each caller decides what that means for it.

export interface Version {
  release: number[];
  /** A pre-release's kind and number: `["rc", 2]`. */
  pre: ["a" | "b" | "rc", number] | null;
  /** A development build's number. */
  dev: number | null;
}

// Each number is at most nine digits, as on the server, where a longer run
// would not fit an integer exactly.
const VERSION_RE = new RegExp(
  "^v?" +
    "([0-9]{1,9}(?:\\.[0-9]{1,9})*)" +
    "(?:" +
    "[-_.]?(alpha|a|beta|b|preview|pre|rc|c)[-_.]?([0-9]{1,9})?" +
    "|" +
    "[-_.]?(dev)[-_.]?([0-9]{1,9})?" +
    ")?$",
  "i",
);

const PRE_KINDS: Record<string, "a" | "b" | "rc"> = {
  a: "a", alpha: "a",
  b: "b", beta: "b",
  rc: "rc", c: "rc", pre: "rc", preview: "rc",
};

const PRE_RANK = { a: 0, b: 1, rc: 2 } as const;

/** What `text` reads as, or null when it is not a version. */
export function readVersion(text: unknown): Version | null {
  if (typeof text !== "string") return null;
  const match = VERSION_RE.exec(text.trim());
  if (!match) return null;
  const release = match[1].split(".").map((part) => parseInt(part, 10));
  if (match[2]) {
    return {
      release,
      pre: [PRE_KINDS[match[2].toLowerCase()], match[3] ? parseInt(match[3], 10) : 0],
      dev: null,
    };
  }
  if (match[4]) {
    return { release, pre: null, dev: match[5] ? parseInt(match[5], 10) : 0 };
  }
  return { release, pre: null, dev: null };
}

/** Whether a version is a release candidate, beta, alpha or development build. */
export function isPrerelease(version: Version): boolean {
  return version.pre !== null || version.dev !== null;
}

/** Where a version stands within its release: a development build first,
 *  then alpha, beta and release candidates by number, then the release. */
function phase(version: Version): number[] {
  if (version.dev !== null) return [0, version.dev];
  if (version.pre !== null) return [1, PRE_RANK[version.pre[0]], version.pre[1]];
  return [2];
}

/** Two lists of numbers compared left to right, a missing number read as 0. */
function compareNumbers(a: number[], b: number[]): number {
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (x !== y) return x > y ? 1 : -1;
  }
  return 0;
}

/** -1, 0 or 1 as `a` is older than, the same as, or newer than `b`; null when
 *  either is not a version. */
export function compareVersions(a: unknown, b: unknown): -1 | 0 | 1 | null {
  const va = readVersion(a);
  const vb = readVersion(b);
  if (!va || !vb) return null;
  const order =
    compareNumbers(va.release, vb.release) || compareNumbers(phase(va), phase(vb));
  return order as -1 | 0 | 1;
}
