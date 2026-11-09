/**
 * The one filter box on a device's page. Its words narrow both the Child
 * Entities rows (across every value in a row, not just the summary columns)
 * and the Live State list, so the page holds them and hands them to each.
 */
import { Search, X } from "lucide-react";

export function DeviceFilter({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  return (
    <div style={{ position: "relative", marginBottom: "var(--space-lg)" }}>
      <Search
        size={16}
        style={{
          position: "absolute",
          left: 10,
          top: "50%",
          transform: "translateY(-50%)",
          color: "var(--text-muted)",
          pointerEvents: "none",
        }}
      />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Filter children and live state by id, label, name, or any value"
        data-testid="device-filter"
        style={{ width: "100%", padding: "var(--space-sm) 34px" }}
      />
      {value && (
        <button
          onClick={() => onChange("")}
          title="Clear filter"
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            padding: 2,
            background: "transparent",
            color: "var(--text-muted)",
            border: "none",
            cursor: "pointer",
          }}
        >
          <X size={16} />
        </button>
      )}
    </div>
  );
}
