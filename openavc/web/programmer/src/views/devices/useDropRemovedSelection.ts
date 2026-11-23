import { useEffect, useRef } from "react";

/**
 * Let go of the open device when it leaves the loaded project.
 *
 * Something other than this tab can remove the device being shown: a second
 * Programmer tab, the AI assistant, a cloud config push. The project reloads
 * without it, and the page would go on asking the server for a device it no
 * longer has. `drop` runs when the selected device was in the project last
 * time it was loaded and is not now. A selection made before the project
 * arrived, or of a device the project never listed, is left alone.
 */
export function useDropRemovedSelection(
  selectedId: string | null,
  devices: readonly { id: string }[] | undefined,
  drop: () => void,
): void {
  const lastLoaded = useRef<ReadonlySet<string> | null>(null);
  useEffect(() => {
    if (!devices) return;
    const now = new Set(devices.map((device) => device.id));
    if (selectedId && lastLoaded.current?.has(selectedId) && !now.has(selectedId)) drop();
    lastLoaded.current = now;
    // Asked again only when the project's devices change; the selection is read as it is then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devices]);
}
