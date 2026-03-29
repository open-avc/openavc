import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Download, Upload, Save, RefreshCw, AlertTriangle, Check } from "lucide-react";
import {
  getTheme,
  createTheme,
  importTheme,
  type ThemeDefinition,
  type ThemeSummary,
} from "../../api/restClient";

// --- WCAG Contrast Utilities ---

function hexToRgb(hex: string): [number, number, number] | null {
  const m = hex.replace("#", "").match(/^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!m) return null;
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

function relativeLuminance(r: number, g: number, b: number): number {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function contrastRatio(hex1: string, hex2: string): number | null {
  const rgb1 = hexToRgb(hex1);
  const rgb2 = hexToRgb(hex2);
  if (!rgb1 || !rgb2) return null;
  const l1 = relativeLuminance(...rgb1);
  const l2 = relativeLuminance(...rgb2);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

type WcagLevel = "AAA" | "AA" | "fail";

function wcagLevel(ratio: number): WcagLevel {
  if (ratio >= 7) return "AAA";
  if (ratio >= 4.5) return "AA";
  return "fail";
}

// --- Variable Categories ---

interface VarDef {
  key: string;
  label: string;
  type: "color" | "number" | "font";
}

const VAR_CATEGORIES: { name: string; vars: VarDef[] }[] = [
  {
    name: "Panel",
    vars: [
      { key: "panel_bg", label: "Background", type: "color" },
      { key: "panel_text", label: "Text Color", type: "color" },
      { key: "surface", label: "Surface", type: "color" },
      { key: "surface_border", label: "Surface Border", type: "color" },
    ],
  },
  {
    name: "Accent",
    vars: [
      { key: "accent", label: "Accent", type: "color" },
      { key: "accent_hover", label: "Accent Hover", type: "color" },
    ],
  },
  {
    name: "Buttons",
    vars: [
      { key: "button_bg", label: "Background", type: "color" },
      { key: "button_text", label: "Text", type: "color" },
      { key: "button_active_bg", label: "Active BG", type: "color" },
      { key: "button_active_text", label: "Active Text", type: "color" },
      { key: "button_border", label: "Border", type: "color" },
    ],
  },
  {
    name: "Status",
    vars: [
      { key: "danger", label: "Danger", type: "color" },
      { key: "success", label: "Success", type: "color" },
      { key: "warning", label: "Warning", type: "color" },
    ],
  },
  {
    name: "Layout",
    vars: [
      { key: "grid_gap", label: "Grid Gap (px)", type: "number" },
      { key: "border_radius", label: "Border Radius (px)", type: "number" },
      { key: "font_family", label: "Font Family", type: "font" },
    ],
  },
];

const ELEMENT_TYPES = [
  "button", "label", "slider", "page_nav", "select", "text_input",
  "status_led", "image", "spacer", "camera_preset", "gauge",
  "level_meter", "fader", "group", "clock", "keypad", "list", "matrix",
] as const;

// Friendly display names for element types
const ELEMENT_TYPE_LABELS: Record<string, string> = {
  button: "Button",
  label: "Label",
  slider: "Slider",
  page_nav: "Page Nav",
  select: "Select",
  text_input: "Text Input",
  status_led: "Status LED",
  image: "Image",
  spacer: "Spacer",
  camera_preset: "Camera Preset",
  gauge: "Gauge",
  level_meter: "Level Meter",
  fader: "Fader",
  group: "Group",
  clock: "Clock",
  keypad: "Keypad",
  list: "List",
  matrix: "Matrix",
};

interface StyleKeyDef {
  key: string;
  label: string;
  type: "color" | "number" | "text";
}

const BASE_STYLE_KEYS: StyleKeyDef[] = [
  { key: "bg_color", label: "Background", type: "color" },
  { key: "text_color", label: "Text Color", type: "color" },
  { key: "border_width", label: "Border Width", type: "number" },
  { key: "border_color", label: "Border Color", type: "color" },
  { key: "border_radius", label: "Border Radius", type: "number" },
  { key: "box_shadow", label: "Box Shadow", type: "text" },
];

// Additional style keys for specific element types
const EXTRA_STYLE_KEYS: Record<string, StyleKeyDef[]> = {
  gauge: [
    { key: "gauge_color", label: "Gauge Color", type: "color" },
    { key: "gauge_bg_color", label: "Gauge Background", type: "color" },
  ],
  list: [
    { key: "item_bg", label: "Item Background", type: "color" },
    { key: "item_active_bg", label: "Selected Item", type: "color" },
  ],
  matrix: [
    { key: "crosspoint_active_color", label: "Active Route", type: "color" },
    { key: "crosspoint_inactive_color", label: "Inactive Route", type: "color" },
  ],
};

// Minimal types that only need background
const MINIMAL_TYPES = new Set(["status_led", "image", "spacer", "level_meter", "fader", "clock"]);

function getStyleKeysForType(elType: string): StyleKeyDef[] {
  if (MINIMAL_TYPES.has(elType)) {
    // These elements only need background and text color
    const keys: StyleKeyDef[] = [
      { key: "bg_color", label: "Background", type: "color" },
      { key: "text_color", label: "Text Color", type: "color" },
    ];
    return [...keys, ...(EXTRA_STYLE_KEYS[elType] || [])];
  }
  return [...BASE_STYLE_KEYS, ...(EXTRA_STYLE_KEYS[elType] || [])];
}

// --- Component ---

interface ThemeEditorProps {
  themes: ThemeSummary[];
  currentThemeId: string;
  onThemeChange: (themeId: string) => void;
  onRefreshThemes: () => void;
  onApplyOverrides?: (overrides: Record<string, unknown>) => void;
}

export function ThemeEditor({
  themes,
  currentThemeId,
  onThemeChange,
  onRefreshThemes,
  onApplyOverrides,
}: ThemeEditorProps) {
  const [themeData, setThemeData] = useState<ThemeDefinition | null>(null);
  const [editedVars, setEditedVars] = useState<Record<string, unknown>>({});
  const [editedDefaults, setEditedDefaults] = useState<Record<string, Record<string, unknown>>>({});
  const [themeName, setThemeName] = useState("");
  const [themeDesc, setThemeDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  const [importMsg, setImportMsg] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load theme data when selected theme changes
  useEffect(() => {
    if (!currentThemeId) return;
    getTheme(currentThemeId)
      .then((t) => {
        setThemeData(t);
        setEditedVars({ ...(t.variables || {}) });
        setEditedDefaults({ ...(t.element_defaults || {}) });
        setThemeName(t.name);
        setThemeDesc(t.description || "");
      })
      .catch(() => {});
  }, [currentThemeId]);

  const updateVar = useCallback((key: string, value: unknown) => {
    setEditedVars((prev) => ({ ...prev, [key]: value }));
  }, []);

  const updateElementDefault = useCallback(
    (elType: string, key: string, value: unknown) => {
      setEditedDefaults((prev) => ({
        ...prev,
        [elType]: { ...(prev[elType] || {}), [key]: value },
      }));
    },
    [],
  );

  // Contrast checks
  const contrastChecks = useMemo(() => {
    const v = editedVars as Record<string, string>;
    const checks: { label: string; fg: string; bg: string; ratio: number | null; level: WcagLevel }[] = [];
    const pairs: [string, string, string][] = [
      ["Text on Background", "panel_text", "panel_bg"],
      ["Button Text", "button_text", "button_bg"],
      ["Active Button Text", "button_active_text", "button_active_bg"],
      ["Accent on Background", "accent", "panel_bg"],
      ["Danger on Background", "danger", "panel_bg"],
      ["Success on Background", "success", "panel_bg"],
      ["Warning on Background", "warning", "panel_bg"],
    ];
    for (const [label, fgKey, bgKey] of pairs) {
      const fg = v[fgKey];
      const bg = v[bgKey];
      if (fg && bg) {
        const ratio = contrastRatio(fg, bg);
        checks.push({ label, fg, bg, ratio, level: ratio ? wcagLevel(ratio) : "fail" });
      }
    }
    return checks;
  }, [editedVars]);

  const failingContrasts = contrastChecks.filter((c) => c.level === "fail");

  // Apply edits as project-level overrides (diff against base theme)
  const handleApplyToProject = () => {
    if (!themeData || !onApplyOverrides) return;
    const baseVars = themeData.variables || {};
    const overrides: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(editedVars)) {
      if (val !== baseVars[key]) {
        overrides[key] = val;
      }
    }
    onApplyOverrides(overrides);
    setSaveMsg(
      Object.keys(overrides).length > 0
        ? `Applied ${Object.keys(overrides).length} override(s) to project`
        : "No changes from base theme — overrides cleared",
    );
  };

  // Save as Custom Theme
  const handleSaveAsCustom = async () => {
    const id = themeName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
    if (!id) return;

    setSaving(true);
    setSaveMsg("");
    try {
      const previewColors = [
        editedVars.panel_bg,
        editedVars.surface || editedVars.button_bg,
        editedVars.accent,
        editedVars.panel_text,
      ].filter(Boolean) as string[];

      const newTheme: ThemeDefinition = {
        id,
        name: themeName,
        version: "1.0.0",
        author: "Custom",
        description: themeDesc,
        preview_colors: previewColors,
        variables: editedVars,
        element_defaults: editedDefaults,
        page_defaults: themeData?.page_defaults,
      };
      const result = await createTheme(newTheme);
      setSaveMsg(`Saved as "${themeName}"`);
      onRefreshThemes();
      onThemeChange(result.id);
    } catch (e) {
      setSaveMsg(`Error: ${e instanceof Error ? e.message : "Save failed"}`);
    }
    setSaving(false);
  };

  // Export theme
  const handleExport = () => {
    if (!themeData) return;
    const exportData = {
      format: "avctheme",
      format_version: "1.0.0",
      theme: {
        ...themeData,
        name: themeName,
        description: themeDesc,
        variables: editedVars,
        element_defaults: editedDefaults,
      },
    };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${themeData.id}.avctheme`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Import theme
  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportMsg("");
    try {
      const result = await importTheme(file);
      setImportMsg(`Imported "${result.name}"`);
      onRefreshThemes();
      onThemeChange(result.id);
    } catch (err) {
      setImportMsg(`Error: ${err instanceof Error ? err.message : "Import failed"}`);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  if (!themeData) {
    return (
      <div style={{ padding: 16, color: "var(--text-muted)", textAlign: "center" }}>
        Select a theme to edit
      </div>
    );
  }

  const isBuiltIn = !themeData._source || themeData._source === "builtin";
  const sectionStyle: React.CSSProperties = {
    marginBottom: 16,
    padding: 12,
    background: "var(--bg-surface)",
    borderRadius: 6,
    border: "1px solid var(--border-color)",
  };
  const sectionTitleStyle: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    color: "var(--text-secondary)",
    marginBottom: 8,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, paddingBottom: 16 }}>
      {/* Theme name + description */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>Theme Info</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label style={{ width: 70, fontSize: 12, color: "var(--text-secondary)", flexShrink: 0 }}>
              Name
            </label>
            <input
              value={themeName}
              onChange={(e) => setThemeName(e.target.value)}
              style={{ flex: 1, fontSize: 12 }}
            />
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label style={{ width: 70, fontSize: 12, color: "var(--text-secondary)", flexShrink: 0 }}>
              Description
            </label>
            <input
              value={themeDesc}
              onChange={(e) => setThemeDesc(e.target.value)}
              style={{ flex: 1, fontSize: 12 }}
            />
          </div>
        </div>
      </div>

      {/* Live preview */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>Preview</div>
        <div
          style={{
            background: String(editedVars.panel_bg || "#1a1a2e"),
            borderRadius: 8,
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          {/* Sample buttons */}
          <div style={{ display: "flex", gap: 6 }}>
            <div
              style={{
                padding: "6px 14px",
                borderRadius: Number(editedVars.border_radius || 8),
                background: String(editedVars.button_bg || "#424242"),
                color: String(editedVars.button_text || "#e0e0e0"),
                fontSize: 12,
                border: `1px solid ${editedVars.button_border || "#555"}`,
              }}
            >
              Button
            </div>
            <div
              style={{
                padding: "6px 14px",
                borderRadius: Number(editedVars.border_radius || 8),
                background: String(editedVars.button_active_bg || "#2196F3"),
                color: String(editedVars.button_active_text || "#fff"),
                fontSize: 12,
              }}
            >
              Active
            </div>
            <div
              style={{
                padding: "6px 14px",
                borderRadius: Number(editedVars.border_radius || 8),
                background: String(editedVars.danger || "#ef5350"),
                color: "#fff",
                fontSize: 12,
              }}
            >
              Danger
            </div>
            <div
              style={{
                padding: "6px 14px",
                borderRadius: Number(editedVars.border_radius || 8),
                background: String(editedVars.success || "#66bb6a"),
                color: "#fff",
                fontSize: 12,
              }}
            >
              OK
            </div>
          </div>
          {/* Sample text */}
          <div style={{ color: String(editedVars.panel_text || "#e0e0e0"), fontSize: 13 }}>
            Sample text on panel background
          </div>
          <div
            style={{
              background: String(editedVars.surface || "#2a2a4a"),
              border: `1px solid ${editedVars.surface_border || "#3a3a5c"}`,
              borderRadius: Number(editedVars.border_radius || 8),
              padding: "6px 10px",
              color: String(editedVars.panel_text || "#e0e0e0"),
              fontSize: 12,
            }}
          >
            Surface card with border
          </div>
          {/* Slider mock */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ color: String(editedVars.panel_text || "#e0e0e0"), fontSize: 11 }}>Volume</span>
            <div
              style={{
                flex: 1,
                height: 6,
                borderRadius: 3,
                background: String(editedVars.surface || "#2a2a4a"),
                position: "relative",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: "60%",
                  height: "100%",
                  background: String(editedVars.accent || "#2196F3"),
                  borderRadius: 3,
                }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Color palette editor by category */}
      {VAR_CATEGORIES.map((cat) => (
        <div key={cat.name} style={sectionStyle}>
          <div style={sectionTitleStyle}>{cat.name}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {cat.vars.map((v) => (
              <div key={v.key} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <label
                  style={{
                    width: 90,
                    fontSize: 11,
                    color: "var(--text-secondary)",
                    flexShrink: 0,
                  }}
                >
                  {v.label}
                </label>
                {v.type === "color" ? (
                  <>
                    <input
                      type="color"
                      value={String(editedVars[v.key] || "#000000")}
                      onChange={(e) => updateVar(v.key, e.target.value)}
                      style={{
                        width: 28,
                        height: 22,
                        padding: 0,
                        border: "1px solid var(--border-color)",
                        borderRadius: 3,
                        cursor: "pointer",
                      }}
                    />
                    <input
                      type="text"
                      value={String(editedVars[v.key] || "")}
                      onChange={(e) => updateVar(v.key, e.target.value)}
                      style={{ flex: 1, fontSize: 11, fontFamily: "monospace" }}
                    />
                  </>
                ) : v.type === "number" ? (
                  <input
                    type="number"
                    value={Number(editedVars[v.key] || 0)}
                    onChange={(e) => updateVar(v.key, Number(e.target.value))}
                    min={0}
                    max={32}
                    style={{ flex: 1, fontSize: 11 }}
                  />
                ) : (
                  <select
                    value={String(editedVars[v.key] || "Inter, system-ui, sans-serif")}
                    onChange={(e) => updateVar(v.key, e.target.value)}
                    style={{ flex: 1, fontSize: 11 }}
                  >
                    <option value="Inter, system-ui, sans-serif">Inter</option>
                    <option value="system-ui, sans-serif">System UI</option>
                    <option value="'Roboto', sans-serif">Roboto</option>
                    <option value="'Segoe UI', sans-serif">Segoe UI</option>
                    <option value="monospace">Monospace</option>
                  </select>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Element Defaults */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>Element Defaults</div>
        {ELEMENT_TYPES.map((elType) => (
          <details key={elType} style={{ marginBottom: 6 }}>
            <summary
              style={{
                cursor: "pointer",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--text-primary)",
                padding: "4px 0",
              }}
            >
              {ELEMENT_TYPE_LABELS[elType] || elType}
            </summary>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                paddingLeft: 12,
                paddingTop: 4,
              }}
            >
              {getStyleKeysForType(elType).map((sk) => {
                const val = editedDefaults[elType]?.[sk.key];
                return (
                  <div key={sk.key} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <label style={{ width: 80, fontSize: 10, color: "var(--text-muted)", flexShrink: 0 }}>
                      {sk.label}
                    </label>
                    {sk.type === "color" ? (
                      <>
                        <input
                          type="color"
                          value={String(val || "#000000").replace("transparent", "#000000")}
                          onChange={(e) => updateElementDefault(elType, sk.key, e.target.value)}
                          style={{
                            width: 24,
                            height: 18,
                            padding: 0,
                            border: "1px solid var(--border-color)",
                            borderRadius: 2,
                            cursor: "pointer",
                          }}
                        />
                        <input
                          type="text"
                          value={String(val ?? "")}
                          onChange={(e) => updateElementDefault(elType, sk.key, e.target.value)}
                          style={{ flex: 1, fontSize: 10, fontFamily: "monospace" }}
                        />
                      </>
                    ) : sk.type === "number" ? (
                      <input
                        type="number"
                        value={Number(val || 0)}
                        onChange={(e) => updateElementDefault(elType, sk.key, Number(e.target.value))}
                        min={0}
                        style={{ flex: 1, fontSize: 10 }}
                      />
                    ) : (
                      <input
                        type="text"
                        value={String(val ?? "")}
                        onChange={(e) => updateElementDefault(elType, sk.key, e.target.value)}
                        placeholder="none"
                        style={{ flex: 1, fontSize: 10 }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        ))}
      </div>

      {/* Contrast Checker */}
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>
          Contrast Check (WCAG)
          {failingContrasts.length > 0 && (
            <span style={{ color: "var(--color-error)", marginLeft: 6, fontSize: 10 }}>
              {failingContrasts.length} failing
            </span>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {contrastChecks.map((c) => (
            <div
              key={c.label}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 11,
                padding: "3px 0",
              }}
            >
              {c.level === "fail" ? (
                <AlertTriangle size={12} style={{ color: "#ef5350", flexShrink: 0 }} />
              ) : (
                <Check size={12} style={{ color: "#66bb6a", flexShrink: 0 }} />
              )}
              <span style={{ flex: 1, color: "var(--text-secondary)" }}>{c.label}</span>
              <div style={{ display: "flex", gap: 2 }}>
                <div
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: 2,
                    background: c.fg,
                    border: "1px solid rgba(128,128,128,0.3)",
                  }}
                />
                <div
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: 2,
                    background: c.bg,
                    border: "1px solid rgba(128,128,128,0.3)",
                  }}
                />
              </div>
              <span
                style={{
                  fontFamily: "monospace",
                  fontSize: 10,
                  fontWeight: 600,
                  color: c.level === "fail" ? "#ef5350" : c.level === "AAA" ? "#66bb6a" : "#ffa726",
                  width: 48,
                  textAlign: "right",
                }}
              >
                {c.ratio ? `${c.ratio.toFixed(1)}:1` : "—"}{" "}
                <span style={{ fontSize: 8 }}>{c.level}</span>
              </span>
            </div>
          ))}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.4 }}>
          WCAG AA requires 4.5:1 for normal text. AAA requires 7:1.
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {onApplyOverrides && (
          <button
            onClick={handleApplyToProject}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "6px 12px",
              borderRadius: 4,
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            <RefreshCw size={14} /> Apply to Project
          </button>
        )}
        <button
          onClick={handleSaveAsCustom}
          disabled={saving || !themeName.trim()}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "6px 12px",
            borderRadius: 4,
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-primary)",
            opacity: saving ? 0.6 : 1,
          }}
        >
          <Save size={14} /> Save as Custom
        </button>
        <button
          onClick={handleExport}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "6px 12px",
            borderRadius: 4,
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-primary)",
          }}
        >
          <Download size={14} /> Export .avctheme
        </button>
        <button
          onClick={() => fileInputRef.current?.click()}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "6px 12px",
            borderRadius: 4,
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-primary)",
          }}
        >
          <Upload size={14} /> Import
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".avctheme,.json"
          style={{ display: "none" }}
          onChange={handleImport}
        />
      </div>

      {/* Status messages */}
      {saveMsg && (
        <div
          style={{
            fontSize: 11,
            color: saveMsg.startsWith("Error") ? "#ef5350" : "#66bb6a",
            padding: "4px 8px",
          }}
        >
          {saveMsg}
        </div>
      )}
      {importMsg && (
        <div
          style={{
            fontSize: 11,
            color: importMsg.startsWith("Error") ? "#ef5350" : "#66bb6a",
            padding: "4px 8px",
          }}
        >
          {importMsg}
        </div>
      )}

      {isBuiltIn && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            background: "var(--bg-surface)",
            padding: "8px 12px",
            borderRadius: 4,
            lineHeight: 1.4,
          }}
        >
          This is a built-in theme. Use "Save as Custom" to create an editable copy with your changes.
        </div>
      )}
    </div>
  );
}
