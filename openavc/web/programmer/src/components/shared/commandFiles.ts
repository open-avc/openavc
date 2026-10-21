/**
 * Commands that take a file or hand one back (a Python driver's `file` param,
 * `returns_file`). They run only from Send Command on the device page and from
 * a Device Audit, so every other command picker leaves them out.
 *
 * The checks here say "too large" or "the wrong kind of file" before anything
 * is uploaded, in the server's own words (`core/file_staging.py`,
 * `file_rules_refusal`); the server checks again when the upload is opened.
 */
import type { DriverParamDef } from "../../api/types";
import { paramLabel } from "./paramLabel";

/** The platform's limit for one file (`spec.FILE_PARAM_MAX_BYTES`). */
export const FILE_PARAM_MAX_BYTES = 1024 * 1024 * 1024;

interface CommandLike {
  params?: unknown;
  returns_file?: unknown;
}

/** The params of a command that take a file, in declared order. */
export function fileParamNames(command: unknown): string[] {
  const params = (command as CommandLike | null | undefined)?.params;
  if (!params || typeof params !== "object") return [];
  return Object.entries(params as Record<string, { type?: unknown } | null>)
    .filter(([, def]) => def?.type === "file")
    .map(([name]) => name);
}

/** Whether a command takes a file or hands one back. */
export function isFileCommand(command: unknown): boolean {
  return (
    fileParamNames(command).length > 0 ||
    (command as CommandLike | null | undefined)?.returns_file === true
  );
}

/** A byte count the way a person reads it: 940 bytes, 3.4 MB, 1.2 GB. The
 *  same wording as the server's (`command_files.describe_size`). */
export function describeSize(size: number): string {
  if (size < 1000) return `${size} byte${size === 1 ? "" : "s"}`;
  const units: [string, number][] = [["KB", 1e3], ["MB", 1e6], ["GB", 1e9]];
  for (const [unit, step] of units) {
    if (size < step * 1000 || unit === "GB") {
      const value = size / step;
      return value < 10 ? `${value.toFixed(1)} ${unit}` : `${Math.round(value)} ${unit}`;
    }
  }
  return `${size} bytes`;
}

/** The `accept` attribute for a file input: the param's endings, or any file. */
export function acceptAttribute(def: Partial<DriverParamDef>): string | undefined {
  const accept = (def as { accept?: unknown }).accept;
  return Array.isArray(accept) && accept.length > 0 ? accept.join(",") : undefined;
}

/** Why this file will not do for this param, or null. */
export function fileRefusal(
  name: string,
  def: Partial<DriverParamDef>,
  file: { name: string; size: number },
): string | null {
  const label = paramLabel(name, def);
  if (file.size <= 0) return `${file.name} is empty. Choose the file again.`;
  const declared = (def as { max_bytes?: unknown }).max_bytes;
  const limit =
    typeof declared === "number" && declared > 0
      ? Math.min(declared, FILE_PARAM_MAX_BYTES)
      : FILE_PARAM_MAX_BYTES;
  if (file.size > limit) {
    return `${file.name} is ${describeSize(file.size)}. ${label} takes a file up to ${describeSize(limit)}.`;
  }
  const accept = (def as { accept?: unknown }).accept;
  const endings = Array.isArray(accept) ? accept.filter((a): a is string => typeof a === "string") : [];
  if (endings.length > 0) {
    const lower = file.name.toLowerCase();
    if (!endings.some((e) => lower.endsWith(e.toLowerCase()))) {
      const listed =
        endings.length > 1
          ? `${endings.slice(0, -1).join(", ")} or ${endings[endings.length - 1]}`
          : endings[0];
      return `${label} takes a ${listed} file.`;
    }
  }
  return null;
}

/** True while a file param cannot be sent: a required one has no file, or a
 *  chosen one will not do. */
export function fileParamsBlocked(
  params: Record<string, Partial<DriverParamDef>>,
  files: Record<string, File | null | undefined>,
): boolean {
  return Object.entries(params).some(([name, def]) => {
    if (def.type !== "file") return false;
    const file = files[name];
    if (!file) return Boolean(def.required);
    return fileRefusal(name, def, file) !== null;
  });
}
