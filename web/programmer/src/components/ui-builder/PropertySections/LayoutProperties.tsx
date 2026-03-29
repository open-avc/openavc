import type { UIElement, GridConfig } from "../../../api/types";

interface LayoutPropertiesProps {
  element: UIElement;
  gridConfig: GridConfig;
  onChange: (patch: Partial<UIElement>) => void;
}

export function LayoutProperties({
  element,
  gridConfig,
  onChange,
}: LayoutPropertiesProps) {
  const { col, row, col_span, row_span } = element.grid_area;

  const handleChange = (field: string, value: number) => {
    const newArea = { ...element.grid_area };
    switch (field) {
      case "col":
        newArea.col = Math.max(1, Math.min(gridConfig.columns, value));
        newArea.col_span = Math.min(
          newArea.col_span,
          gridConfig.columns - newArea.col + 1,
        );
        break;
      case "row":
        newArea.row = Math.max(1, Math.min(gridConfig.rows, value));
        newArea.row_span = Math.min(
          newArea.row_span,
          gridConfig.rows - newArea.row + 1,
        );
        break;
      case "col_span":
        newArea.col_span = Math.max(
          1,
          Math.min(gridConfig.columns - col + 1, value),
        );
        break;
      case "row_span":
        newArea.row_span = Math.max(
          1,
          Math.min(gridConfig.rows - row + 1, value),
        );
        break;
    }
    onChange({ grid_area: newArea });
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: "var(--space-sm)",
      }}
    >
      <NumberField
        label="Column"
        value={col}
        min={1}
        max={gridConfig.columns}
        onChange={(v) => handleChange("col", v)}
      />
      <NumberField
        label="Row"
        value={row}
        min={1}
        max={gridConfig.rows}
        onChange={(v) => handleChange("row", v)}
      />
      <NumberField
        label="Col Span"
        value={col_span}
        min={1}
        max={gridConfig.columns - col + 1}
        onChange={(v) => handleChange("col_span", v)}
      />
      <NumberField
        label="Row Span"
        value={row_span}
        min={1}
        max={gridConfig.rows - row + 1}
        onChange={(v) => handleChange("row_span", v)}
      />
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label
        style={{
          display: "block",
          fontSize: 11,
          color: "var(--text-muted)",
          marginBottom: 2,
        }}
      >
        {label}
      </label>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(e) => onChange(Number(e.target.value) || min)}
        style={{
          width: "100%",
          padding: "4px 6px",
          fontSize: "var(--font-size-sm)",
        }}
      />
    </div>
  );
}
