import { useEffect, useState, useCallback } from "react";
import { Search, CheckCircle, Download, RefreshCw, AlertTriangle, Shield, X, PlayCircle, ArrowUpCircle, Loader2 } from "lucide-react";
import { useDriverBuilderStore } from "../../store/driverBuilderStore";
import { useConnectionStore } from "../../store/connectionStore";
import { Modal } from "../shared/Modal";
import { hasUpdate, compareSemver } from "../../api/types";
import type { CommunityDriver } from "../../api/types";

/** The platform release a driver needs, when this system is older than it.
 *
 * The catalog has always carried `min_platform_version` and the server has
 * always enforced it, but nothing showed it: the card rendered a live Install
 * button and the refusal only arrived as an error after the click. That was a
 * rare case until the `server` -> `openavc` rename, which gated every Python
 * driver in the catalog at once -- so on a 0.24.x box the whole Python half of
 * the library looked installable and none of it was.
 *
 * Returns "" when the driver is installable, so the caller can treat it as a
 * plain boolean. An unparseable or absent version never blocks: the server is
 * the authority, and this is only here to say so earlier.
 */
export function blockedByPlatform(
  running: string,
  minRequired: string | null | undefined,
): string {
  if (!minRequired || !running) return "";
  try {
    return compareSemver(minRequired, running) > 0 ? minRequired : "";
  } catch {
    return "";
  }
}

const COMMUNITY_BASE_URL =
  "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/";

const CATEGORIES = [
  "All",
  "Projector",
  "Display",
  "Switcher",
  "Audio",
  "Camera",
  "Video",
  "Streaming",
  "Lighting",
  "Power",
  "Utility",
] as const;

const CATEGORY_COLORS: Record<string, string> = {
  projector: "#e67e22",
  display: "#3498db",
  switcher: "#9b59b6",
  audio: "#2ecc71",
  camera: "#e74c3c",
  video: "#16a085",
  streaming: "#d35400",
  lighting: "#f1c40f",
  power: "#c0392b",
  utility: "#95a5a6",
};

const TRANSPORT_COLORS: Record<string, string> = {
  tcp: "#007acc",
  serial: "#e67e22",
  udp: "#2ecc71",
  http: "#1abc9c",
  osc: "#9b59b6",
};

// A single card rendered in the browser. A driver may produce multiple cards
// when its compatible_models lists more than one manufacturer (e.g. a generic
// VISCA-IP driver covering Sony, AVer, Marshall — each brand gets its own
// card so the integrator searching "AVer" sees an "AVer PTZ Camera" entry,
// not a "Generic VISCA-IP" entry with AVer hidden inside).
//
// Native vs. via:
//   - native (isViaCard=false): the card's brand matches driver.manufacturer.
//     This is the canonical brand the driver is named for. Sorts first within
//     a brand so users always get the dedicated driver if one exists.
//   - via   (isViaCard=true):  the card's brand is one of the OTHER
//     manufacturers in compatible_models. The card is labeled with a small
//     "via <driver name>" line so the user knows it's a generic fallback.
export type DisplayCard = {
  driver: CommunityDriver;
  brand: string;
  brandModels: string[];
  brandConfidence: 'full' | 'partial' | 'untested' | null;
  brandNotes?: string;
  isViaCard: boolean;
};

/** Fan a driver out into one card per distinct manufacturer it claims to
 *  support. Drivers that only target a single brand produce a single card,
 *  matching today's behavior.
 */
export function expandDriverToCards(driver: CommunityDriver): DisplayCard[] {
  const cms = driver.compatible_models ?? [];
  if (cms.length === 0) {
    // No compatible_models declared — fall back to the top-level manufacturer.
    return [{
      driver,
      brand: driver.manufacturer,
      brandModels: [],
      brandConfidence: null,
      isViaCard: false,
    }];
  }
  const distinct = new Set(cms.map((c) => c.manufacturer));
  if (distinct.size <= 1) {
    // Single-manufacturer driver — keep one card, aggregate models so the
    // count line stays accurate.
    return [{
      driver,
      brand: driver.manufacturer,
      brandModels: cms.flatMap((c) => c.models),
      brandConfidence: cms[0]?.confidence ?? null,
      brandNotes: cms[0]?.notes,
      isViaCard: false,
    }];
  }
  // Multi-manufacturer driver: one card per DISTINCT manufacturer, not per
  // compatible_models entry. A brand split across several entries (e.g. a
  // validated model at confidence:full plus a batch of untested siblings)
  // collapses into a single card; the detail modal still lists every entry
  // with its own confidence.
  const confidenceRank: Record<string, number> = { full: 3, partial: 2, untested: 1 };
  const byBrand = new Map<string, DisplayCard>();
  for (const cm of cms) {
    const card = byBrand.get(cm.manufacturer);
    if (card) {
      card.brandModels = [...card.brandModels, ...cm.models];
      // Surface the strongest confidence the driver claims for this brand.
      if ((confidenceRank[cm.confidence] ?? 0) > (confidenceRank[card.brandConfidence ?? ""] ?? 0)) {
        card.brandConfidence = cm.confidence;
        card.brandNotes = cm.notes;
      }
    } else {
      byBrand.set(cm.manufacturer, {
        driver,
        brand: cm.manufacturer,
        brandModels: [...cm.models],
        brandConfidence: cm.confidence,
        brandNotes: cm.notes,
        isViaCard: cm.manufacturer !== driver.manufacturer,
      });
    }
  }
  return [...byBrand.values()];
}

/** Sort: alphabetical by brand, native cards before via cards within a brand,
 *  driver name as final tiebreaker. Keeps Sony's dedicated driver above any
 *  Sony card surfaced by a generic VISCA-IP driver, etc.
 *
 *  This is the BROWSE order, and the tiebreaker once a search has ranked by
 *  relevance (see scoreCard).
 */
function compareCards(a: DisplayCard, b: DisplayCard): number {
  const ba = a.brand.toLowerCase();
  const bb = b.brand.toLowerCase();
  if (ba !== bb) return ba < bb ? -1 : 1;
  if (a.isViaCard !== b.isViaCard) return a.isViaCard ? 1 : -1;
  return a.driver.name.localeCompare(b.driver.name);
}

// How well a query matched one field, 0 when it didn't match at all.
//
// A plain substring test says only yes/no, which is what made searching a
// manufacturer's own name useless: "lea" matched eighteen cards, and the LEA
// amplifier sat alphabetically in the middle of them, below brands that only
// contain the letters (Turt|leA|V) and drivers whose DESCRIPTION happens to
// say "re|lea|ses", "c|lea|r" or "|lea|rned". Where in the word a match lands
// is the signal that separates those: a brand a user typed on purpose starts
// with what they typed.
export function matchQuality(field: string | undefined, q: string): number {
  if (!field) return 0;
  const f = field.toLowerCase();
  if (f === q) return 1;        // the whole field IS the query
  if (f.startsWith(q)) return 0.8;  // "lea" -> "LEA Professional"
  let best = 0;
  for (let i = f.indexOf(q); i !== -1; i = f.indexOf(q, i + 1)) {
    // Starts a word somewhere inside ("Connect Series" in "LEA Connect
    // Series Amplifier") beats landing mid-word ("lea" in "TurtleAV").
    if (i === 0 || !/[a-z0-9]/.test(f[i - 1])) return 0.6;
    best = 0.25;
  }
  return best;
}

// What a match in each field is worth. Brand and model are what an integrator
// actually types; the description is prose we wrote, and is the weakest signal
// there is -- it stays searchable (it's how you find "the driver that does
// IR") but it can no longer outrank the brand someone asked for by name.
const FIELD_WEIGHTS = {
  brand: 100,
  model: 80,
  name: 60,
  id: 40,
  tag: 30,
  description: 10,
} as const;

/** Relevance of one card to one already-lowercased query. 0 = no match, which
 *  is also what filters the card out, so what MATCHES and what RANKS can never
 *  drift apart into two lists of fields.
 */
export function scoreCard(card: DisplayCard, q: string): number {
  const d = card.driver;
  return Math.max(
    FIELD_WEIGHTS.brand * matchQuality(card.brand, q),
    FIELD_WEIGHTS.name * matchQuality(d.name, q),
    FIELD_WEIGHTS.id * matchQuality(d.id, q),
    FIELD_WEIGHTS.description * matchQuality(d.description, q),
    ...card.brandModels.map((m) => FIELD_WEIGHTS.model * matchQuality(m, q)),
    ...(d.tags ?? []).map((t) => FIELD_WEIGHTS.tag * matchQuality(t, q)),
  );
}

/** Apply the category filter and the search box to the expanded card list.
 *
 *  Scoring does double duty: a zero score is "no match", so the set that shows
 *  and the order it shows in come from one pass over one list of fields and
 *  cannot drift apart.
 */
export function rankCards(
  cards: DisplayCard[],
  searchQuery: string,
  activeCategory: string,
): DisplayCard[] {
  const q = searchQuery.trim().toLowerCase();
  return cards
    .filter((card) =>
      activeCategory === "All" ||
      card.driver.category.toLowerCase() === activeCategory.toLowerCase(),
    )
    .map((card) => ({ card, score: q ? scoreCard(card, q) : 0 }))
    .filter((entry) => !q || entry.score > 0)
    // Best match first while searching; browsing (no query) stays alphabetical.
    // compareCards breaks ties either way, so two cards matching a brand name
    // equally well still put the dedicated driver above the generic one.
    .sort((a, b) => b.score - a.score || compareCards(a.card, b.card))
    .map((entry) => entry.card);
}

export function CommunityBrowser() {
  const communityDrivers = useDriverBuilderStore((s) => s.communityDrivers);
  const installedDrivers = useDriverBuilderStore((s) => s.installedDrivers);
  const communityLoading = useDriverBuilderStore((s) => s.communityLoading);
  const communityError = useDriverBuilderStore((s) => s.communityError);
  const loadCommunityDrivers = useDriverBuilderStore((s) => s.loadCommunityDrivers);
  const loadInstalledDrivers = useDriverBuilderStore((s) => s.loadInstalledDrivers);
  // Primitive selector on purpose: subscribing to the whole liveState object
  // would re-render this grid on every unrelated state push.
  const runningVersion = useConnectionStore(
    (s) => String(s.liveState["system.version"] ?? ""),
  );

  const [searchQuery, setSearchQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState("All");
  const [installingIds, setInstallingIds] = useState<Set<string>>(new Set());
  const [installErrors, setInstallErrors] = useState<Record<string, string>>({});
  const [selectedDriver, setSelectedDriver] = useState<CommunityDriver | null>(null);

  useEffect(() => {
    loadCommunityDrivers();
    loadInstalledDrivers();
  }, [loadCommunityDrivers, loadInstalledDrivers]);

  const installedIdSet = new Set(installedDrivers.map((d) => d.id));
  const installedVersions = new Map(installedDrivers.map((d) => [d.id, d.version ?? ""]));

  // Build a map of driver id -> name for deprecated drivers' replacement lookup
  const driverNameById = new Map(communityDrivers.map((d) => [d.id, d.name]));

  // Expand each driver into 1+ cards (one per supported manufacturer when the
  // driver is multi-brand), then filter cards individually. Filtering at the
  // card level means searching "Sony" only surfaces Sony-branded cards — not
  // every other brand the same driver happens to support.
  const allCards = communityDrivers.flatMap(expandDriverToCards);
  const filteredCards = rankCards(allCards, searchQuery, activeCategory);

  const handleInstall = useCallback(
    async (driver: CommunityDriver) => {
      const fileUrl = `${COMMUNITY_BASE_URL}${driver.file}`;
      setInstallingIds((prev) => new Set(prev).add(driver.id));
      setInstallErrors((prev) => {
        const next = { ...prev };
        delete next[driver.id];
        return next;
      });
      try {
        await useDriverBuilderStore.getState().installDriver(driver.id, fileUrl, driver.min_platform_version);
      } catch (e) {
        setInstallErrors((prev) => ({ ...prev, [driver.id]: String(e) }));
      } finally {
        setInstallingIds((prev) => {
          const next = new Set(prev);
          next.delete(driver.id);
          return next;
        });
      }
    },
    []
  );

  const handleUpdate = useCallback(
    async (driver: CommunityDriver) => {
      const fileUrl = `${COMMUNITY_BASE_URL}${driver.file}`;
      setInstallingIds((prev) => new Set(prev).add(driver.id));
      setInstallErrors((prev) => {
        const next = { ...prev };
        delete next[driver.id];
        return next;
      });
      try {
        await useDriverBuilderStore.getState().updateDriver(driver.id, fileUrl, driver.min_platform_version);
      } catch (e) {
        setInstallErrors((prev) => ({ ...prev, [driver.id]: String(e) }));
      } finally {
        setInstallingIds((prev) => {
          const next = new Set(prev);
          next.delete(driver.id);
          return next;
        });
      }
    },
    []
  );

  const handleRetry = useCallback(() => {
    loadCommunityDrivers();
    loadInstalledDrivers();
  }, [loadCommunityDrivers, loadInstalledDrivers]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Search bar */}
      <div
        style={{
          padding: "var(--space-md) var(--space-lg)",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-sm)",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <Search size={16} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search drivers by name, manufacturer, or description..."
            style={{
              flex: 1,
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              border: "1px solid var(--border-color)",
              background: "var(--bg-surface)",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />
          <button
            onClick={handleRetry}
            title="Refresh driver list"
            style={{
              padding: "var(--space-sm)",
              borderRadius: "var(--border-radius)",
              background: "var(--bg-hover)",
              color: "var(--text-muted)",
              border: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              flexShrink: 0,
            }}
          >
            <RefreshCw size={16} />
          </button>
        </div>

        {/* Category filter pills */}
        <div
          style={{
            display: "flex",
            gap: "var(--space-xs)",
            flexWrap: "wrap",
          }}
        >
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              style={{
                padding: "var(--space-2xs) var(--space-md)",
                borderRadius: "var(--radius-lg)",
                border: "1px solid",
                borderColor:
                  activeCategory === cat ? "var(--accent)" : "var(--border-color)",
                background:
                  activeCategory === cat ? "var(--accent)" : "transparent",
                color: activeCategory === cat ? "#fff" : "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                cursor: "pointer",
                fontWeight: activeCategory === cat ? 600 : 400,
              }}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Content area */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "var(--space-md) var(--space-lg)",
        }}
      >
        {communityLoading ? (
          <LoadingState />
        ) : communityError ? (
          <ErrorState error={communityError} onRetry={handleRetry} />
        ) : filteredCards.length === 0 ? (
          <EmptyFilterState
            hasDrivers={communityDrivers.length > 0}
            searchQuery={searchQuery}
            category={activeCategory}
          />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
              gap: "var(--space-md)",
            }}
          >
            {filteredCards.map((card) => {
              const driver = card.driver;
              return (
                <DriverCard
                  // Brand is part of the key because one driver may produce
                  // multiple cards (Sony / AVer / Marshall via generic VISCA).
                  key={`${driver.id}|${card.brand}`}
                  card={card}
                  installed={installedIdSet.has(driver.id)}
                  installing={installingIds.has(driver.id)}
                  installError={installErrors[driver.id] || null}
                  updateAvailable={hasUpdate(installedVersions.get(driver.id) ?? "", driver.version)}
                  requiresPlatform={blockedByPlatform(runningVersion, driver.min_platform_version)}
                  replacementName={
                    driver.deprecated && driver.replacement_id
                      ? driverNameById.get(driver.replacement_id) ?? driver.replacement_id
                      : null
                  }
                  onInstall={handleInstall}
                  onUpdate={handleUpdate}
                  onSelect={setSelectedDriver}
                  onTagClick={setSearchQuery}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* Footer with count */}
      {!communityLoading && !communityError && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-lg)",
            borderTop: "1px solid var(--border-color)",
            fontSize: "var(--font-size-sm)",
            color: "var(--text-muted)",
            flexShrink: 0,
          }}
        >
          {filteredCards.length} {filteredCards.length === 1 ? "result" : "results"} ·{" "}
          {communityDrivers.length} {communityDrivers.length === 1 ? "driver" : "drivers"}
          {installedDrivers.length > 0 &&
            ` · ${installedDrivers.length} installed`}
        </div>
      )}

      {/* Detail modal */}
      {selectedDriver && (
        <CommunityDriverDetail
          driver={selectedDriver}
          installed={installedIdSet.has(selectedDriver.id)}
          installing={installingIds.has(selectedDriver.id)}
          installError={installErrors[selectedDriver.id] || null}
          updateAvailable={hasUpdate(installedVersions.get(selectedDriver.id) ?? "", selectedDriver.version)}
          requiresPlatform={blockedByPlatform(runningVersion, selectedDriver.min_platform_version)}
          replacementName={
            selectedDriver.deprecated && selectedDriver.replacement_id
              ? driverNameById.get(selectedDriver.replacement_id) ?? selectedDriver.replacement_id
              : null
          }
          onInstall={handleInstall}
          onUpdate={handleUpdate}
          onClose={() => setSelectedDriver(null)}
          onTagClick={(tag) => {
            setSearchQuery(tag);
            setSelectedDriver(null);
          }}
        />
      )}
    </div>
  );
}

// --- Driver Card ---

function DriverCard({
  card,
  installed,
  installing,
  installError,
  updateAvailable,
  requiresPlatform,
  replacementName,
  onInstall,
  onUpdate,
  onSelect,
  onTagClick,
}: {
  card: DisplayCard;
  installed: boolean;
  installing: boolean;
  installError: string | null;
  updateAvailable: boolean;
  /** Platform release this driver needs, when this system is older. "" = fine. */
  requiresPlatform: string;
  replacementName: string | null;
  onInstall: (driver: CommunityDriver) => void;
  onUpdate: (driver: CommunityDriver) => void;
  onSelect: (driver: CommunityDriver) => void;
  onTagClick: (tag: string) => void;
}) {
  const driver = card.driver;
  // Count models for THIS card's brand only — for via cards, that's just the
  // brand we're surfacing on this card, not the entire compatible_models tree.
  const compatibleModelCount = card.brandModels.length;
  const [hovered, setHovered] = useState(false);

  const catColor = CATEGORY_COLORS[driver.category.toLowerCase()] || "#888";
  const transportColor = TRANSPORT_COLORS[driver.transport.toLowerCase()] || "#888";

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => onSelect(driver)}
      style={{
        background: hovered ? "#3d3d3d" : "#2d2d2d",
        borderRadius: "var(--border-radius)",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
        border: "1px solid var(--border-color)",
        transition: "background 0.15s",
        cursor: "pointer",
        opacity: driver.deprecated ? 0.65 : 1,
      }}
    >
      {/* Header row: name + badges */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-sm)" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
            }}
          >
            <span
              title={card.isViaCard ? `${card.brand} (provided by ${driver.name})` : driver.name}
              style={{
                fontWeight: "var(--font-weight-semibold)",
                fontSize: "var(--font-size-sm)",
                color: "#ccc",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {card.isViaCard ? card.brand : driver.name}
            </span>
            {driver.verified && (
              <span title="Verified driver" style={{ display: "flex", flexShrink: 0 }}>
                <Shield size={14} style={{ color: "var(--color-success)" }} />
              </span>
            )}
            {driver.simulated && (
              <span title="Simulator available" style={{ display: "flex", flexShrink: 0 }}>
                <PlayCircle size={14} style={{ color: "var(--accent)" }} />
              </span>
            )}
            {driver.deprecated && (
              <span
                title={
                  replacementName
                    ? `Deprecated: use ${replacementName} instead`
                    : "Deprecated"
                }
                style={{
                  marginLeft: "var(--space-xs)",
                  padding: "0 var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-2xs)",
                  fontWeight: "var(--font-weight-semibold)",
                  background: "rgba(239, 68, 68, 0.15)",
                  color: "var(--color-error)",
                  border: "1px solid rgba(239, 68, 68, 0.4)",
                  textTransform: "uppercase",
                  letterSpacing: "var(--tracking-wide)",
                  flexShrink: 0,
                }}
              >
                Deprecated
              </span>
            )}
          </div>
          {card.isViaCard ? (
            // Via cards make the fallback explicit: this brand isn't getting
            // a dedicated driver — it's covered by a generic. The italic
            // styling and "via …" wording are the user's signal that a
            // dedicated driver, if one ever ships, would be preferred.
            <div
              title={`${card.brand} is covered by ${driver.name}, a generic / multi-brand driver. If a dedicated ${card.brand} driver becomes available, it will appear above this card.`}
              style={{
                fontSize: "var(--font-size-sm)",
                color: "#888",
                marginTop: "var(--space-2xs)",
                fontStyle: "italic",
              }}
            >
              via {driver.name} · by {driver.author}
            </div>
          ) : (
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                color: "#888",
                marginTop: "var(--space-2xs)",
              }}
            >
              {driver.manufacturer} · by {driver.author}
            </div>
          )}
        </div>
      </div>

      {/* Badges */}
      <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
        <span
          style={{
            padding: "var(--space-2xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-medium)",
            background: `${catColor}22`,
            color: catColor,
            border: `1px solid ${catColor}44`,
          }}
        >
          {driver.category}
        </span>
        <span
          style={{
            padding: "var(--space-2xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-medium)",
            background: `${transportColor}22`,
            color: transportColor,
            border: `1px solid ${transportColor}44`,
          }}
        >
          {driver.transport.toUpperCase()}
        </span>
        <span
          style={{
            padding: "var(--space-2xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
            fontSize: "var(--font-size-xs)",
            color: "#888",
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.1)",
          }}
        >
          v{driver.version}
        </span>
      </div>

      {/* Tags */}
      {driver.tags && driver.tags.length > 0 && (
        <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
          {driver.tags.map((tag) => (
            <button
              key={tag}
              onClick={(e) => {
                e.stopPropagation();
                onTagClick(tag);
              }}
              title={`Search for "${tag}"`}
              style={{
                padding: "var(--space-2xs) var(--space-sm)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-2xs)",
                fontWeight: "var(--font-weight-medium)",
                background: "rgba(96,165,250,0.10)",
                color: "#60a5fa",
                border: "1px solid rgba(96,165,250,0.25)",
                cursor: "pointer",
              }}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Description */}
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "#888",
          lineHeight: "var(--line-base)",
          overflow: "hidden",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          flex: 1,
        }}
      >
        {driver.description}
      </div>

      {compatibleModelCount > 0 && (
        <div style={{ fontSize: "var(--font-size-xs)", color: "#888" }}>
          Compatible with {compatibleModelCount}{" "}
          {compatibleModelCount === 1 ? "model" : "models"}
        </div>
      )}

      {/* Install error */}
      {installError && (
        <div
          style={{
            fontSize: "var(--font-size-xs)",
            color: "var(--color-error)",
            background: "rgba(239,68,68,0.1)",
            padding: "var(--space-xs) var(--space-sm)",
            borderRadius: "var(--border-radius)",
          }}
        >
          Install failed: {installError.replace(/^Error:\s*/i, "").slice(0, 100)}
        </div>
      )}

      {/* Action button */}
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: "var(--space-sm)" }}>
        {requiresPlatform && (
          <span
            title={`This driver needs OpenAVC ${requiresPlatform} or later. Update OpenAVC first, then install it.`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
            }}
          >
            <AlertTriangle size={14} />
            Needs OpenAVC v{requiresPlatform}
          </span>
        )}
        {installed ? (
          updateAvailable && !installing && !requiresPlatform ? (
            <button
              onClick={(e) => { e.stopPropagation(); onUpdate(driver); }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-sm)",
                background: "#007acc",
                color: "#fff",
                border: "none",
                cursor: "pointer",
                fontWeight: "var(--font-weight-medium)",
              }}
            >
              <ArrowUpCircle size={14} />
              Update
            </button>
          ) : installing ? (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-sm)",
                color: "var(--text-muted)",
                background: "var(--bg-hover)",
                fontWeight: "var(--font-weight-medium)",
              }}
            >
              <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
              Updating...
            </span>
          ) : (
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-xs) var(--space-md)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-sm)",
                color: "var(--color-success)",
                background: "rgba(76,175,80,0.12)",
                fontWeight: "var(--font-weight-medium)",
              }}
            >
              <CheckCircle size={14} />
              Installed
            </span>
          )
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onInstall(driver); }}
            disabled={installing || !!requiresPlatform}
            title={
              requiresPlatform
                ? `This driver needs OpenAVC ${requiresPlatform} or later.`
                : undefined
            }
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-sm)",
              background: installing || requiresPlatform ? "var(--bg-hover)" : "#007acc",
              color: installing || requiresPlatform ? "var(--text-muted)" : "#fff",
              border: "none",
              cursor: installing || requiresPlatform ? "default" : "pointer",
              fontWeight: "var(--font-weight-medium)",
            }}
          >
            {installing ? (
              <>
                <RefreshCw
                  size={14}
                  style={{
                    animation: "spin 1s linear infinite",
                  }}
                />
                Installing...
              </>
            ) : (
              <>
                <Download size={14} />
                Install
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}

// --- Community Driver Detail Modal ---

function CommunityDriverDetail({
  driver,
  installed,
  installing,
  installError,
  updateAvailable,
  requiresPlatform,
  replacementName,
  onInstall,
  onUpdate,
  onClose,
  onTagClick,
}: {
  driver: CommunityDriver;
  installed: boolean;
  installing: boolean;
  installError: string | null;
  updateAvailable: boolean;
  /** Platform release this driver needs, when this system is older. "" = fine. */
  requiresPlatform: string;
  replacementName: string | null;
  onInstall: (driver: CommunityDriver) => void;
  onUpdate: (driver: CommunityDriver) => void;
  onClose: () => void;
  onTagClick: (tag: string) => void;
}) {
  const catColor = CATEGORY_COLORS[driver.category.toLowerCase()] || "#888";
  const transportColor = TRANSPORT_COLORS[driver.transport.toLowerCase()] || "#888";
  const confidenceLabel = (c: string) =>
    c === "full" ? "Full support" : c === "partial" ? "Partial support" : "Untested";
  const confidenceColor = (c: string) =>
    c === "full" ? "#10b981" : c === "partial" ? "#f59e0b" : "#94a3b8";

  return (
    <Modal
      onClose={onClose}
      label="Community Driver Details"
      panelStyle={{
        background: "var(--bg-surface, #2d2d2d)",
        borderRadius: "8px",
        border: "1px solid var(--border-color)",
        width: "min(560px, 90vw)",
        maxHeight: "80vh",
        overflow: "auto",
        padding: "var(--space-lg)",
      }}
    >
        {/* Deprecation banner */}
        {driver.deprecated && (
          <div
            style={{
              marginBottom: "var(--space-md)",
              padding: "var(--space-sm) var(--space-md)",
              background: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              borderRadius: "var(--border-radius)",
              color: "var(--color-error)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <strong>Deprecated.</strong>{" "}
            {replacementName
              ? `Use ${replacementName} instead.`
              : "Do not install for new projects."}
          </div>
        )}

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
              <h2 style={{ margin: 0, fontSize: "1.25rem" }}>{driver.name}</h2>
              {driver.verified && (
                <span title="Verified driver"><Shield size={16} style={{ color: "var(--color-success)" }} /></span>
              )}
              {driver.simulated && (
                <span title="Simulator available"><PlayCircle size={16} style={{ color: "var(--accent)" }} /></span>
              )}
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", marginTop: "var(--space-xs)" }}>
              {driver.manufacturer} &middot; by {driver.author}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: "var(--space-xs)",
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Badges */}
        <div style={{ display: "flex", gap: "var(--space-xs)", marginTop: "var(--space-md)", flexWrap: "wrap" }}>
          <span
            style={{
              padding: "var(--space-2xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              background: `${catColor}22`,
              color: catColor,
              border: `1px solid ${catColor}44`,
            }}
          >
            {driver.category}
          </span>
          <span
            style={{
              padding: "var(--space-2xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              background: `${transportColor}22`,
              color: transportColor,
              border: `1px solid ${transportColor}44`,
            }}
          >
            {driver.transport.toUpperCase()}
          </span>
          <span
            style={{
              padding: "var(--space-2xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-xs)",
              color: "#888",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.1)",
            }}
          >
            v{driver.version}
          </span>
          <span
            style={{
              padding: "var(--space-2xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
              fontSize: "var(--font-size-xs)",
              color: "#888",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.1)",
            }}
          >
            {driver.format === "python" ? "Python" : "YAML"}
          </span>
        </div>

        {/* Tags */}
        {driver.tags && driver.tags.length > 0 && (
          <div style={{ marginTop: "var(--space-sm)", display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
            {driver.tags.map((tag) => (
              <button
                key={tag}
                onClick={() => onTagClick(tag)}
                title={`Search for "${tag}"`}
                style={{
                  padding: "var(--space-2xs) var(--space-sm)",
                  borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: "var(--font-weight-medium)",
                  background: "rgba(96,165,250,0.10)",
                  color: "#60a5fa",
                  border: "1px solid rgba(96,165,250,0.25)",
                  cursor: "pointer",
                }}
              >
                {tag}
              </button>
            ))}
          </div>
        )}

        {/* Full description */}
        <div style={{ marginTop: "var(--space-lg)", lineHeight: "var(--line-relaxed)", fontSize: "var(--font-size-sm)" }}>
          {driver.description}
        </div>

        {/* Protocols */}
        {driver.protocols && driver.protocols.length > 0 && (
          <div style={{ marginTop: "var(--space-md)" }}>
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: "var(--space-xs)", fontWeight: "var(--font-weight-semibold)", textTransform: "uppercase", letterSpacing: "var(--tracking-wide)" }}>
              Protocols
            </div>
            <div style={{ display: "flex", gap: "var(--space-xs)", flexWrap: "wrap" }}>
              {driver.protocols.map((p) => (
                <span
                  key={p}
                  style={{
                    padding: "var(--space-2xs) var(--space-sm)",
                    borderRadius: "var(--border-radius)",
                    fontSize: "var(--font-size-xs)",
                    background: "rgba(59,130,246,0.15)",
                    color: "#60a5fa",
                    border: "1px solid rgba(59,130,246,0.3)",
                  }}
                >
                  {p}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Ports */}
        {driver.ports && driver.ports.length > 0 && (
          <div style={{ marginTop: "var(--space-md)" }}>
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: "var(--space-xs)", fontWeight: "var(--font-weight-semibold)", textTransform: "uppercase", letterSpacing: "var(--tracking-wide)" }}>
              Default Ports
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)" }}>
              {driver.ports.join(", ")}
            </div>
          </div>
        )}

        {/* Help overview (when driver carries help.overview) */}
        {driver.help?.overview && (
          <div style={{ marginTop: "var(--space-md)" }}>
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: "var(--space-xs)", fontWeight: "var(--font-weight-semibold)", textTransform: "uppercase", letterSpacing: "var(--tracking-wide)" }}>
              Overview
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", lineHeight: "var(--line-base)", color: "var(--text-muted)", whiteSpace: "pre-wrap" }}>
              {driver.help.overview}
            </div>
          </div>
        )}

        {/* Compatible models */}
        {driver.compatible_models && driver.compatible_models.length > 0 && (
          <div style={{ marginTop: "var(--space-md)" }}>
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: "var(--space-xs)", fontWeight: "var(--font-weight-semibold)", textTransform: "uppercase", letterSpacing: "var(--tracking-wide)" }}>
              Compatible Devices
            </div>
            {driver.compatible_models.map((cm, idx) => (
              <div
                key={idx}
                style={{
                  marginTop: idx === 0 ? 0 : "var(--space-sm)",
                  padding: "var(--space-sm) var(--space-md)",
                  background: "rgba(255,255,255,0.03)",
                  borderRadius: "var(--border-radius)",
                  border: "1px solid var(--border-color)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginBottom: "var(--space-xs)" }}>
                  <span style={{ fontSize: "var(--font-size-sm)", fontWeight: "var(--font-weight-semibold)", color: "#ccc" }}>
                    {cm.manufacturer}
                  </span>
                  <span
                    style={{
                      padding: "var(--space-2xs) var(--space-sm)",
                      borderRadius: "var(--border-radius)",
                      fontSize: "var(--font-size-2xs)",
                      fontWeight: "var(--font-weight-medium)",
                      background: `${confidenceColor(cm.confidence)}22`,
                      color: confidenceColor(cm.confidence),
                      border: `1px solid ${confidenceColor(cm.confidence)}44`,
                    }}
                  >
                    {confidenceLabel(cm.confidence)}
                  </span>
                </div>
                <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-muted)", lineHeight: "var(--line-base)" }}>
                  {cm.models.join(", ")}
                </div>
                {cm.notes && (
                  <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginTop: "var(--space-xs)", fontStyle: "italic" }}>
                    {cm.notes}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Note about help */}
        <div
          style={{
            marginTop: "var(--space-lg)",
            padding: "var(--space-sm) var(--space-md)",
            background: "rgba(255,255,255,0.03)",
            borderRadius: "var(--border-radius)",
            border: "1px solid var(--border-color)",
            fontSize: "var(--font-size-xs)",
            color: "var(--text-muted)",
            fontStyle: "italic",
          }}
        >
          Install this driver to see configuration details and available commands.
        </div>

        {/* Install error */}
        {installError && (
          <div
            style={{
              marginTop: "var(--space-sm)",
              fontSize: "var(--font-size-xs)",
              color: "var(--color-error)",
              background: "rgba(239,68,68,0.1)",
              padding: "var(--space-xs) var(--space-sm)",
              borderRadius: "var(--border-radius)",
            }}
          >
            Install failed: {installError.replace(/^Error:\s*/i, "").slice(0, 200)}
          </div>
        )}

        {/* Action */}
        {requiresPlatform && (
          <div
            style={{
              marginTop: "var(--space-lg)",
              fontSize: "var(--font-size-sm)",
              color: "var(--text-muted)",
              background: "var(--bg-hover)",
              padding: "var(--space-sm) var(--space-md)",
              borderRadius: "var(--border-radius)",
              display: "flex",
              alignItems: "center",
              gap: "var(--space-sm)",
            }}
          >
            <AlertTriangle size={14} />
            This driver needs OpenAVC v{requiresPlatform} or later. Update OpenAVC
            first, then install it.
          </div>
        )}
        <div style={{ marginTop: "var(--space-lg)", display: "flex", justifyContent: "flex-end" }}>
          {installed ? (
            updateAvailable && !installing && !requiresPlatform ? (
              <button
                onClick={() => onUpdate(driver)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-sm)",
                  background: "#007acc",
                  color: "#fff",
                  border: "none",
                  cursor: "pointer",
                  fontWeight: "var(--font-weight-medium)",
                }}
              >
                <ArrowUpCircle size={14} />
                Update
              </button>
            ) : installing ? (
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-muted)",
                  background: "var(--bg-hover)",
                  fontWeight: "var(--font-weight-medium)",
                }}
              >
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
                Updating...
              </span>
            ) : (
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "var(--space-sm) var(--space-lg)",
                  borderRadius: "var(--border-radius)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-success)",
                  background: "rgba(76,175,80,0.12)",
                  fontWeight: "var(--font-weight-medium)",
                }}
              >
                <CheckCircle size={14} />
                Installed
              </span>
            )
          ) : (
            <button
              onClick={() => onInstall(driver)}
              disabled={installing || !!requiresPlatform}
              title={
                requiresPlatform
                  ? `This driver needs OpenAVC ${requiresPlatform} or later.`
                  : undefined
              }
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-xs)",
                padding: "var(--space-sm) var(--space-lg)",
                borderRadius: "var(--border-radius)",
                fontSize: "var(--font-size-sm)",
                background: installing || requiresPlatform ? "var(--bg-hover)" : "#007acc",
                color: installing || requiresPlatform ? "var(--text-muted)" : "#fff",
                border: "none",
                cursor: installing || requiresPlatform ? "default" : "pointer",
                fontWeight: "var(--font-weight-medium)",
              }}
            >
              {installing ? (
                <>
                  <RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />
                  Installing...
                </>
              ) : (
                <>
                  <Download size={14} />
                  Install Driver
                </>
              )}
            </button>
          )}
        </div>
    </Modal>
  );
}


// --- Loading State ---

function LoadingState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-md)",
        color: "var(--text-muted)",
      }}
    >
      <RefreshCw
        size={32}
        style={{
          animation: "spin 1s linear infinite",
          opacity: 0.5,
        }}
      />
      <span style={{ fontSize: "var(--font-size-sm)" }}>
        Loading community drivers...
      </span>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

// --- Error State ---

function ErrorState({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-md)",
      }}
    >
      <AlertTriangle size={32} style={{ color: "var(--color-error)", opacity: 0.7 }} />
      <div
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--text-muted)",
          textAlign: "center",
          maxWidth: 400,
        }}
      >
        Failed to load community drivers.
        <br />
        <span style={{ fontSize: "var(--font-size-sm)", color: "#888" }}>
          {error.replace(/^Error:\s*/i, "").slice(0, 200)}
        </span>
      </div>
      <button
        onClick={onRetry}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "var(--space-sm) var(--space-lg)",
          borderRadius: "var(--border-radius)",
          background: "var(--accent-bg)",
          color: "#fff",
          border: "none",
          cursor: "pointer",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <RefreshCw size={14} />
        Retry
      </button>
    </div>
  );
}

// --- Empty Filter State ---

function EmptyFilterState({
  hasDrivers,
  searchQuery,
  category,
}: {
  hasDrivers: boolean;
  searchQuery: string;
  category: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: "var(--space-sm)",
        color: "var(--text-muted)",
      }}
    >
      <Search size={32} style={{ opacity: 0.3 }} />
      <span style={{ fontSize: "var(--font-size-sm)" }}>
        {hasDrivers
          ? `No drivers match "${searchQuery}"${category !== "All" ? ` in ${category}` : ""}`
          : "No community drivers available yet."}
      </span>
      {hasDrivers && (
        <span style={{ fontSize: "var(--font-size-sm)", color: "#888" }}>
          Try a different search term or category.
        </span>
      )}
    </div>
  );
}
