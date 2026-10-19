import { useCallback, useEffect, useState } from "react";
import { recoverSetupTransfer, type RetainedSetup } from "../../api/setupClient";
import { parseApiError } from "../../api/errors";
import { Dialog } from "../../components/shared/Dialog";
import { SetupActionWizard } from "./SetupActionWizard";

/** Refresh can recover an old page's inputs, but never starts its device work. */
export function RetainedSetupHost() {
  const [draft, setDraft] = useState<RetainedSetup | null>(null);
  const [error, setError] = useState("");
  const [generation, setGeneration] = useState(0);
  const retry = useCallback(() => { setError(""); setGeneration((n) => n + 1); }, []);
  useEffect(() => {
    let active = true;
    const recover = async () => {
      try {
        // The transport can deliver one Set-Cookie header at a time. Clean at
        // most its finite cookie bound, without dropping an active draft.
        for (let count = 0; count <= 8 && active; count++) {
          const result = await recoverSetupTransfer();
          if (!active) return;
          if (result.pending || !result.cleared) { setDraft(result.pending); return; }
        }
      } catch (e) { if (active) setError(parseApiError(e)); }
    };
    void recover();
    return () => { active = false; };
  }, [generation]);
  if (draft) return <SetupActionWizard key={draft.run_id} deviceId={draft.device_id}
    action={draft.action} retained={draft} onClose={() => setDraft(null)} />;
  if (error) return <Dialog title="Continue setup" onClose={() => setError("")}>
    <p role="alert">{error}</p><button onClick={retry}>Check retained inputs</button>
  </Dialog>;
  return null;
}
