import { useState } from "react";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import { useProjectStore } from "../../store/projectStore";

export function InstalledEquipment({ deviceId, name }: { deviceId: string; name: string }) {
  const [label, setLabel] = useState(name);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  return <section aria-label="Product hardware" style={{ padding: 14, background: "var(--bg-secondary)", borderRadius: 8 }}>
    <strong>Product hardware</strong>
    <p style={{ margin: "6px 0 12px", color: "var(--text-secondary)" }}>The hardware of the product running this OpenAVC server. It is available in every project and cannot be removed. Disable stops OpenAVC control in this project; the product keeps operating.</p>
    <form onSubmit={async (event) => {
      event.preventDefault(); setSaving(true); setMessage("");
      try {
        await api.updateDevice(deviceId, { name: label });
        await useProjectStore.getState().load();
        setMessage("Label saved.");
      } catch (error) { setMessage(parseApiError(error)); }
      finally { setSaving(false); }
    }} style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <label>Project label <input aria-label="Project label" value={label} onChange={e => setLabel(e.target.value)} maxLength={128} /></label>
      <button type="submit" disabled={saving || !label.trim()}>{saving ? "Saving..." : "Save label"}</button>
      <span role="status">{message}</span>
    </form>
  </section>;
}
