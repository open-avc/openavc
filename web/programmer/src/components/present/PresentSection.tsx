import { useState, useEffect, useCallback } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Plus, Trash2, Pencil, RefreshCw, ExternalLink, KeyRound } from "lucide-react";
import { Dialog } from "../shared/Dialog";
import { ConfirmDialog } from "../shared/ConfirmDialog";
import { CopyButton } from "../shared/CopyButton";
import { showError, showSuccess } from "../../store/toastStore";
import { useProjectStore } from "../../store/projectStore";
import * as presentApi from "../../api/presentClient";
import type { Room } from "../../api/presentClient";

// Room add/edit/delete persists into the project's plugin config server-side,
// outside the project store's save path. Re-sync the project store afterward so
// its in-memory project + ETag track the server and a later UI Builder save
// can't overwrite the room list. No-op when there are unsaved edits; the
// server-side revision bump + 409 guard covers that case.
async function syncProjectStore() {
  await useProjectStore.getState().load();
}

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 4,
};

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "5px 8px",
  fontSize: "var(--font-size-sm)",
  borderRadius: "var(--border-radius)",
  border: "1px solid var(--border-color)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
};

const primaryBtn: CSSProperties = {
  padding: "6px 16px",
  borderRadius: "var(--border-radius)",
  background: "var(--accent-bg)",
  color: "#fff",
  border: "none",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};

const secondaryBtn: CSSProperties = {
  padding: "6px 16px",
  borderRadius: "var(--border-radius)",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  border: "none",
  cursor: "pointer",
  fontSize: "var(--font-size-sm)",
};

const iconBtnStyle: CSSProperties = {
  display: "flex",
  padding: 6,
  borderRadius: "var(--border-radius)",
  background: "transparent",
  border: "none",
  color: "var(--text-muted)",
  cursor: "pointer",
};

function slugify(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return s || "room";
}

// The plugin returns a site-relative Display URL (key included); the copyable
// link needs the absolute form a separate display device can open.
function displayUrl(room: Room): string {
  return `${window.location.origin}${room.display_path}`;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <label style={labelStyle}>{label}</label>
      {children}
    </div>
  );
}

function RoomForm({
  room,
  onClose,
  onSaved,
}: {
  room: Room | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState(room?.label ?? "");
  const [roomId, setRoomId] = useState(room?.id ?? "");
  const [roomIdTouched, setRoomIdTouched] = useState(!!room);
  const [saving, setSaving] = useState(false);

  const effectiveId = (roomIdTouched ? roomId : slugify(label)).trim();
  const valid = label.trim() && effectiveId;

  async function save() {
    if (!valid) return;
    setSaving(true);
    try {
      const payload = { label: label.trim(), room_id: effectiveId };
      if (room) await presentApi.updateRoom(room.id, payload);
      else await presentApi.createRoom(payload);
      showSuccess(room ? "Room updated." : "Room added.");
      onSaved();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog title={room ? "Edit Room" : "Add Room"} onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
        <Field label="Name">
          <input
            style={inputStyle}
            value={label}
            placeholder="Main Boardroom"
            onChange={(e) => setLabel(e.target.value)}
            autoFocus
          />
        </Field>
        <Field label="Room ID">
          <input
            style={inputStyle}
            value={effectiveId}
            onChange={(e) => {
              setRoomIdTouched(true);
              setRoomId(e.target.value);
            }}
          />
        </Field>
        {room && effectiveId !== room.id && (
          <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--color-warning, #b26a00)" }}>
            Changing the room ID changes the room&apos;s display link. Any display
            already using the old link will need the new one.
          </p>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-sm)", marginTop: "var(--space-lg)" }}>
        <button style={secondaryBtn} onClick={onClose}>
          Cancel
        </button>
        <button style={{ ...primaryBtn, opacity: valid && !saving ? 1 : 0.5 }} onClick={save} disabled={!valid || saving}>
          {saving ? "Saving..." : room ? "Save" : "Add Room"}
        </button>
      </div>
    </Dialog>
  );
}

export function PresentSection() {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Room | "new" | null>(null);
  const [deleting, setDeleting] = useState<Room | null>(null);
  const [rekeying, setRekeying] = useState<Room | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setRooms(await presentApi.listRooms());
    } catch (err) {
      showError(presentApi.errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function confirmDelete() {
    if (!deleting) return;
    const r = deleting;
    setDeleting(null);
    try {
      await presentApi.deleteRoom(r.id);
      showSuccess(`Removed "${r.label}".`);
      refresh();
      syncProjectStore();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    }
  }

  async function confirmRekey() {
    if (!rekeying) return;
    const r = rekeying;
    setRekeying(null);
    try {
      await presentApi.regenerateKey(r.id);
      showSuccess(`New display link created for "${r.label}".`);
      refresh();
      syncProjectStore();
    } catch (err) {
      showError(presentApi.errorMessage(err));
    }
  }

  return (
    <div style={{ marginTop: "var(--space-2xl)", maxWidth: 600 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-md)" }}>
        <h3 style={{ fontSize: "var(--font-size-base)", color: "var(--text-secondary)", margin: 0 }}>Present</h3>
        <div style={{ display: "flex", gap: "var(--space-sm)" }}>
          <button
            onClick={refresh}
            title="Refresh"
            style={{ display: "flex", padding: 6, borderRadius: "var(--border-radius)", background: "var(--bg-hover)", border: "none", color: "var(--text-secondary)", cursor: "pointer" }}
          >
            <RefreshCw size={15} />
          </button>
          <button
            onClick={() => setEditing("new")}
            style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)", ...primaryBtn, padding: "var(--space-xs) var(--space-md)" }}
          >
            <Plus size={15} /> Add Room
          </button>
        </div>
      </div>

      <div style={{ background: "var(--bg-surface)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", overflow: "hidden" }}>
        {loading ? (
          <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>Loading rooms...</div>
        ) : rooms.length === 0 ? (
          <div style={{ padding: "var(--space-lg)", color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            No rooms yet. Add a room, then open its display link on the device
            driving the room display.
          </div>
        ) : (
          rooms.map((r, i) => (
            <div
              key={r.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-md)",
                padding: "var(--space-sm) var(--space-md)",
                borderTop: i === 0 ? "none" : "1px solid var(--border-color)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-primary)" }}>{r.label}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.id}</span>
                  <CopyButton value={displayUrl(r)} title="Copy display link" />
                </div>
              </div>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: r.output_state === "live" ? "var(--color-success, #2e7d32)" : "var(--text-muted)",
                }}
              >
                {r.output_state === "live" ? "Live" : "Idle"}
              </span>
              <button
                onClick={() => window.open(displayUrl(r), "_blank", "noopener")}
                title="Open display page"
                style={iconBtnStyle}
              >
                <ExternalLink size={15} />
              </button>
              <button onClick={() => setRekeying(r)} title="Regenerate display link" style={iconBtnStyle}>
                <KeyRound size={15} />
              </button>
              <button onClick={() => setEditing(r)} title="Edit" style={iconBtnStyle}>
                <Pencil size={15} />
              </button>
              <button onClick={() => setDeleting(r)} title="Remove" style={iconBtnStyle}>
                <Trash2 size={15} />
              </button>
            </div>
          ))
        )}
      </div>

      <p style={{ marginTop: "var(--space-md)", fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: 1.5 }}>
        Each room has a display link that shows the connect card and the live
        presenter. Copy it with the icon next to the room ID and open it, full
        screen, in a browser on the device driving the room display. The link
        includes the room&apos;s display key — treat it like a password.
      </p>

      {editing && (
        <RoomForm
          room={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
            syncProjectStore();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="Remove room"
          message={`Remove "${deleting.label}"? Its display link will stop working.`}
          confirmLabel="Remove"
          destructive
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
      {rekeying && (
        <ConfirmDialog
          title="Regenerate display link"
          message={`Create a new display link for "${rekeying.label}"? Every copy of the current link stops working immediately, including any display that is using it right now.`}
          confirmLabel="Regenerate"
          destructive
          onConfirm={confirmRekey}
          onCancel={() => setRekeying(null)}
        />
      )}
    </div>
  );
}
