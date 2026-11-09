/** The two controls every Settings card draws: the on/off switch, and the
 *  box for a secret the server never sends back. */
import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { inputStyle } from "../../components/system/settingsStyles";

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
