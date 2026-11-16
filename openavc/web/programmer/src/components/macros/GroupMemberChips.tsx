import { AlertTriangle, CheckCircle, XCircle } from "lucide-react";
import type { GroupCommandResult } from "../../store/logStore";
import { memberTitle, memberTone, type MemberTone } from "./groupMemberLook";

/** A member chip's look, by how far the command got (groupMemberLook.ts). */
const MEMBER_LOOKS: Record<MemberTone, { Icon: typeof CheckCircle; color: string; background: string }> = {
  done: { Icon: CheckCircle, color: "#10b981", background: "rgba(16,185,129,0.12)" },
  reached: { Icon: AlertTriangle, color: "#f59e0b", background: "rgba(245,158,11,0.12)" },
  failed: { Icon: XCircle, color: "#ef4444", background: "rgba(239,68,68,0.12)" },
};

/** A group command step's per-device results, one chip per member. */
export function GroupMemberChips({ groupResult }: { groupResult: GroupCommandResult }) {
  return (
    <div
      style={{
        padding: "var(--space-xs) var(--space-md)",
        fontSize: 12,
        borderTop: "1px solid var(--border-color)",
        display: "flex",
        flexWrap: "wrap",
        gap: "var(--space-xs)",
      }}
    >
      {groupResult.deviceResults.map((dr) => {
        const look = MEMBER_LOOKS[memberTone(dr)];
        return (
          <span
            key={dr.device_id}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
              padding: "1px 6px",
              borderRadius: 3,
              fontSize: 11,
              background: look.background,
              color: look.color,
            }}
            title={memberTitle(dr)}
          >
            <look.Icon size={10} />
            {dr.name}
          </span>
        );
      })}
    </div>
  );
}
