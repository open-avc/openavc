/** The controls and rows every Settings card draws: the on/off switch, the
 *  box for a secret the server never sends back, a number box, and the two
 *  rows a setting sits in (a switch beside its name and help line; a label
 *  beside its control in the card's grid). A row for a setting the
 *  environment can hold takes it as `held` (EnvHeldNote.tsx): the control is
 *  locked and the line under it says where the value is set. */
import { useState, type CSSProperties, type ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { EnvLock, HeldNote, type HeldSetting } from "../../components/system/EnvHeldNote";
import { fieldRow, helpText, inputStyle, labelStyle, toggleRow } from "../../components/system/settingsStyles";

/** What the server sends in place of a secret it has stored. Sending it back
 *  unchanged means "leave it as it is", and it counts as set. */
export const REDACTED = "***";

const toggleStyle: React.CSSProperties = {
  position: "relative",
  width: 40,
  height: 22,
  borderRadius: 11,
  cursor: "pointer",
  transition: "background var(--transition-fast)",
  border: "none",
  flexShrink: 0,
};

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      style={{
        ...toggleStyle,
        background: checked ? "var(--accent-bg)" : "var(--bg-hover, #555)",
      }}
      onClick={() => onChange(!checked)}
    >
      <div
        style={{
          position: "absolute",
          top: 3,
          left: checked ? 21 : 3,
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "#fff",
          transition: "left var(--transition-fast)",
        }}
      />
    </button>
  );
}

/** A stored secret shows as an empty box saying it is set; typing replaces it. */
export function PasswordField({
  value,
  placeholder,
  onChange,
}: {
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const isRedacted = value === REDACTED;
  return (
    <div style={{ position: "relative" }}>
      <input
        type={visible ? "text" : "password"}
        autoComplete="new-password"
        style={inputStyle}
        value={isRedacted ? "" : value}
        placeholder={isRedacted ? "Set (hidden)" : placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {!isRedacted && (
        <button
          type="button"
          onClick={() => setVisible(!visible)}
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            background: "none",
            border: "none",
            color: "var(--text-muted)",
            cursor: "pointer",
            padding: 4,
          }}
        >
          {visible ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      )}
    </div>
  );
}

/** A whole number. A cleared box, or one reading 0, saves `fallback`. */
export function NumberBox({ id, value, fallback, onChange, min, max, style = inputStyle }: {
  id?: string;
  value: number;
  fallback: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  style?: CSSProperties;
}) {
  return (
    <input
      id={id}
      type="number"
      min={min}
      max={max}
      style={style}
      value={value}
      onChange={(e) => onChange(parseInt(e.target.value) || fallback)}
    />
  );
}

/** An on/off setting: its name and help line, then the switch. */
export function SwitchRow({ title, help, helpTestId, label, held, checked, onChange }: {
  title: string;
  help: ReactNode;
  helpTestId?: string;
  /** The switch's own name, for a reader that finds it without its row. */
  label?: string;
  held?: HeldSetting;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  const toggle = <Toggle checked={checked} onChange={onChange} label={label} />;
  return (
    <div style={toggleRow}>
      <div>
        <div style={{ fontSize: "var(--font-size-sm)" }}>{title}</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }} data-testid={helpTestId}>{help}</div>
        {held && <HeldNote hold={held.hold} variable={held.variable} />}
      </div>
      {held ? <EnvLock held={held.locked} style={{ flexShrink: 0 }}>{toggle}</EnvLock> : toggle}
    </div>
  );
}

/** A setting in the card's label-and-control grid: the label, the control
 *  (`children`), its help line, the held line, then anything `after` says
 *  about the value. */
export function FieldRow({ label, htmlFor, help, held, lockStyle, after, children }: {
  label: string;
  htmlFor?: string;
  help?: ReactNode;
  held?: HeldSetting;
  /** How the locked control is laid out, for a control of more than one part. */
  lockStyle?: CSSProperties;
  after?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div style={fieldRow}>
      <label style={labelStyle} htmlFor={htmlFor}>{label}</label>
      {held ? <EnvLock held={held.locked} style={lockStyle}>{children}</EnvLock> : children}
      {help !== undefined && <span style={helpText}>{help}</span>}
      {held && <HeldNote hold={held.hold} variable={held.variable} style={{ gridColumn: "2" }} />}
      {after}
    </div>
  );
}
