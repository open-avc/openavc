/** A command's `confirm` field in the Driver Builder: whether someone sending
 *  the command by hand is asked first, and with what sentence. The question
 *  itself is asked by Live Test, the device page and a device audit, worded by
 *  `commandConfirmMessage` (components/shared/commandParams.ts); macros,
 *  triggers and panel buttons never ask. */

import { FIELD_LABEL, FlagField } from "./commandFieldParts";

/** Ticked is `true`, a message makes it that sentence, unticked removes it. */
export function CommandConfirmField({
  confirm,
  onChange,
}: {
  confirm: boolean | string | undefined;
  onChange: (confirm: boolean | string | undefined) => void;
}) {
  const enabled = confirm === true || (typeof confirm === "string" && confirm !== "");
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <FlagField
        checked={enabled}
        onChange={(checked) => onChange(checked ? true : undefined)}
        title="Ask before sending"
      >
        Tick this for a command that erases or resets the device. Someone
        sending it from the device page, Live Test or a device audit is
        asked first. Macros, triggers and panel buttons send it without
        asking.
      </FlagField>
      {enabled && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <label style={FIELD_LABEL}>Confirmation Message (optional)</label>
          <input
            value={typeof confirm === "string" ? confirm : ""}
            onChange={(e) => onChange(e.target.value || true)}
            placeholder="Leave blank for the generic prompt"
            style={{ width: "100%" }}
          />
        </div>
      )}
    </div>
  );
}
