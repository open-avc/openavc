import { Plus, Upload, Download, Trash2 } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

interface DriverListProps {
  definitions: DriverDefinition[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onImport: () => void;
  onExport: (id: string) => void;
  onDelete: (id: string) => void;
}

export function DriverList({
  definitions,
  selectedId,
  onSelect,
  onNew,
  onImport,
  onExport,
  onDelete,
}: DriverListProps) {
  return (
    <div
      style={{
        width: 260,
        flexShrink: 0,
        borderRight: "1px solid var(--border-color)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "var(--space-md)",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-xs)",
        }}
      >
        <button
          onClick={onNew}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            width: "100%",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--accent)",
            color: "var(--text-on-accent)",
            fontSize: "var(--font-size-sm)",
            justifyContent: "center",
          }}
        >
          <Plus size={14} /> Create New Driver
        </button>
        <button
          onClick={onImport}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            width: "100%",
            padding: "var(--space-sm) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--bg-hover)",
            fontSize: "var(--font-size-sm)",
            justifyContent: "center",
          }}
        >
          <Upload size={14} /> Import from File
        </button>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-sm)" }}>
        {definitions.length === 0 ? (
          <p
            style={{
              color: "var(--text-muted)",
              fontSize: "var(--font-size-sm)",
              padding: "var(--space-md)",
              textAlign: "center",
            }}
          >
            No custom drivers yet.
            <br />
            Create a new driver or import an .avcdriver file.
          </p>
        ) : (
          definitions.map((def) => (
            <button
              key={def.id}
              onClick={() => onSelect(def.id)}
              style={{
                display: "flex",
                alignItems: "center",
                width: "100%",
                padding: "var(--space-sm) var(--space-md)",
                borderRadius: "var(--border-radius)",
                background:
                  selectedId === def.id ? "var(--accent-dim)" : "transparent",
                textAlign: "left",
                marginBottom: "var(--space-xs)",
                gap: "var(--space-sm)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  title={def.name}
                  style={{
                    fontWeight: 500,
                    fontSize: "var(--font-size-sm)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {def.name}
                </div>
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                  }}
                >
                  {def.manufacturer} · {def.category}
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onExport(def.id);
                }}
                title="Export driver as .json file"
                style={{
                  padding: "2px",
                  borderRadius: "var(--border-radius)",
                  color: "var(--text-muted)",
                  flexShrink: 0,
                }}
              >
                <Download size={14} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(def.id);
                }}
                title="Delete driver"
                style={{
                  padding: "2px",
                  borderRadius: "var(--border-radius)",
                  color: "var(--text-muted)",
                  flexShrink: 0,
                }}
              >
                <Trash2 size={14} />
              </button>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
