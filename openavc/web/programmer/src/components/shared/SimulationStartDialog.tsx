import { useState } from "react";
import type { DeviceConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { Modal } from "./Modal";
import styles from "../../styles/simulationStart.module.css";

const NO_DEVICES: DeviceConfig[] = [];

interface Props {
  onClose: () => void;
  onStart: (deviceIds: string[] | null) => void;
  opensSimulatorTab?: boolean;
}

/** Both starting doors ask which devices may leave their real connections.
 * null retains the server's all-devices mode, including later additions. */
export function SimulationStartDialog({ onClose, onStart, opensSimulatorTab = false }: Props) {
  const devices = useProjectStore((s) => s.project?.devices) ?? NO_DEVICES;
  // Keep the default "all" while the project is still loading. Once a person
  // changes a checkbox, retain that explicit choice as the device list updates.
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [search, setSearch] = useState("");
  const selectedIds = devices.filter((d) => selected === null || selected.has(d.id)).map((d) => d.id);
  const allSelected = devices.length > 0 && selectedIds.length === devices.length;
  const query = search.trim().toLowerCase();
  const visible = devices.filter((d) => `${d.name} ${d.id}`.toLowerCase().includes(query));

  return (
    <Modal onClose={onClose} label="Start Device Simulation" panelClassName={styles.dialog}>
      <h3>Start Device Simulation</h3>
      <p>Choose which devices to simulate. Unselected devices keep their real connections.</p>
      {devices.length > 0 ? (
        <>
          <input
            type="search"
            aria-label="Find devices"
            placeholder="Find devices"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className={styles.search}
          />
          <div className={styles.selectionActions}>
            <span>{selectedIds.length} of {devices.length} selected</span>
            <button type="button" onClick={() => setSelected(null)}>Select all</button>
            <button type="button" onClick={() => setSelected(new Set())}>Clear selection</button>
          </div>
          <div className={styles.devices}>
            {visible.map((device) => (
              <label key={device.id} className={styles.device}>
                <input
                  type="checkbox"
                  checked={selected === null || selected.has(device.id)}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setSelected((previous) => {
                      const next = new Set(previous ?? devices.map((d) => d.id));
                      if (checked) next.add(device.id);
                      else next.delete(device.id);
                      return next;
                    });
                  }}
                />
                <span>
                  <strong>{device.name || device.id}</strong>
                  <small>{device.id}{device.enabled === false ? " · Disabled" : ""}</small>
                </span>
              </label>
            ))}
            {visible.length === 0 && <p>No matching devices.</p>}
          </div>
          <p className={styles.additions}>
            {allSelected
              ? "New devices added while simulation is on will also be simulated."
              : "New devices added while simulation is on will use their real connections."}
          </p>
        </>
      ) : <p>Add a device to your project before starting simulation.</p>}
      <details className={styles.details}>
        <summary>About simulation</summary>
        <ul>
          <li>Selected devices disconnect from real equipment</li>
          <li>Only drivers with simulation support will respond</li>
          {opensSimulatorTab && <li>The Simulator UI opens in a new browser tab</li>}
          <li>Simulation stays on after a restart until you stop it</li>
          <li>Stop simulation to reconnect to real devices</li>
        </ul>
      </details>
      <div className={styles.actions}>
        <button type="button" onClick={onClose}>Cancel</button>
        <button
          type="button"
          className={styles.start}
          disabled={selectedIds.length === 0}
          onClick={() => { if (selectedIds.length) onStart(allSelected ? null : selectedIds); }}
        >Start Simulation</button>
      </div>
    </Modal>
  );
}
