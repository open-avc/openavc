import { useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import type { DriverParamDef } from "../../../../api/types";
import { useAuditStore } from "../../../../store/auditStore";
import { ParamInput } from "../../../../components/shared/ParamInput";
import { needsPuttingBack, settingsCount, suggestedSetting } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

function valueText(value: unknown): string {
  if (value === null || value === undefined) return "not reported";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/**
 * The driver's device settings, on the Commands step: pick a new value and
 * OpenAVC writes it, checks the device reports it back, puts the old value
 * back and checks that too. A setting whose value cannot be read is shown
 * with the reason and cannot be written (it could not be put back). Every
 * setting is shown, the suggested one first (``suggestedSetting``), as the
 * command list shows every command with its key ones first.
 */
export function SettingsPanel({
  sessionId,
  settings,
  busy,
}: {
  sessionId: string;
  settings: audit.AuditSettings;
  /** A command or the status queries are still being sent or watched. */
  busy: boolean;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const running = settings.current !== null;

  const act = async (key: string, call: () => Promise<{ session: audit.AuditSessionState }>) => {
    setError("");
    setWorking(key);
    try {
      const { session } = await call();
      useAuditStore.getState().setSession(session);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setWorking("");
    }
  };

  if (settings.catalog.length === 0) return null;
  const first = suggestedSetting(settings.catalog);
  const ordered = first
    ? [...settings.catalog.filter((s) => s.key === first), ...settings.catalog.filter((s) => s.key !== first)]
    : settings.catalog;
  return (
    <div style={{ marginTop: "var(--space-lg)" }}>
      <div style={labelStyle}>Device settings</div>
      <p style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-sm)" }}>
        This checks that a setting OpenAVC writes takes effect on the device. Enter a new value and
        press Write and put back: OpenAVC writes it, checks that the device reports it back, then
        puts the old value back and checks that too. Try as many as are safe to change on this device
        for a moment{first ? "; the suggested one is a good place to start." : "."}
      </p>
      <div style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, margin: "0 0 var(--space-sm)" }}>
        {settingsCount(settings)}
      </div>
      {error && <ErrorLine text={error} />}
      {ordered.map((s) => {
        const last = [...settings.trials].reverse().find((t) => t.key === s.key);
        const inFlight = last && last.status !== "done";
        const blocked = busy || running || working !== "" || !s.can_write;
        const value = values[s.key] ?? "";
        const def: Partial<DriverParamDef> = {
          ...s.definition,
          ...(s.definition.regex ? { pattern: s.definition.regex } : {}),
          label: s.label,
        };
        return (
          <div key={s.key} style={{ ...panelStyle, marginBottom: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
            <div style={{ fontWeight: 600 }}>
              {s.label}{" "}
              <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>
                now {valueText(s.value)}
              </span>
              {s.key === first && (
                <span
                  style={{
                    marginLeft: "var(--space-sm)",
                    padding: "0 6px",
                    borderRadius: 8,
                    border: "1px solid var(--accent)",
                    color: "var(--accent)",
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                  }}
                >
                  Suggested
                </span>
              )}
            </div>
            {s.help && <div style={hintStyle}>{s.help}</div>}
            {!s.can_write ? (
              <div style={{ ...hintStyle, fontSize: "var(--font-size-sm)" }}>{s.reason}</div>
            ) : (
              <div style={{ display: "flex", gap: "var(--space-sm)", alignItems: "flex-start", marginTop: "var(--space-xs)" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <ParamInput
                    def={def}
                    value={value}
                    onChange={(v) => setValues((prev) => ({ ...prev, [s.key]: v }))}
                    placeholder="New value"
                    style={{ width: "100%" }}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void act(s.key, () => audit.writeAuditSetting(sessionId, s.key, value))}
                  disabled={blocked || value === ""}
                  aria-label={`Write ${s.label}, then put it back`}
                  style={buttonStyle("muted", blocked || value === "")}
                >
                  {working === s.key && <Loader2 size={14} style={spinStyle} />}
                  Write and put back
                </button>
              </div>
            )}
            {last && (
              <div role="status" style={{ marginTop: "var(--space-xs)", overflowWrap: "anywhere" }}>
                {inFlight ? (
                  <span style={{ display: "inline-flex", gap: "var(--space-xs)", alignItems: "center" }}>
                    <Loader2 size={12} style={spinStyle} />
                    {last.status === "writing"
                      ? `Writing ${valueText(last.value)} and waiting for the device to report it.`
                      : `Putting ${valueText(last.original)} back.`}
                  </span>
                ) : (
                  last.summary
                )}
              </div>
            )}
            {needsPuttingBack(last) && (
              <button
                type="button"
                onClick={() => void act(s.key, () => audit.putAuditSettingBack(sessionId, s.key))}
                disabled={busy || running || working !== ""}
                style={{ ...buttonStyle("muted", busy || running || working !== ""), marginTop: "var(--space-xs)" }}
              >
                <RotateCcw size={12} /> Put it back
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
