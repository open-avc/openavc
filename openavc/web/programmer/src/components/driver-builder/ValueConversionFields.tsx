import { formatUnknownCodes, parseUnknownCodes } from "./stateVariableHelpers";

// The device-number conversion fields for a numeric state variable (scale,
// offset and the values that mean "no reading") or a numeric command
// parameter (scale and offset). Shared by the state variable, child entity
// and command editors.

type ConversionField = "scale" | "offset" | "unknown";

interface ValueConversionFieldsProps {
  scale?: number;
  offset?: number;
  unknown?: Array<number | string>;
  /** A command parameter converts the other way and has no no-reading values. */
  forParam?: boolean;
  onChange: (field: ConversionField, value: unknown) => void;
}

const inputStyle: React.CSSProperties = { fontSize: "var(--font-size-sm)" };

function numberOrUndefined(raw: string): number | undefined {
  if (raw === "") return undefined;
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : undefined;
}

export function ValueConversionFields({
  scale,
  offset,
  unknown,
  forParam = false,
  onChange,
}: ValueConversionFieldsProps) {
  const unknownText = formatUnknownCodes(unknown);
  return (
    <div
      style={{
        marginTop: "var(--space-xs)",
        marginLeft: "var(--space-sm)",
        paddingLeft: "var(--space-sm)",
        borderLeft: "2px solid var(--border-color)",
        display: "grid",
        gridTemplateColumns: forParam ? "100px 100px 1fr" : "100px 100px 200px 1fr",
        gap: "var(--space-sm)",
        alignItems: "center",
      }}
    >
      <input
        type="number"
        value={scale ?? ""}
        onChange={(e) => onChange("scale", numberOrUndefined(e.target.value))}
        placeholder="scale (1)"
        title="Device number × scale + offset = the real value"
        style={inputStyle}
      />
      <input
        type="number"
        value={offset ?? ""}
        onChange={(e) => onChange("offset", numberOrUndefined(e.target.value))}
        placeholder="offset (0)"
        title="Device number × scale + offset = the real value"
        style={inputStyle}
      />
      {!forParam && (
        <input
          key={unknownText}
          defaultValue={unknownText}
          onBlur={(e) => onChange("unknown", parseUnknownCodes(e.target.value))}
          placeholder="no-reading values (255)"
          title="Comma-separated device values that mean no reading"
          style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
        />
      )}
      <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
        {forParam
          ? "Sends the device (value − offset) ÷ scale, rounded. Match the state variable's scale and offset."
          : "Real value = device number × scale + offset. A no-reading value leaves the variable empty."}
      </div>
    </div>
  );
}
