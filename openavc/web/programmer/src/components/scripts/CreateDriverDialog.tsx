import { useState } from "react";
import { Modal } from "../shared/Modal";
import { DRIVER_TEMPLATES } from "./driverTemplates";
import { DRIVER_CATEGORIES } from "../driver-builder/driverCategories";

interface CreateDriverDialogProps {
  onSubmit: (id: string, source: string) => void;
  onCancel: () => void;
  existingIds?: string[];
}

const TRANSPORTS = [
  { value: "tcp", label: "TCP" },
  { value: "serial", label: "Serial (RS-232)" },
  { value: "http", label: "HTTP / REST" },
  { value: "udp", label: "UDP" },
  { value: "osc", label: "OSC (Open Sound Control)" },
];

export function CreateDriverDialog({ onSubmit, onCancel, existingIds = [] }: CreateDriverDialogProps) {
  const [driverId, setDriverId] = useState("");
  const [driverName, setDriverName] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [category, setCategory] = useState("utility");
  const [transport, setTransport] = useState("tcp");
  const [selectedTemplate, setSelectedTemplate] = useState<string>("tcp");

  const sanitizedId = driverId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_");
  const isDuplicate = existingIds.includes(sanitizedId);
  const isValid = sanitizedId.length > 0 && driverName.trim().length > 0 && !isDuplicate;

  const handleSubmit = () => {
    if (!isValid) return;

    const template = DRIVER_TEMPLATES.find((t) => t.id === selectedTemplate) ?? DRIVER_TEMPLATES[0];
    const source = template.generateCode({
      id: sanitizedId,
      name: driverName.trim(),
      manufacturer: manufacturer.trim() || "",
      category,
      transport,
    });

    onSubmit(sanitizedId, source);
  };

  // Filter templates to show transport-relevant ones first, but show all
  const sortedTemplates = [...DRIVER_TEMPLATES].sort((a, b) => {
    const aMatch = a.transport === transport ? 0 : 1;
    const bMatch = b.transport === transport ? 0 : 1;
    return aMatch - bMatch;
  });

  return (
    <Modal
      onClose={onCancel}
      label="Create Python Driver"
      overlayStyle={{ background: "rgba(0,0,0,0.5)" }}
      panelStyle={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-color)",
        borderRadius: "var(--radius-lg)",
        padding: "var(--space-lg)",
        width: 520,
        maxHeight: "85vh",
        overflow: "auto",
        boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
      }}
    >
        <h3
          style={{
            margin: "0 0 var(--space-md) 0",
            fontSize: "var(--font-size-lg, 16px)",
            color: "var(--text-primary)",
          }}
        >
          Create Python Driver
        </h3>

        {/* Form fields */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
          <label style={labelStyle}>
            Driver ID
            <input
              type="text"
              value={driverId}
              onChange={(e) => setDriverId(e.target.value)}
              placeholder="e.g. lg_webos"
              style={inputStyle}
              autoFocus
            />
            {driverId && sanitizedId !== driverId.trim() && (
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                Will be saved as: {sanitizedId}
              </span>
            )}
            {isDuplicate && (
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-error)" }}>
                Driver ID already exists
              </span>
            )}
          </label>

          <label style={labelStyle}>
            Display Name
            <input
              type="text"
              value={driverName}
              onChange={(e) => setDriverName(e.target.value)}
              placeholder="e.g. LG webOS Display"
              style={inputStyle}
            />
          </label>

          <label style={labelStyle}>
            Manufacturer
            <input
              type="text"
              value={manufacturer}
              onChange={(e) => setManufacturer(e.target.value)}
              placeholder="Optional"
              style={inputStyle}
            />
          </label>

          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <label style={{ ...labelStyle, flex: 1 }}>
              Category
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                style={inputStyle}
              >
                {DRIVER_CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ ...labelStyle, flex: 1 }}>
              Transport
              <select
                value={transport}
                onChange={(e) => {
                  setTransport(e.target.value);
                  // Auto-select matching template
                  const match = DRIVER_TEMPLATES.find((t) => t.transport === e.target.value);
                  if (match) setSelectedTemplate(match.id);
                }}
                style={inputStyle}
              >
                {TRANSPORTS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {/* Template selection */}
        <div style={{ marginTop: "var(--space-md)" }}>
          <div
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
              fontWeight: "var(--font-weight-semibold)",
            }}
          >
            Template
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            {sortedTemplates.map((t) => (
              <div
                key={t.id}
                onClick={() => setSelectedTemplate(t.id)}
                style={{
                  padding: "var(--space-sm) var(--space-md)",
                  border: `1px solid ${selectedTemplate === t.id ? "var(--accent)" : "var(--border-color)"}`,
                  borderRadius: "var(--border-radius)",
                  cursor: "pointer",
                  background:
                    selectedTemplate === t.id ? "var(--bg-hover)" : "transparent",
                }}
              >
                <div
                  style={{
                    fontSize: "var(--font-size-sm)",
                    fontWeight: "var(--font-weight-medium)",
                    color: "var(--text-primary)",
                  }}
                >
                  {t.name}
                  {t.transport === transport && t.id !== "minimal" && (
                    <span
                      style={{
                        marginLeft: "var(--space-sm)",
                        fontSize: "var(--font-size-2xs)",
                        color: "var(--accent)",
                        fontWeight: "var(--font-weight-normal)",
                      }}
                    >
                      Recommended
                    </span>
                  )}
                </div>
                <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                  {t.description}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-sm)",
            marginTop: "var(--space-lg)",
          }}
        >
          <button onClick={onCancel} style={cancelBtnStyle}>
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!isValid}
            style={{
              ...createBtnStyle,
              opacity: isValid ? 1 : 0.5,
              cursor: isValid ? "pointer" : "not-allowed",
            }}
          >
            Create Driver
          </button>
        </div>
    </Modal>
  );
}

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-xs)",
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  fontWeight: "var(--font-weight-medium)",
};

const inputStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-normal)",
};

const createBtnStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
  fontWeight: "var(--font-weight-medium)",
};

const cancelBtnStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-lg)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-primary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};
