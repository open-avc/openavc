/**
 * The sentence a Code view toast gives what the driver check found in a
 * Python driver (the rules `python -m openavc.drivers.check` holds it to):
 * how many, then each on its own line.
 */
export function problemsToFix(issues: string[]): string {
  const count = issues.length;
  const heading = `The driver check found ${count} problem${count === 1 ? "" : "s"} to fix:`;
  return [heading, ...issues.map((issue) => `• ${issue}`)].join("\n");
}
