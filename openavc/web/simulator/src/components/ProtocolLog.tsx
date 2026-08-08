import { useEffect, useRef, useState } from "react";
import { Terminal, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import type { LogEntry } from "../store/api";

interface Props {
  entries: LogEntry[];
  onClear: () => void;
}

export function ProtocolLog({ entries, onClear }: Props) {
  const [open, setOpen] = useState(true);
  const [filter, setFilter] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  // Auto-scroll to bottom unless user has scrolled up
  useEffect(() => {
    if (autoScrollRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 40;
  };

  const filtered = filter
    ? entries.filter((e) => e.device_id.includes(filter))
    : entries;

  return (
    <div className="protocol-log">
      <div className="protocol-log-header" onClick={() => setOpen(!open)}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <Terminal size={14} />
          Protocol Log
          <span style={{ color: "var(--text-muted)" }}>({entries.length})</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }} onClick={(e) => e.stopPropagation()}>
          <input
            placeholder="Filter by device..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ width: 140, fontSize: 11, padding: "2px 6px" }}
          />
          <button
            onClick={onClear}
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)" }}
          >
            <Trash2 size={12} /> Clear
          </button>
        </div>
      </div>
      {open && (
        <div className="protocol-log-content" ref={scrollRef} onScroll={handleScroll}>
          {filtered.map((entry, i) => {
            const time = new Date(entry.timestamp * 1000);
            const timeStr = time.toLocaleTimeString("en-US", {
              hour12: false,
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }) + "." + String(time.getMilliseconds()).padStart(3, "0");

            return (
              <div key={i} className="log-entry">
                <span className="log-time">{timeStr}</span>
                <span className="log-device">{entry.device_id}</span>
                <span className={`log-dir ${entry.direction}`}>
                  {entry.direction === "in" ? "\u2190" : "\u2192"}
                </span>
                <span className="log-data">{entry.data_text || entry.data}</span>
              </div>
            );
          })}
          {filtered.length === 0 && (
            <div style={{ padding: "16px 14px", color: "var(--text-muted)", fontStyle: "italic", textAlign: "center" }}>
              {entries.length === 0 ? "No protocol traffic yet" : "No entries match filter"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
