import { Trash2 } from "lucide-react";
import { oscArgValueIssue } from "./validateDriver";

/**
 * Editor for a list of typed OSC arguments (`{ type, value }[]`) — the shape
 * the runtime's `_build_osc_args` consumes. Shared by the command editor and
 * the on_connect lifecycle editor so both author OSC args identically, with
 * the same per-argument value validation.
 */
export function OscArgsEditor({
  args,
  onChange,
}: {
  args: { type: string; value: string }[];
  onChange: (args: { type: string; value: string }[]) => void;
}) {
  const addArg = () => {
    onChange([...args, { type: "f", value: "" }]);
  };

  const removeArg = (index: number) => {
    onChange(args.filter((_, i) => i !== index));
  };

  const updateArg = (index: number, partial: Partial<{ type: string; value: string }>) => {
    const next = [...args];
    next[index] = { ...next[index], ...partial };
    onChange(next);
  };

  return (
    <div style={{ marginTop: "var(--space-sm)" }}>
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-secondary)",
          marginBottom: "var(--space-xs)",
        }}
      >
        Arguments
      </div>
      {args.length === 0 && (
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-muted)",
            marginBottom: "var(--space-xs)",
          }}
        >
          No arguments — message will be sent as a query (address only).
        </div>
      )}
      {args.map((arg, i) => {
        const problem = oscArgValueIssue(arg.type, arg.value);
        return (
          <div key={i} style={{ marginBottom: "var(--space-xs)" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
              }}
            >
              <select
                value={arg.type}
                onChange={(e) => updateArg(i, { type: e.target.value })}
                style={{ width: 100, fontSize: "var(--font-size-sm)" }}
              >
                <option value="f">Float (f)</option>
                <option value="d">Double (d)</option>
                <option value="i">Integer (i)</option>
                <option value="h">Int64 (h)</option>
                <option value="s">String (s)</option>
                <option value="T">True (T)</option>
                <option value="F">False (F)</option>
                <option value="N">Nil (N)</option>
              </select>
              {!["T", "F", "N"].includes(arg.type) && (
                <input
                  value={arg.value}
                  onChange={(e) => updateArg(i, { value: e.target.value })}
                  placeholder={
                    arg.type === "f" ? "0.0" : arg.type === "i" ? "0" : "text"
                  }
                  style={{
                    flex: 1,
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--font-size-sm)",
                    borderColor: problem ? "var(--color-error)" : undefined,
                  }}
                />
              )}
              <button
                onClick={() => removeArg(i)}
                style={{ padding: "2px", color: "var(--text-muted)" }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {problem && (
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--color-error)",
                  marginTop: 2,
                }}
              >
                Argument {i + 1} {problem} — the command fails to send until
                this is fixed.
              </div>
            )}
          </div>
        );
      })}
      <button
        onClick={addArg}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add Argument
      </button>
      <div
        style={{
          fontSize: "11px",
          color: "var(--text-muted)",
          marginTop: "var(--space-xs)",
        }}
      >
        Values support {"{param_name}"} substitution from the command&apos;s
        parameters and device config, with optional format specs — e.g.{" "}
        <code>{"{level}"}</code> or <code>{"{level:.2f}"}</code>.
      </div>
    </div>
  );
}
