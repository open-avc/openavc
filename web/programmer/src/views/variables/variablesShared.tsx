import { useState } from "react";
import { ChevronRight, ExternalLink, X, Zap, Layout, FileCode } from "lucide-react";
import { useNavigationStore, type FocusTarget } from "../../store/navigationStore";
import type { ProjectConfig, ScriptReference } from "../../api/types";
import type { ViewId } from "../../components/layout/Sidebar";

// ==========================================================================
// Shared types
// ==========================================================================

export interface VariableUsage {
  type: "macro" | "ui" | "script";
  icon: typeof Zap;
  label: string;
  detail: string;
  /** Navigation target when clicked */
  nav?: { view: ViewId; focus: FocusTarget };
}

// ==========================================================================
// Shared components
// ==========================================================================

export function HelpBanner({ storageKey, children }: { storageKey: string; children: React.ReactNode }) {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(storageKey) === "1");
  if (dismissed) return null;
  return (
    <div style={helpBannerStyle}>
      <div style={{ flex: 1, lineHeight: 1.5 }}>{children}</div>
      <button
        onClick={() => { setDismissed(true); localStorage.setItem(storageKey, "1"); }}
        style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 2, flexShrink: 0 }}
        title="Dismiss"
      >
        <X size={14} />
      </button>
    </div>
  );
}

export function UsageRow({ usage }: { usage: VariableUsage }) {
  const navigateTo = useNavigationStore((s) => s.navigateTo);
  const hasNav = !!usage.nav;
  const typeLabel = usage.type === "macro" ? "Macro" : usage.type === "ui" ? "UI" : "Script";

  return (
    <div
      onClick={hasNav ? () => navigateTo(usage.nav!.view, usage.nav!.focus) : undefined}
      style={{
        ...usageRowStyle,
        cursor: hasNav ? "pointer" : "default",
      }}
      onMouseEnter={hasNav ? (e) => (e.currentTarget.style.background = "var(--bg-hover)") : undefined}
      onMouseLeave={hasNav ? (e) => (e.currentTarget.style.background = "var(--bg-surface)") : undefined}
      title={hasNav ? `Jump to ${typeLabel}` : undefined}
    >
      <usage.icon size={14} style={{ color: usageColor(usage.type), flexShrink: 0 }} />
      <span style={{ color: usageColor(usage.type), fontWeight: 500, flexShrink: 0 }}>
        {typeLabel}
      </span>
      <span style={{ color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{usage.label}</span>
      <ChevronRight size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      <span style={{ color: "var(--text-secondary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{usage.detail}</span>
      {hasNav && (
        <ExternalLink size={12} style={{ color: "var(--text-muted)", flexShrink: 0, opacity: 0.6 }} />
      )}
    </div>
  );
}

// ==========================================================================
// Cross-reference logic
// ==========================================================================

/** Simple glob matcher for patterns like "device.*.power" */
function globMatch(pattern: string, key: string): boolean {
  if (pattern === key) return true;
  if (!pattern.includes("*")) return false;
  const regex = new RegExp("^" + pattern.replace(/\./g, "\\.").replace(/\*/g, "[^.]+") + "$");
  return regex.test(key);
}

/** Build usage map for var.* keys only (used in Variables sub-tab) */
export function buildUsageMap(project: ProjectConfig, scriptRefs: ScriptReference[] = []): Map<string, VariableUsage[]> {
  const map = new Map<string, VariableUsage[]>();

  const addUsage = (varId: string, usage: VariableUsage) => {
    const list = map.get(varId) ?? [];
    list.push(usage);
    map.set(varId, list);
  };

  for (const macro of project.macros) {
    const macroNav = { view: "macros" as ViewId, focus: { type: "macro", id: macro.id } };
    for (const step of macro.steps) {
      if (step.action === "state.set" && step.key?.startsWith("var.")) {
        addUsage(step.key.slice(4), {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Set Variable step → ${JSON.stringify(step.value)}`,
          nav: macroNav,
        });
      }
    }
    for (const trigger of macro.triggers ?? []) {
      if (trigger.state_key?.startsWith("var.")) {
        addUsage(trigger.state_key.slice(4), {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Trigger "${trigger.id}" — state change on this variable`,
          nav: macroNav,
        });
      }
      for (const cond of trigger.conditions ?? []) {
        if (cond.key?.startsWith("var.")) {
          addUsage(cond.key.slice(4), {
            type: "macro", icon: Zap, label: macro.name,
            detail: `Trigger "${trigger.id}" — guard condition`,
            nav: macroNav,
          });
        }
      }
    }
  }

  for (const page of project.ui.pages) {
    for (const el of page.elements) {
      const elNav = { view: "ui-builder" as ViewId, focus: { type: "element", id: el.id, detail: `page:${page.id}` } };
      scanBindingForVars(el.bindings, (varId, detail) => {
        addUsage(varId, {
          type: "ui", icon: Layout,
          label: `${page.name} → ${el.label || el.type} (${el.id})`,
          detail,
          nav: elNav,
        });
      });
    }
  }

  // Script references
  for (const ref of scriptRefs) {
    if (!ref.key.startsWith("var.")) continue;
    const varId = ref.key.slice(4);
    const usageLabel = ref.usage_type === "subscribe" ? "@on_state_change" : ref.usage_type === "write" ? "state.set" : "state.get";
    addUsage(varId, {
      type: "script", icon: FileCode, label: ref.script_name,
      detail: `line ${ref.line} — ${usageLabel}`,
      nav: { view: "scripts", focus: { type: "script", id: ref.script_id, detail: `line:${ref.line}` } },
    });
  }

  return map;
}

/** Build usage map for ALL state keys (var.*, device.*, system.*) — used in Device States sub-tab */
export function buildStateUsageMap(project: ProjectConfig, scriptRefs: ScriptReference[] = []): Map<string, VariableUsage[]> {
  const map = new Map<string, VariableUsage[]>();

  const addUsage = (key: string, usage: VariableUsage) => {
    const list = map.get(key) ?? [];
    list.push(usage);
    map.set(key, list);
  };

  for (const macro of project.macros) {
    const macroNav = { view: "macros" as ViewId, focus: { type: "macro", id: macro.id } };
    for (const step of macro.steps) {
      if (step.action === "state.set" && step.key) {
        addUsage(step.key, {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Set Variable step → ${JSON.stringify(step.value)}`,
          nav: macroNav,
        });
      }
    }
    for (const trigger of macro.triggers ?? []) {
      if (trigger.state_key) {
        addUsage(trigger.state_key, {
          type: "macro", icon: Zap, label: macro.name,
          detail: `Trigger "${trigger.id}" — state change`,
          nav: macroNav,
        });
      }
      for (const cond of trigger.conditions ?? []) {
        if (cond.key) {
          addUsage(cond.key, {
            type: "macro", icon: Zap, label: macro.name,
            detail: `Trigger "${trigger.id}" — guard condition`,
            nav: macroNav,
          });
        }
      }
    }
  }

  for (const page of project.ui.pages) {
    for (const el of page.elements) {
      const elNav = { view: "ui-builder" as ViewId, focus: { type: "element", id: el.id, detail: `page:${page.id}` } };
      scanBindingForAllKeys(el.bindings, (key, detail) => {
        addUsage(key, {
          type: "ui", icon: Layout,
          label: `${page.name} → ${el.label || el.type} (${el.id})`,
          detail,
          nav: elNav,
        });
      });
    }
  }

  // Script references — match against all keys, supporting wildcards
  for (const ref of scriptRefs) {
    const usageLabel = ref.usage_type === "subscribe" ? "@on_state_change" : ref.usage_type === "write" ? "state.set" : "state.get";
    const scriptNav = { view: "scripts" as ViewId, focus: { type: "script", id: ref.script_id, detail: `line:${ref.line}` } };
    const entry: VariableUsage = {
      type: "script", icon: FileCode, label: ref.script_name,
      detail: `line ${ref.line} — ${usageLabel}`,
      nav: scriptNav,
    };
    if (ref.key.includes("*")) {
      // Wildcard pattern — add to all matching existing keys
      for (const existingKey of map.keys()) {
        if (globMatch(ref.key, existingKey)) {
          map.get(existingKey)!.push(entry);
        }
      }
    } else {
      addUsage(ref.key, entry);
    }
  }

  return map;
}

function scanBindingForVars(
  bindings: Record<string, unknown>,
  onFound: (varId: string, detail: string) => void,
) {
  if (!bindings) return;

  const checkKey = (obj: any, context: string) => {
    if (!obj || typeof obj !== "object") return;
    const key = obj.key as string | undefined;
    if (key?.startsWith("var.")) {
      onFound(key.slice(4), context);
    }
  };

  if (bindings.variable) checkKey(bindings.variable, "Two-way variable binding");
  if (bindings.text) checkKey(bindings.text, "Text display binding");
  if (bindings.feedback) checkKey(bindings.feedback, "Feedback/color binding");

  for (const eventType of ["press", "release", "change"]) {
    const binding = bindings[eventType] as Record<string, unknown> | undefined;
    if (!binding) continue;
    if (binding.action === "state.set" && typeof binding.key === "string" && binding.key.startsWith("var.")) {
      onFound(binding.key.slice(4), `${eventType} → Set Variable`);
    }
    if (binding.action === "value_map" && binding.map) {
      const actionMap = binding.map as Record<string, any>;
      for (const [optVal, subAction] of Object.entries(actionMap)) {
        if (subAction?.action === "state.set" && typeof subAction.key === "string" && subAction.key.startsWith("var.")) {
          onFound(subAction.key.slice(4), `${eventType} → ${optVal} → Set Variable`);
        }
      }
    }
  }

  if (bindings.value) checkKey(bindings.value, "Slider value source");
}

/** Scan bindings for ALL state key references (not just var.*) */
function scanBindingForAllKeys(
  bindings: Record<string, unknown>,
  onFound: (key: string, detail: string) => void,
) {
  if (!bindings) return;

  const checkKey = (obj: any, context: string) => {
    if (!obj || typeof obj !== "object") return;
    const key = obj.key as string | undefined;
    if (key) onFound(key, context);
  };

  if (bindings.variable) checkKey(bindings.variable, "Two-way binding");
  if (bindings.text) checkKey(bindings.text, "Text display binding");
  if (bindings.feedback) checkKey(bindings.feedback, "Feedback binding");
  if (bindings.color) checkKey(bindings.color, "Color binding");

  for (const eventType of ["press", "release", "change"]) {
    const binding = bindings[eventType] as Record<string, unknown> | undefined;
    if (!binding) continue;
    if (binding.action === "state.set" && typeof binding.key === "string") {
      onFound(binding.key, `${eventType} → Set state`);
    }
    if (binding.action === "value_map" && binding.map) {
      const actionMap = binding.map as Record<string, any>;
      for (const [optVal, subAction] of Object.entries(actionMap)) {
        if (subAction?.action === "state.set" && typeof subAction.key === "string") {
          onFound(subAction.key, `${eventType} → ${optVal} → Set state`);
        }
      }
    }
  }

  if (bindings.value) checkKey(bindings.value, "Slider value source");
}

export function usageColor(type: string): string {
  switch (type) {
    case "macro": return "#f59e0b";
    case "ui": return "#3b82f6";
    case "script": return "#10b981";
    default: return "var(--text-muted)";
  }
}

// ==========================================================================
// Shared styles
// ==========================================================================

export const subTabBarStyle: React.CSSProperties = {
  display: "flex",
  gap: 0,
  borderBottom: "1px solid var(--border-color)",
  flexShrink: 0,
};

export const subTabBtnStyle: React.CSSProperties = {
  padding: "var(--space-sm) var(--space-lg)",
  background: "none",
  border: "none",
  fontSize: "var(--font-size-sm)",
  cursor: "pointer",
  transition: "color 0.15s",
};

const helpBannerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--space-sm)",
  padding: "var(--space-sm) var(--space-md)",
  background: "rgba(33,150,243,0.08)",
  borderBottom: "1px solid rgba(33,150,243,0.15)",
  fontSize: 12,
  color: "var(--text-secondary)",
  lineHeight: 1.5,
  fontStyle: "italic",
};

export const headerBtnStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
  padding: "var(--space-xs) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--accent)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

export const searchInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-xs) var(--space-sm)",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
};

export const createFormStyle: React.CSSProperties = {
  padding: "var(--space-md)",
  borderBottom: "1px solid var(--border-color)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  background: "var(--bg-surface)",
};

export const miniLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  marginBottom: 2,
};

export const fieldInput: React.CSSProperties = {
  width: "100%",
  padding: "4px 6px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

export const btnPrimary: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent)",
  color: "#fff",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

export const btnSecondary: React.CSSProperties = {
  padding: "4px 14px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  fontSize: "var(--font-size-sm)",
  border: "none",
  cursor: "pointer",
};

export const codeStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-sm)",
};

export const typeBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  color: "var(--text-muted)",
  background: "var(--bg-hover)",
  padding: "0 5px",
  borderRadius: 3,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

export const iconBtn: React.CSSProperties = {
  display: "flex",
  padding: 4,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  color: "var(--text-muted)",
  border: "none",
  cursor: "pointer",
};

export const detailLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 4,
};

export const detailInput: React.CSSProperties = {
  width: "100%",
  padding: "4px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

export const sectionTitle: React.CSSProperties = {
  fontSize: "var(--font-size-sm)",
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  fontWeight: 600,
  marginBottom: "var(--space-md)",
};

const usageRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-sm)",
  padding: "var(--space-sm) var(--space-md)",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-surface)",
  border: "1px solid var(--border-color)",
  fontSize: "var(--font-size-sm)",
};
