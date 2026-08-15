/**
 * Monitor — one concept, two doors.
 *
 * Tagging a reading is the same act whether you reach it from the State tab or
 * from a device's live state, so it is one control, and both doors write into
 * the project's one `monitors` list. The Dashboard, the cloud health card and
 * the alert all read that list, which is what stops them disagreeing about
 * what "bad" means in this room.
 *
 * The shape of the control is the feature. Collapsed it is a single toggle,
 * because the common case is one click: the type, label, unit and range are
 * already declared by the driver or the variable, so tagging lamp hours should
 * not put a form in front of somebody. The limits only appear when the user
 * asks for them, and even then the whole form is three things — what it is
 * called, what normal looks like, and how long it has to be wrong.
 *
 * Two rules the widgets exist to hold:
 *   - a boolean is authored as words ("Occupied" / "Vacant"), never true/false;
 *   - declaring nothing is allowed and means informational, so the toggle alone
 *     is a complete answer.
 */

import { useState } from "react";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import type { MonitorConfig, MonitorStateEntry } from "../../api/types";
import { monitorStatus, hasLimits, normalValues, ABNORMAL } from "../../api/monitorHelpers";

/** What the driver or the variable already says about this reading. Everything
 *  here is pre-filled and stays editable — a driver's 0–10000 lamp-hour range
 *  is the lamp's range, not the point at which this customer wants telling. */
export interface DeclaredReading {
  label?: string;
  unit?: string;
  /** string | integer | number | boolean | enum | float */
  type?: string;
  min?: number | null;
  max?: number | null;
  /** enum: the values the driver says this key can take */
  values?: string[];
}

interface Props {
  stateKey: string;
  declared?: DeclaredReading;
  monitors: MonitorConfig[];
  onChange: (monitors: MonitorConfig[], description: string) => void;
  /** The value showing right now, so the author can see what they are judging. */
  liveValue?: unknown;
  compact?: boolean;
  /** Controlled disclosure. The Live State table drives it from outside so the
   *  limits panel can be drawn in a full-width row of its own rather than
   *  squeezed into the narrow cell holding the toggle. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Draw the toggle only, and leave the panel to the caller. */
  toggleOnly?: boolean;
}

const NUMERIC_TYPES = new Set(["number", "integer", "float"]);

function isNumeric(type: string | undefined): boolean {
  return NUMERIC_TYPES.has(type ?? "");
}

/** The values a "normal is..." picker can offer: what the driver declares, in
 *  the order it declares them, then anything the author named that the driver
 *  did not, and true/false for a bare boolean.
 *
 *  The declared order leads on purpose. Listing the author's entries first
 *  meant a value JUMPED TO THE TOP the moment you typed a word for it, so the
 *  rows reordered under the cursor mid-edit and the next tick could land on a
 *  different value than the one being looked at. */
function candidateValues(monitor: MonitorConfig, declared?: DeclaredReading): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (v: string) => {
    if (!seen.has(v)) { seen.add(v); out.push(v); }
  };
  for (const v of declared?.values ?? []) add(v);
  if (monitor.type === "boolean" || declared?.type === "boolean") {
    add("true");
    add("false");
  }
  for (const v of Object.keys(monitor.states ?? {})) add(v);
  return out;
}

/** The word shown for a value in the authoring list. Falls back to Yes/No for a
 *  bare boolean so the author never has to read `true` either. */
function wordFor(monitor: MonitorConfig, value: string): string {
  const entry = monitor.states?.[value];
  if (entry?.label) return entry.label;
  if (value === "true") return "Yes";
  if (value === "false") return "No";
  return value;
}

export function MonitorControl({
  stateKey, declared, monitors, onChange, liveValue, compact,
  open: controlledOpen, onOpenChange, toggleOnly,
}: Props) {
  const monitor = monitors.find((m) => m.key === stateKey);
  const [ownOpen, setOwnOpen] = useState(false);
  const open = controlledOpen ?? ownOpen;
  const setOpen = (next: boolean) => {
    if (onOpenChange) onOpenChange(next);
    else setOwnOpen(next);
  };

  const toggle = () => {
    if (monitor) {
      setOpen(false);
      onChange(monitors.filter((m) => m.key !== stateKey), `Stop monitoring ${stateKey}`);
      return;
    }
    // One click, and nothing to fill in: everything here is already declared.
    const created: MonitorConfig = { key: stateKey };
    if (declared?.label) created.label = declared.label;
    if (declared?.unit) created.unit = declared.unit;
    if (declared?.type) created.type = declared.type;
    onChange([...monitors, created], `Monitor ${stateKey}`);
  };

  const on = Boolean(monitor);
  const status = monitor ? monitorStatus(monitor, liveValue) : null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
        <button
          onClick={toggle}
          title={on ? "Stop watching this reading" : "Watch this reading on the Dashboard and in the cloud"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-sm)",
            padding: compact ? "2px 8px" : "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: on ? "rgba(138,180,147,0.15)" : "var(--bg-surface)",
            border: "1px solid " + (on ? "rgba(138,180,147,0.3)" : "var(--border-color)"),
            color: on ? "var(--accent)" : "var(--text-secondary)",
            fontSize: compact ? 11 : "var(--font-size-sm)",
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          <Activity size={compact ? 11 : 14} />
          {on ? "Monitored" : "Monitor"}
        </button>

        {on && (
          <button
            onClick={() => setOpen(!open)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: compact ? "2px 6px" : "var(--space-sm)",
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              fontSize: compact ? 11 : "var(--font-size-sm)",
              cursor: "pointer",
            }}
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {monitor && statusWord(monitor, status)}
          </button>
        )}
      </div>

      {on && monitor && open && !toggleOnly && (
        <MonitorLimitsPanel
          monitor={monitor}
          declared={declared}
          monitors={monitors}
          onChange={onChange}
        />
      )}
    </div>
  );
}

/** The limits form on its own, so a caller with an awkward container (the Live
 *  State table) can place it where it will actually fit. */
export function MonitorLimitsPanel({
  monitor, declared, monitors, onChange,
}: {
  monitor: MonitorConfig;
  declared?: DeclaredReading;
  monitors: MonitorConfig[];
  onChange: (monitors: MonitorConfig[], description: string) => void;
}) {
  const patch = (next: Partial<MonitorConfig>, description: string) => {
    onChange(
      monitors.map((m) => (m.key === monitor.key ? { ...m, ...next } : m)),
      description,
    );
  };
  return (
    <MonitorLimits
      monitor={monitor}
      declared={declared}
      patch={patch}
      candidates={candidateValues(monitor, declared)}
    />
  );
}

/** What the collapsed row says: the limit that was declared, so the face of the
 *  control states the declaration and the form behind it is where you change
 *  it. Never a colour word for a monitor nobody set limits on — that would be
 *  claiming a judgement its author did not make.
 *
 *  It used to say "Normal" once limits existed, which is the live verdict
 *  rather than the setting, and sat one word away from a control called "Set
 *  what normal looks like" — so the same slot read as a noun on one row and a
 *  verdict on the next. The verdict is still here, but only in the case worth
 *  interrupting for: the reading is outside normal right now. */
function statusWord(monitor: MonitorConfig, status: string | null): string {
  if (!hasLimits(monitor)) return "Set what normal looks like";
  const summary = limitSummary(monitor);
  return status === ABNORMAL ? `${summary} · outside now` : summary;
}

/** "Normal 0–80 %" / "Normal up to 2000 hours" / "Normal: Healthy, Standby". */
function limitSummary(monitor: MonitorConfig): string {
  const words = normalValues(monitor).map((v) => wordFor(monitor, v));
  if (words.length > 0) {
    const shown = words.slice(0, 2).join(", ");
    return `Normal: ${shown}${words.length > 2 ? ` +${words.length - 2}` : ""}`;
  }
  const unit = monitor.unit ? ` ${monitor.unit}` : "";
  const low = monitor.normal_min;
  const high = monitor.normal_max;
  if (low != null && high != null) return `Normal ${low}–${high}${unit}`;
  if (high != null) return `Normal up to ${high}${unit}`;
  return `Normal from ${low}${unit}`;
}

function MonitorLimits({
  monitor, declared, patch, candidates,
}: {
  monitor: MonitorConfig;
  declared?: DeclaredReading;
  patch: (next: Partial<MonitorConfig>, description: string) => void;
  candidates: string[];
}) {
  const type = monitor.type || declared?.type || "string";
  const numeric = isNumeric(type);

  const setStateEntry = (value: string, next: Partial<MonitorStateEntry>) => {
    const states = { ...(monitor.states ?? {}) };
    states[value] = { ...(states[value] ?? {}), ...next };
    patch({ states }, `Monitor limits for ${monitor.key}`);
  };

  return (
    <div
      style={{
        marginTop: "var(--space-sm)",
        padding: "var(--space-md)",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
        maxWidth: 460,
      }}
    >
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        <Field label="Shown as">
          <input
            value={monitor.label ?? ""}
            placeholder={declared?.label || monitor.key}
            onChange={(e) => patch({ label: e.target.value }, `Rename monitor ${monitor.key}`)}
            style={inputStyle}
          />
        </Field>
        {numeric && (
          <Field label="Unit" width={90}>
            <input
              value={monitor.unit ?? ""}
              placeholder={declared?.unit || ""}
              onChange={(e) => patch({ unit: e.target.value }, `Monitor unit for ${monitor.key}`)}
              style={inputStyle}
            />
          </Field>
        )}
      </div>

      <div>
        <div style={legendStyle}>Normal is</div>
        {numeric ? (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <input
              type="number"
              value={monitor.normal_min ?? ""}
              placeholder={declared?.min != null ? String(declared.min) : "no minimum"}
              onChange={(e) => patch(
                { normal_min: e.target.value === "" ? null : Number(e.target.value) },
                `Monitor minimum for ${monitor.key}`,
              )}
              style={{ ...inputStyle, width: 120 }}
            />
            <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>to</span>
            <input
              type="number"
              value={monitor.normal_max ?? ""}
              placeholder={declared?.max != null ? String(declared.max) : "no maximum"}
              onChange={(e) => patch(
                { normal_max: e.target.value === "" ? null : Number(e.target.value) },
                `Monitor maximum for ${monitor.key}`,
              )}
              style={{ ...inputStyle, width: 120 }}
            />
            {monitor.unit && (
              <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
                {monitor.unit}
              </span>
            )}
          </div>
        ) : candidates.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {candidates.map((value) => (
              <div key={value} style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 90, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={monitor.states?.[value]?.normal === true}
                    onChange={(e) => setStateEntry(value, { normal: e.target.checked ? true : null })}
                  />
                  <span style={{ fontSize: "var(--font-size-sm)" }}>{wordFor(monitor, value)}</span>
                </label>
                <input
                  value={monitor.states?.[value]?.label ?? ""}
                  placeholder={`Call it something ("${wordFor(monitor, value)}")`}
                  onChange={(e) => setStateEntry(value, { label: e.target.value })}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", minWidth: 48 }}>
                  {value}
                </code>
              </div>
            ))}
          </div>
        ) : (
          <ValueAdder onAdd={(v) => setStateEntry(v, { normal: true })} />
        )}
        <div style={hintStyle}>
          {numeric
            ? "Leave both blank to show the reading without judging it."
            : "Tick the values that mean everything is fine. Tick none to show the reading without judging it."}
        </div>
      </div>

      <div>
        <div style={legendStyle}>Tell me if it stays wrong for</div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <input
            type="number"
            min={0}
            value={monitor.duration_seconds ? monitor.duration_seconds / 60 : ""}
            placeholder="0"
            onChange={(e) => patch(
              { duration_seconds: e.target.value === "" ? 0 : Number(e.target.value) * 60 },
              `Monitor delay for ${monitor.key}`,
            )}
            style={{ ...inputStyle, width: 90 }}
          />
          <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>minutes</span>
        </div>
        <div style={hintStyle}>
          Blank tells you straight away. A projector that is off is normal at
          3am and a problem ten minutes into a lecture.
        </div>
      </div>
    </div>
  );
}

/** For a string reading whose values nobody has declared — the author types the
 *  ones that count as normal, because only they know what this device says. */
function ValueAdder({ onAdd }: { onAdd: (value: string) => void }) {
  const [draft, setDraft] = useState("");
  const commit = () => {
    const value = draft.trim();
    if (value) { onAdd(value); setDraft(""); }
  };
  return (
    <div style={{ display: "flex", gap: "var(--space-sm)" }}>
      <input
        value={draft}
        placeholder="a value that means everything is fine"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }}
        style={{ ...inputStyle, flex: 1 }}
      />
      <button onClick={commit} style={{ ...inputStyle, cursor: "pointer", width: 60 }}>Add</button>
    </div>
  );
}

function Field({ label, width, children }: { label: string; width?: number; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: width ? undefined : 1, width }}>
      <div style={legendStyle}>{label}</div>
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "6px 8px",
  background: "var(--bg-input, var(--bg-hover))",
  border: "1px solid var(--border-color)",
  borderRadius: "var(--border-radius)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  width: "100%",
};

const legendStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-secondary)",
  marginBottom: 4,
};

const hintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginTop: 6,
};
