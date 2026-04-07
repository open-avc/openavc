import { useState, useEffect, useRef } from "react";
import { Wifi } from "lucide-react";
import { setNetworkPreset, fetchNetwork } from "../store/api";

const PRESET_LABELS: Record<string, string> = {
  perfect: "Perfect",
  typical_lan: "Typical LAN",
  busy_network: "Busy Network",
  flaky_wifi: "Flaky WiFi",
  barely_working: "Barely Working",
};

export function NetworkConditions() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("perfect");
  const ref = useRef<HTMLDivElement>(null);

  // Load current preset on mount
  useEffect(() => {
    fetchNetwork()
      .then((data) => {
        // Determine which preset matches current settings
        if (data.global.latency_ms === 0) setActive("perfect");
        else if (data.global.latency_ms <= 5) setActive("typical_lan");
        else if (data.global.latency_ms <= 100) setActive("busy_network");
        else if (data.global.latency_ms <= 500) setActive("flaky_wifi");
        else setActive("barely_working");
      })
      .catch(() => {});
  }, []);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const handleSelect = async (preset: string) => {
    setActive(preset);
    setOpen(false);
    await setNetworkPreset(preset);
  };

  return (
    <div className="network-dropdown" ref={ref}>
      <button className="network-btn" onClick={() => setOpen(!open)}>
        <Wifi size={14} />
        {PRESET_LABELS[active] || active}
      </button>
      {open && (
        <div className="network-menu">
          {Object.entries(PRESET_LABELS).map(([key, label]) => (
            <button
              key={key}
              className={active === key ? "active" : ""}
              onClick={() => handleSelect(key)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
