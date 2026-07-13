// Audio level scale handling for the simulator's fallback AudioPanel (the panel
// shown when a device declares no explicit controls, so no min/max metadata is
// available). A device's `level` state can be a 0..1 fraction (e.g. QSC gain), a
// dB value (e.g. -100..12 on a Biamp), or a 0..100 percent — and the scale can't
// be read from a single value alone: 0 dB (nominal/unity) sits in [0,1] and was
// misread as a silent 0% fraction. A reported dB reading (`level_db`) or a
// negative value disambiguates the dB case; one classifier drives both the meter
// and the slider write-back so they can never disagree on scale.

// Representative dB fader range for the meter when no min/max metadata exists.
export const DB_MIN = -100;
export const DB_MAX = 12;

export type AudioScale = "fraction" | "db" | "percent";

/**
 * Classify a `level` value's scale. A dB reading (hasDb) or a negative value is
 * dB; otherwise [0,1] is a 0..1 fraction and >1 is a 0..100 percent.
 */
export function audioLevelScale(level: number, hasDb: boolean): AudioScale {
  if (hasDb || level < 0) return "db";
  if (level <= 1) return "fraction";
  return "percent";
}

const clamp = (n: number): number => Math.max(0, Math.min(100, n));

/** Map a device `level` to a 0..100 meter percentage for its detected scale. */
export function normalizeAudioLevel(level: number, hasDb: boolean): number {
  const scale = audioLevelScale(level, hasDb);
  if (scale === "fraction") return level * 100;
  if (scale === "db") return clamp(((level - DB_MIN) / (DB_MAX - DB_MIN)) * 100);
  return clamp(level); // percent
}

/** Convert a 0..100 slider percentage back to the device's `level` scale, using
 *  the same classification as normalizeAudioLevel so the write-back matches. */
export function denormalizeAudioLevel(percent: number, level: number, hasDb: boolean): number {
  const scale = audioLevelScale(level, hasDb);
  if (scale === "fraction") return percent / 100;
  if (scale === "db") return DB_MIN + (percent / 100) * (DB_MAX - DB_MIN);
  return percent; // percent
}
