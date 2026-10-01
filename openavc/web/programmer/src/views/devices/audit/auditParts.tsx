import { ArrowLeft } from "lucide-react";
import { useAuditStore } from "../../../store/auditStore";
import { AUDIT_STEPS, type AuditStep } from "./auditHelpers";
import { buttonStyle } from "./auditStyles";

/** Small pieces the audit wizard's steps share. */

export function ErrorLine({ text }: { text: string }) {
  return (
    <div
      role="alert"
      style={{
        marginBottom: "var(--space-md)",
        padding: "var(--space-sm) var(--space-md)",
        borderRadius: "var(--border-radius)",
        background: "var(--color-error-bg)",
        border: "1px solid var(--color-error)",
        color: "var(--text-primary)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      {text}
    </div>
  );
}

/** Back to an earlier step. The step rail shows where the audit is but does
 *  not move it, so each step that can go back carries this. */
export function BackButton({ to, disabled = false }: { to: AuditStep; disabled?: boolean }) {
  const label = AUDIT_STEPS.find((s) => s.key === to)?.label ?? "";
  return (
    <button
      type="button"
      onClick={() => useAuditStore.getState().setStep(to)}
      disabled={disabled}
      style={buttonStyle("muted", disabled)}
    >
      <ArrowLeft size={14} /> Back to {label}
    </button>
  );
}
