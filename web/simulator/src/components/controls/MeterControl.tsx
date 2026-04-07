import type { MeterControlDef } from "../../store/api";

interface Props {
  control: MeterControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

function resolveKey(pattern: string, ch: number): string {
  return pattern.replace("{ch}", String(ch));
}

export function MeterControl({ control, state, onStateChange }: Props) {
  const channels = Array.from({ length: control.channels }, (_, i) => i + 1);

  return (
    <div className="ctrl-meters">
      {control.label && <div className="ctrl-label">{control.label}</div>}
      <div className="meters-row">
        {channels.map((ch) => {
          const key = resolveKey(control.key_pattern, ch);
          const raw = Number(state[key] ?? 0);
          // Normalize to 0-100 for display
          const normalized = raw <= 1 && raw >= 0 ? raw * 100 : Math.max(0, Math.min(100, raw));
          const muted = control.mute_pattern
            ? Boolean(state[resolveKey(control.mute_pattern, ch)])
            : false;

          return (
            <div key={ch} className="meter-channel">
              <div className="audio-meter-bar">
                <div
                  className="audio-meter-fill"
                  style={{
                    height: `${muted ? 0 : normalized}%`,
                    background: normalized > 80 ? "var(--color-warning)" : "var(--accent)",
                  }}
                />
              </div>
              <span className="meter-label">{ch}</span>
              {muted && <span className="meter-muted">M</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
