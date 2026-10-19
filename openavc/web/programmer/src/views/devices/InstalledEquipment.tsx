import { useState } from "react";
import * as api from "../../api/restClient";
import { parseApiError } from "../../api/errors";
import { useProjectStore } from "../../store/projectStore";

export function InstalledEquipment({
  deviceId,
  name,
  completionWait,
}: {
  deviceId: string;
  name: string;
  completionWait?: { default: number; max: number };
}) {
  const [label, setLabel] = useState(name);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const model = useProjectStore((s) => s.project?.devices.find((d) => d.id === deviceId)?.config?.integration);
  const savedWait = useProjectStore((s) => s.project?.devices.find((d) => d.id === deviceId)?.config?.completion_wait);
  const [wait, setWait] = useState(typeof savedWait === "number" ? String(savedWait) : "");
  const [waitMessage, setWaitMessage] = useState("");
  const [savingWait, setSavingWait] = useState(false);
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
    {completionWait && <>
    <form onSubmit={async (event) => {
      event.preventDefault(); setWaitMessage("");
      const text = wait.trim();
      const seconds = Number(text);
      if (text && (!Number.isFinite(seconds) || seconds < 0 || seconds > completionWait.max)) {
        setWaitMessage(`Enter a number of seconds from 0 to ${completionWait.max}, or leave it empty.`);
        return;
      }
      setSavingWait(true);
      try {
        await api.updateDevice(deviceId, {
          config: text ? { integration: model, completion_wait: seconds } : { integration: model },
        });
        await useProjectStore.getState().load();
        setWaitMessage("Wait saved.");
      } catch (error) { setWaitMessage(parseApiError(error)); }
      finally { setSavingWait(false); }
    }} style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
      <label>Wait for completion (seconds) <input aria-label="Wait for completion (seconds)" type="number" min={0} max={completionWait.max} step="any" placeholder={String(completionWait.default)} value={wait} onChange={e => setWait(e.target.value)} style={{ width: 90 }} /></label>
      <button type="submit" disabled={savingWait}>{savingWait ? "Saving..." : "Save wait"}</button>
      <span role="status">{waitMessage}</span>
    </form>
    <p style={{ margin: "6px 0 0", color: "var(--text-secondary)" }}>How long a command waits for the product to confirm it finished. A command that takes longer is reported as not confirmed. Leave it empty for {completionWait.default} seconds.</p>
    </>}
  </section>;
}
