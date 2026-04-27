import { useState } from "react";
import { Plus, Trash2, Layers } from "lucide-react";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { useProjectStore } from "../../store/projectStore";
import type { DeviceGroup } from "../../api/types";

export function DeviceGroupsPanel() {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);

  const groups = project?.device_groups ?? [];
  const devices = project?.devices ?? [];

  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const selectedGroup = groups.find((g) => g.id === selectedGroupId);

  // Auto-generate ID from display name
  const autoGroupId = newGroupName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

  const handleCreate = () => {
    const id = autoGroupId;
    if (!id || groups.some((g) => g.id === id)) return;
    const newGroup: DeviceGroup = {
      id,
      name: newGroupName.trim(),
      device_ids: [],
    };
    update({ device_groups: [...groups, newGroup] });
    setNewGroupName("");
    setShowCreate(false);
    setSelectedGroupId(id);
    useProjectStore.getState().debouncedSave();
  };

  const handleDelete = (groupId: string) => {
    const group = groups.find((g) => g.id === groupId);
    updateWithUndo({ device_groups: groups.filter((g) => g.id !== groupId) }, `Delete group "${group?.name || groupId}"`);
    if (selectedGroupId === groupId) setSelectedGroupId(null);
    setDeleteConfirm(null);
    useProjectStore.getState().debouncedSave();
  };

  const handleUpdateGroup = (groupId: string, patch: Partial<DeviceGroup>) => {
    update({
      device_groups: groups.map((g) =>
        g.id === groupId ? { ...g, ...patch } : g
      ),
    });
    useProjectStore.getState().debouncedSave();
  };

  const toggleDevice = (groupId: string, deviceId: string) => {
    const group = groups.find((g) => g.id === groupId);
    if (!group) return;
    const ids = group.device_ids.includes(deviceId)
      ? group.device_ids.filter((d) => d !== deviceId)
      : [...group.device_ids, deviceId];
    handleUpdateGroup(groupId, { device_ids: ids });
  };

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* Left: group list */}
      <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid var(--border-color)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-color)" }}>
          <button
            onClick={() => setShowCreate((v) => !v)}
            style={{
              display: "flex", alignItems: "center", gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-sm)",
              background: "var(--accent-bg)", color: "var(--text-on-accent)",
              border: "none", borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-sm)", cursor: "pointer", fontWeight: 500,
            }}
          >
            <Plus size={14} /> New Group
          </button>
        </div>

        {showCreate && (
          <div style={{ padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            <input
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              placeholder="Group name (e.g., All Projectors)"
              style={{ fontSize: "var(--font-size-sm)", padding: "var(--space-xs) var(--space-sm)", borderRadius: "var(--border-radius)", border: "1px solid var(--border-color)", background: "var(--bg-input)", color: "var(--text-primary)" }}
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
            {autoGroupId && (
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                ID: <code style={{ fontFamily: "var(--font-mono)" }}>{autoGroupId}</code>
                {groups.some((g) => g.id === autoGroupId) && (
                  <span style={{ color: "var(--color-error, #ef4444)", marginLeft: 6 }}>Already exists</span>
                )}
              </div>
            )}
            <div style={{ display: "flex", gap: "var(--space-xs)" }}>
              <button onClick={handleCreate} disabled={!autoGroupId || groups.some((g) => g.id === autoGroupId)} style={{ padding: "var(--space-xs) var(--space-sm)", background: "var(--accent-bg)", color: "var(--text-on-accent)", border: "none", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-sm)", cursor: "pointer", opacity: !autoGroupId || groups.some((g) => g.id === autoGroupId) ? 0.5 : 1 }}>Create</button>
              <button onClick={() => { setShowCreate(false); setNewGroupName(""); }} style={{ padding: "var(--space-xs) var(--space-sm)", background: "var(--bg-hover)", color: "var(--text-secondary)", border: "none", borderRadius: "var(--border-radius)", fontSize: "var(--font-size-sm)", cursor: "pointer" }}>Cancel</button>
            </div>
          </div>
        )}

        <div style={{ flex: 1, overflow: "auto" }}>
          {groups.length === 0 ? (
            <div style={{ padding: "var(--space-xl)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--font-size-sm)", lineHeight: 1.6 }}>
              <Layers size={32} style={{ opacity: 0.3, marginBottom: "var(--space-sm)" }} />
              <div style={{ fontWeight: 500, color: "var(--text-secondary)", marginBottom: "var(--space-sm)" }}>No groups yet</div>
              <div>
                Groups let you control multiple devices at once.
                Create a "Projectors" group to power them all on with one macro step.
              </div>
              <button
                onClick={() => setShowCreate(true)}
                style={{
                  marginTop: "var(--space-md)", padding: "var(--space-xs) var(--space-md)",
                  background: "var(--accent-bg)", color: "var(--text-on-accent)",
                  border: "none", borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-sm)", cursor: "pointer", fontWeight: 500,
                }}
              >
                Create your first group
              </button>
            </div>
          ) : (
            groups.map((g) => (
              <div
                key={g.id}
                onClick={() => setSelectedGroupId(g.id)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "var(--space-sm) var(--space-md)",
                  cursor: "pointer",
                  background: selectedGroupId === g.id ? "var(--bg-hover)" : "transparent",
                  borderBottom: "1px solid var(--border-color)",
                }}
                onMouseEnter={(e) => { if (selectedGroupId !== g.id) (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
                onMouseLeave={(e) => { if (selectedGroupId !== g.id) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
                    <Layers size={14} style={{ color: "var(--accent)" }} />
                    <span style={{ fontWeight: selectedGroupId === g.id ? 600 : 400, color: "var(--text-primary)" }}>{g.name}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 1 }}>
                    {g.device_ids.length} device{g.device_ids.length !== 1 ? "s" : ""}
                  </div>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); setDeleteConfirm(g.id); }}
                  style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 2 }}
                  title="Delete group"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Right: group detail */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-lg)" }}>
        {selectedGroup ? (
          <div>
            <div style={{ marginBottom: "var(--space-lg)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: "var(--space-sm)" }}>
                <Layers size={20} style={{ color: "var(--accent)" }} />
                <input
                  value={selectedGroup.name}
                  onChange={(e) => handleUpdateGroup(selectedGroup.id, { name: e.target.value })}
                  style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, background: "transparent", border: "none", color: "var(--text-primary)", outline: "none", padding: 0 }}
                />
              </div>
              <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
                ID: <code style={{ background: "var(--bg-hover)", padding: "1px 4px", borderRadius: 3 }}>{selectedGroup.id}</code>
              </div>
            </div>

            <h3 style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "var(--space-sm)" }}>
              Devices ({selectedGroup.device_ids.length})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {devices.map((dev) => {
                const isMember = selectedGroup.device_ids.includes(dev.id);
                return (
                  <label
                    key={dev.id}
                    style={{
                      display: "flex", alignItems: "center", gap: "var(--space-sm)",
                      padding: "var(--space-xs) var(--space-sm)",
                      borderRadius: "var(--border-radius)",
                      cursor: "pointer",
                      background: isMember ? "rgba(138,180,147,0.08)" : "transparent",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isMember}
                      onChange={() => toggleDevice(selectedGroup.id, dev.id)}
                    />
                    <span style={{ fontWeight: isMember ? 500 : 400, color: "var(--text-primary)" }}>{dev.name}</span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>({dev.id})</span>
                  </label>
                );
              })}
              {devices.length === 0 && (
                <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", fontStyle: "italic" }}>
                  No devices in the project yet.
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", gap: "var(--space-sm)", textAlign: "center" }}>
            <Layers size={32} style={{ opacity: 0.3 }} />
            <div style={{ fontSize: "var(--font-size-md)" }}>
              {groups.length === 0 ? "Create your first device group" : "Select a group to manage its devices"}
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", maxWidth: 420, lineHeight: 1.5 }}>
              Device groups let you target multiple devices with a single macro step.
              Create a group, add devices to it, then use "Group Command" in your macros.
            </div>
          </div>
        )}
      </div>

      {deleteConfirm && (
        <ConfirmDialog
          title="Delete Group"
          message={`Delete group "${groups.find((g) => g.id === deleteConfirm)?.name ?? deleteConfirm}"? This will not delete any devices.`}
          confirmLabel="Delete"
          onConfirm={() => handleDelete(deleteConfirm)}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </div>
  );
}
