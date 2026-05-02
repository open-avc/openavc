import { Plus, Upload, Download, Trash2, Copy, Lock, ExternalLink } from "lucide-react";
import type { DriverDefinition } from "../../api/types";

interface DriverListProps {
  definitions: DriverDefinition[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onImport: () => void;
  onExport: (id: string) => void;
  onDuplicate: (id: string) => void;
  onDelete: (id: string) => void;
  onViewAsInstalled?: (id: string) => void;
}

export function DriverList({
  definitions,
  selectedId,
  onSelect,
  onNew,
  onImport,
  onExport,
  onDuplicate,
  onDelete,
  onViewAsInstalled,
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
            background: "var(--accent-bg)",
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
          definitions.map((def) => {
            const isBuiltin = def.source === "builtin";
            // Built-in drivers ship with the platform and can't be edited
            // in place — clicking the row offers a copy instead, so users
            // can't accidentally damage a stock driver. The Lock icon and
            // muted styling cue that.
            const handleRowClick = () => {
              if (isBuiltin) {
                onDuplicate(def.id);
              } else {
                onSelect(def.id);
              }
            };
            return (
              <button
                key={def.id}
                onClick={handleRowClick}
                title={
                  isBuiltin
                    ? "Built-in driver — click to create an editable copy."
                    : def.name
                }
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
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    {isBuiltin && (
                      <Lock
                        size={11}
                        style={{
                          color: "var(--text-muted)",
                          flexShrink: 0,
                        }}
                      />
                    )}
                    <span
                      style={{
                        fontWeight: 500,
                        fontSize: "var(--font-size-sm)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        minWidth: 0,
                        color: isBuiltin ? "var(--text-secondary)" : undefined,
                      }}
                    >
                      {def.name}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-muted)",
                    }}
                  >
                    {def.manufacturer} · {def.category}
                    {isBuiltin && " · built-in"}
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDuplicate(def.id);
                  }}
                  title={
                    isBuiltin
                      ? "Customize a copy — clones to your driver library"
                      : "Duplicate driver — create an editable copy"
                  }
                  style={{
                    padding: "2px",
                    borderRadius: "var(--border-radius)",
                    color: "var(--text-muted)",
                    flexShrink: 0,
                  }}
                >
                  <Copy size={14} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onExport(def.id);
                  }}
                  title="Export driver as .avcdriver file"
                  style={{
                    padding: "2px",
                    borderRadius: "var(--border-radius)",
                    color: "var(--text-muted)",
                    flexShrink: 0,
                  }}
                >
                  <Download size={14} />
                </button>
                {onViewAsInstalled && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onViewAsInstalled(def.id);
                    }}
                    title="View this driver in the Installed catalog"
                    style={{
                      padding: "2px",
                      borderRadius: "var(--border-radius)",
                      color: "var(--text-muted)",
                      flexShrink: 0,
                    }}
                  >
                    <ExternalLink size={14} />
                  </button>
                )}
                {!isBuiltin && (
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
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
