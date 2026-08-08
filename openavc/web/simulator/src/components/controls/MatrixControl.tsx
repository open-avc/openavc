import type { MatrixControlDef } from "../../store/api";

interface Props {
  control: MatrixControlDef;
  state: Record<string, unknown>;
  onStateChange: (key: string, value: unknown) => void;
}

function resolveKey(pattern: string, output: number): string {
  return pattern.replace("{output}", String(output));
}

export function MatrixControl({ control, state, onStateChange }: Props) {
  const inputs = Array.from({ length: control.inputs }, (_, i) => i + 1);
  const outputs = Array.from({ length: control.outputs }, (_, i) => i + 1);

  const getRoute = (output: number): number => {
    const key = resolveKey(control.state_pattern, output);
    return Number(state[key] ?? 0);
  };

  const setRoute = (output: number, input: number) => {
    const key = resolveKey(control.state_pattern, output);
    onStateChange(key, input);
  };

  return (
    <div className="ctrl-matrix">
      {control.label && <div className="ctrl-label">{control.label}</div>}
      <div className="matrix-grid">
        {/* Header row — output labels */}
        <div className="matrix-cell matrix-corner" />
        {outputs.map((out) => (
          <div key={`h-${out}`} className="matrix-cell matrix-header">
            {control.output_labels?.[out - 1] ?? `Out ${out}`}
          </div>
        ))}

        {/* Input rows */}
        {inputs.map((inp) => (
          <div key={`r-${inp}`} className="matrix-row">
            <div className="matrix-cell matrix-label">
              {control.input_labels?.[inp - 1] ?? `In ${inp}`}
            </div>
            {outputs.map((out) => {
              const active = getRoute(out) === inp;
              return (
                <div
                  key={`${inp}-${out}`}
                  className={`matrix-cell matrix-point ${active ? "active" : ""}`}
                  onClick={() => setRoute(out, inp)}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
