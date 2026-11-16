import { lineOptions, type SerialLineKey } from "./serialLine";

/** A picker for one serial line setting, offering what the driver contract's
 *  table offers (serialLine.ts) with `written` selected however it was
 *  spelled. The Builder's Connection tab and the device form both draw it. */
export function LineSettingSelect({
  setting,
  written,
  onChange,
  style,
}: {
  setting: SerialLineKey;
  written: unknown;
  onChange: (value: string) => void;
  style?: React.CSSProperties;
}) {
  const { selected, options } = lineOptions(setting, written);
  return (
    <select value={selected} onChange={(e) => onChange(e.target.value)} style={style}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
