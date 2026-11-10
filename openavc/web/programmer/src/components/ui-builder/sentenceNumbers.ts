/**
 * How a review sentence prints a number, the way the Python side's f-strings do.
 *
 * The page review and its floors exist twice, here and under openavc/ui/, and
 * tests/test_ui_review_parity.py compares the two sentence for sentence, byte
 * for byte. So every number either side puts in a message is printed by Python's
 * rules: rounded half to even, a percentage without trailing zeros, an authored
 * bound without a `.0`.
 */

/**
 * Python's `format(value, '.Nf')`, which rounds ties to EVEN.
 *
 * `toFixed` rounds ties away from zero, so the two disagree on any box that
 * lands exactly on a half pixel -- reachable, because a height is a four-decimal
 * percentage of 800 and 0.0625% is exactly 0.5px. One digit of difference in one
 * message is a parity failure, and chasing it later from a red test would cost
 * far more than writing it correctly once.
 */
export function fixed(value: number, digits: number): string {
  const negative = value < 0 || Object.is(value, -0);
  const scale = 10 ** digits;
  const scaled = Math.abs(value) * scale;
  const floor = Math.floor(scaled);
  const remainder = scaled - floor;
  const rounded =
    remainder > 0.5 ? floor + 1
    : remainder < 0.5 ? floor
    : floor % 2 === 0 ? floor : floor + 1;
  const body = (rounded / scale).toFixed(digits);
  return negative ? `-${body}` : body;
}

/** A percentage the way it would be written back into a placement. */
export function pct(value: number): string {
  return fixed(value, 2).replace(/0+$/, "").replace(/\.$/, "") || "0";
}

/** An authored bound, without a trailing .0 on whole numbers.
 *
 *  The mirror of Python's `_num`, which has to strip one because a JSON `24`
 *  arrives there as a float. Numbers here are already doubles that print whole. */
export function num(value: number): string {
  return String(value);
}
