/**
 * Suggested prompt cards — context-aware suggestions shown in the AI chat empty state
 * and as follow-up chips after responses.
 */

import { Search, LayoutGrid, HelpCircle, Play, Wifi, Wrench, Plus, Clock, Sparkles } from "lucide-react";
import { useProjectStore } from "../../store/projectStore";

interface PromptCard {
  icon: React.ReactNode;
  title: string;
  prompt: string;
}

function getPromptCards(): PromptCard[] {
  const project = useProjectStore.getState().project;

  const deviceCount = project?.devices?.length ?? 0;
  const macroCount = project?.macros?.length ?? 0;
  const pageCount = project?.ui?.pages?.length ?? 0;

  if (deviceCount === 0) {
    // Empty project
    return [
      { icon: <Search size={16} />, title: "Scan my network", prompt: "Scan my network for AV devices" },
      { icon: <LayoutGrid size={16} />, title: "Set up a room", prompt: "Help me set up a conference room" },
      { icon: <HelpCircle size={16} />, title: "What can you do?", prompt: "What can you help me with?" },
    ];
  }

  if (macroCount === 0) {
    // Has devices, no macros
    return [
      { icon: <Play size={16} />, title: "Create System On", prompt: "Create a System On macro for my devices" },
      { icon: <LayoutGrid size={16} />, title: "Build a control page", prompt: "Build a control page for my devices" },
      { icon: <Wifi size={16} />, title: "Check connections", prompt: "Check device connection status" },
    ];
  }

  // Mature project
  return [
    { icon: <Wrench size={16} />, title: "Troubleshoot", prompt: "Troubleshoot a device issue" },
    { icon: <Plus size={16} />, title: "Add a room", prompt: "Add a new room or zone" },
    { icon: <Clock size={16} />, title: "Set up scheduling", prompt: "Set up scheduling for my macros" },
    { icon: <Sparkles size={16} />, title: "Review my setup", prompt: "Review and optimize my setup" },
  ];
}

/** Full prompt cards shown in the empty state (no messages). */
export function PromptCards({ onSelect }: { onSelect: (prompt: string) => void }) {
  const cards = getPromptCards();

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-lg)",
      }}
    >
      <p style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)", textAlign: "center", maxWidth: 400 }}>
        Describe what you want to build or what's not working.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)", justifyContent: "center", maxWidth: 500 }}>
        {cards.map((card) => (
          <button
            key={card.title}
            onClick={() => onSelect(card.prompt)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-secondary)",
              color: "var(--text-primary)",
              fontSize: "var(--font-size-sm)",
              cursor: "pointer",
              transition: "background 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-secondary)")}
          >
            <span style={{ color: "var(--accent)", flexShrink: 0 }}>{card.icon}</span>
            {card.title}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Compact follow-up suggestion chips shown above the input after a response. */
export function SuggestionChips({
  suggestions,
  onSelect,
}: {
  suggestions: string[];
  onSelect: (prompt: string) => void;
}) {
  if (!suggestions.length) return null;

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-xs)",
        padding: "var(--space-xs) var(--space-lg)",
        overflowX: "auto",
      }}
    >
      {suggestions.map((s, i) => (
        <button
          key={i}
          onClick={() => onSelect(s)}
          style={{
            padding: "2px var(--space-sm)",
            borderRadius: 12,
            border: "1px solid var(--border-color)",
            background: "var(--bg-secondary)",
            color: "var(--text-secondary)",
            fontSize: "var(--font-size-xs)",
            cursor: "pointer",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-secondary)")}
        >
          {s}
        </button>
      ))}
    </div>
  );
}
