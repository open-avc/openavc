/**
 * The one home for a device-backed surface: a live picture of the unit with a
 * persistent inspector rail. Click a control to edit it, Shift+click to press
 * it; with nothing selected the rail shows the deck itself. The editor page
 * and the hardware page stay in lockstep both ways.
 */
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { X } from "lucide-react";
import { showInfo } from "../../../store/toastStore";
import { CollapsibleSection } from "../../driver-builder/CollapsibleSection";
import { useConnectionStore } from "../../../store/connectionStore";
import * as api from "../../../api/restClient";
import { BASE } from "../../../api/base";
import { AppearanceEditor } from "./AppearanceEditor";
import { AutoPageEditor } from "./AutoPageEditor";
import { BezelCanvas } from "./BezelCanvas";
import { BrightnessEditor } from "./BrightnessEditor";
import { ControlAssignmentPanel } from "./ControlAssignmentPanel";
import { DeckInspector } from "./DeckInspector";
import { DialAssignmentPanel } from "./DialAssignmentPanel";
import { InfoStripEditor } from "./InfoStripEditor";
import { NetworkDeckDialog } from "./NetworkDeckDialog";
import { PageTabsRow } from "./PageTabsRow";
import { TouchscreenZonesEditor } from "./TouchscreenZonesEditor";
import { DECK_SECTION_KEYS, SURFACE_ACTIONS, addVirtualUnit, effectivePageCount, forEachNavigateTarget, hasAnyNavigate, networkEntriesOf, networkEntryKey } from "./deckHelpers";
import type { ButtonAssignment, DialAssignment, SurfaceLayout, TouchZone, WorkbenchSelection } from "./types";

export function DeckWorkbench({
  pluginId,
  staticLayout,
  config,
  onConfigChange,
}: {
  pluginId: string;
  staticLayout: SurfaceLayout;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
}) {
  const liveState = useConnectionStore((s) => s.liveState);
  const statePrefix = `plugin.${pluginId}.`;

  const deckSerials = String(liveState[`${statePrefix}deck_serials`] ?? "")
    .split(",")
    .filter(Boolean);
  const decksMap =
    (config.decks as Record<string, Record<string, unknown>> | undefined) ?? {};
  const deckNames = (config.deck_names as Record<string, string> | undefined) ?? {};
  const deckSettings =
    (config.deck_settings as Record<string, { brightness?: number }> | undefined) ?? {};
  const networkEntries = networkEntriesOf(config);
  // A network deck the plugin has connected to at least once carries its
  // serial on the config entry, so it ghosts like any remembered deck.
  const networkSerials = networkEntries
    .map((e) => e.serial)
    .filter((s): s is string => Boolean(s));
  // A network deck answers to a second, ADVERTISED serial (the dock's mDNS
  // alias, e.g. "A00WA6111J9SAM" for a deck whose own serial is
  // "WA6111J9SAM"). It is the same physical deck — never let the alias
  // become its own card.
  const aliasSerials = new Set(
    networkEntries.map((e) => e.mdns_sn).filter((s): s is string => Boolean(s))
  );
  const rememberedSerials = [
    ...new Set([
      ...Object.keys(decksMap),
      ...Object.keys(deckNames),
      ...networkSerials,
    ]),
  ].filter((s) => !deckSerials.includes(s) && !aliasSerials.has(s));
  const knownSerials = [...deckSerials, ...rememberedSerials].filter(
    (s) => !aliasSerials.has(s)
  );

  const [selectedSerial, setSelectedSerial] = useState<string | null>(null);
  const activeSerial =
    selectedSerial && knownSerials.includes(selectedSerial)
      ? selectedSerial
      : knownSerials[0];
  const sp = `${statePrefix}${activeSerial}.`;
  const connected = Boolean(liveState[`${sp}connected`]);
  const model = String(liveState[`${sp}model`] ?? "");
  const rows = Number(liveState[`${sp}rows`] ?? 0);
  const columns = Number(liveState[`${sp}columns`] ?? 0);
  const keyCount = Number(liveState[`${sp}key_count`] ?? 0);
  const touchKeyCount = Number(liveState[`${sp}touch_key_count`] ?? 0);
  const dialCount = Number(liveState[`${sp}dial_count`] ?? 0);
  const hasTouchscreen = Boolean(liveState[`${sp}has_touchscreen`]);
  const hasInfoScreen = Boolean(liveState[`${sp}has_info_screen`]);
  const isVisual = liveState[`${sp}visual`] === undefined
    ? true
    : Boolean(liveState[`${sp}visual`]);
  const isVirtual = Boolean(liveState[`${sp}virtual`]);
  // Network decks: transport/address come from state once connected; a
  // ghost still matches its config entry by serial.
  const activeNetworkEntry = networkEntries.find(
    (e) => e.serial && e.serial === activeSerial
  );
  const transport = String(
    liveState[`${sp}transport`] ?? (activeNetworkEntry ? "network" : "")
  );
  const address =
    String(liveState[`${sp}address`] ?? "") ||
    (activeNetworkEntry ? networkEntryKey(activeNetworkEntry) : "");
  const networkStatus = address
    ? String(
        liveState[
          `${statePrefix}net.${address.replace(/[^A-Za-z0-9_-]/g, "_")}.status`
        ] ?? ""
      )
    : "";
  const renderVersion = Number(liveState[`${sp}render_version`] ?? 0);
  const deckPage = Number(liveState[`${sp}current_page`] ?? 0);
  // Geometry can outlive a disconnect within a session; a ghost with no
  // geometry at all can't draw a canvas (its layout is still kept).
  const hasGeometry = rows > 0 && columns > 0 && keyCount > 0;

  const isOwn = activeSerial ? decksMap[activeSerial] !== undefined : false;
  const viewConfig: Record<string, unknown> =
    isOwn && activeSerial ? decksMap[activeSerial] : config;
  const onViewChange = useCallback(
    (next: Record<string, unknown>) => {
      if (isOwn && activeSerial) {
        onConfigChange({ ...config, decks: { ...decksMap, [activeSerial]: next } });
      } else {
        onConfigChange(next);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isOwn, activeSerial, config, onConfigChange]
  );

  // ── Pages (emergent) ──
  const pageCount = effectivePageCount(viewConfig);
  const [selection, setSelection] = useState<WorkbenchSelection>({ kind: "deck" });
  const [editorPage, setEditorPage] = useState(0);
  const [draftPage, setDraftPage] = useState(false);
  const totalPages = pageCount + (draftPage ? 1 : 0);

  // A draft page becomes real the moment something references it.
  useEffect(() => {
    if (draftPage && editorPage < pageCount) setDraftPage(false);
  }, [draftPage, editorPage, pageCount]);
  useEffect(() => {
    if (editorPage > totalPages - 1) setEditorPage(totalPages - 1);
  }, [editorPage, totalPages]);

  const pageNames = (viewConfig.page_names as Record<string, string> | undefined) ?? {};
  const pageLabel = useCallback(
    (p: number) => pageNames[String(p)] || `Page ${p + 1}`,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [viewConfig.page_names]
  );
  const renamePage = useCallback(
    (p: number, name: string) => {
      const next = { ...pageNames };
      if (name.trim()) {
        next[String(p)] = name.trim();
      } else {
        delete next[String(p)];
      }
      onViewChange({ ...viewConfig, page_names: next });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pageNames, viewConfig, onViewChange]
  );

  const navigateOptions = useMemo(
    () => [
      { value: "__next_page__", label: "Next Page" },
      { value: "__prev_page__", label: "Previous Page" },
      ...Array.from({ length: totalPages }, (_, p) => ({
        value: String(p),
        label: pageLabel(p),
      })),
    ],
    [totalPages, pageLabel]
  );

  // ── Two-way page sync: the canvas can never lie ──
  const userNavAt = useRef(0);
  const onSelectPage = useCallback(
    (p: number) => {
      setEditorPage(p);
      if (p < pageCount && connected && activeSerial) {
        userNavAt.current = Date.now();
        api
          .emitContextAction(pluginId, "set_page", { serial: activeSerial, page: p })
          .catch(() => {});
      }
    },
    [pageCount, connected, activeSerial, pluginId]
  );
  useEffect(() => {
    if (!connected) return;
    if (draftPage && editorPage >= pageCount) return; // building a new page
    if (deckPage === editorPage) return;
    setEditorPage(deckPage);
    // The user's own tab clicks come right back via state — only narrate
    // flips that came from somewhere else (a page rule, a nav key, a macro).
    if (Date.now() - userNavAt.current > 2500) {
      showInfo(`${deckNames[activeSerial ?? ""] || model || "Deck"} moved to ${pageLabel(deckPage)}.`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deckPage, connected]);

  // Esc steps back to the deck panel (matching the ✕ on every inspector).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return; // let fields handle their own Esc (e.g. rename cancel)
      }
      setSelection((prev) => (prev.kind === "deck" ? prev : { kind: "deck" }));
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Switching decks resets the bench to that deck's reality.
  useEffect(() => {
    setSelection({ kind: "deck" });
    setDraftPage(false);
    setEditorPage(Number(useConnectionStore.getState().liveState[`${statePrefix}${activeSerial}.current_page`] ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSerial]);

  // ── Input echo: pressing a physical control flashes it here ──
  const lastInput = String(liveState[`${sp}last_input`] ?? "");
  const [inputFlash, setInputFlash] = useState<{ kind: string; index: number } | null>(null);
  useEffect(() => {
    if (!lastInput) return;
    const [kind, indexStr] = lastInput.split(":");
    const index = Number(indexStr);
    if (!Number.isFinite(index)) return;
    setInputFlash({ kind, index });
    const timer = setTimeout(() => setInputFlash(null), 350);
    return () => clearTimeout(timer);
  }, [lastInput]);

  // ── Live mirror (physical decks mirror only while the bench is open) ──
  useEffect(() => {
    if (!connected || isVirtual) return;
    api.emitContextAction(pluginId, "set_live_mirror", { on: true }).catch(() => {});
    return () => {
      api.emitContextAction(pluginId, "set_live_mirror", { on: false }).catch(() => {});
    };
  }, [pluginId, activeSerial, isVirtual, connected]);

  const [images, setImages] = useState<Record<string, string>>({});
  const imagesRef = useRef<Record<string, string>>({});
  imagesRef.current = images;
  useEffect(() => {
    if (!connected || !isVisual || !activeSerial) {
      setImages((prev) => {
        Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
        return {};
      });
      return;
    }
    let cancelled = false;
    const items: string[] = [];
    for (let i = 0; i < keyCount; i++) items.push(`key_${i}`);
    if (hasTouchscreen) items.push("touchscreen");
    if (hasInfoScreen) items.push("screen");
    (async () => {
      const next: Record<string, string> = {};
      await Promise.all(
        items.map(async (item) => {
          try {
            const res = await fetch(
              `${BASE}/plugins/${pluginId}/ext/live/${activeSerial}/${item}?v=${renderVersion}`
            );
            if (!res.ok) return;
            next[item] = URL.createObjectURL(await res.blob());
          } catch {
            /* mirror not populated yet */
          }
        })
      );
      if (cancelled) {
        Object.values(next).forEach((url) => URL.revokeObjectURL(url));
        return;
      }
      setImages((prev) => {
        Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [pluginId, activeSerial, renderVersion, keyCount, hasTouchscreen, hasInfoScreen, connected, isVisual]);
  useEffect(
    () => () => {
      Object.values(imagesRef.current).forEach((url) => URL.revokeObjectURL(url));
    },
    []
  );

  const simulate = useCallback(
    (payload: Record<string, unknown>) => {
      if (!activeSerial) return;
      api
        .emitContextAction(pluginId, "simulate_input", { serial: activeSerial, ...payload })
        .catch(() => {});
    },
    [pluginId, activeSerial]
  );

  // ── Assignments (locked keys win on every page, like the runtime) ──
  const buttons = (viewConfig.buttons as ButtonAssignment[] | undefined) ?? [];
  const globalButtons =
    (viewConfig.global_buttons as ButtonAssignment[] | undefined) ?? [];
  const lockedIndexes = useMemo(
    () => new Set(globalButtons.map((b) => b.index)),
    [globalButtons]
  );
  const isLocked = useCallback(
    (index: number) => lockedIndexes.has(index),
    [lockedIndexes]
  );
  const getAssignment = useCallback(
    (index: number, page: number = 0): ButtonAssignment | undefined => {
      const locked = globalButtons.find((b) => b.index === index);
      if (locked) return locked;
      return buttons.find((b) => b.index === index && (b.page ?? 0) === page);
    },
    [buttons, globalButtons]
  );
  const shadowPageCount = useCallback(
    (index: number) =>
      new Set(
        buttons.filter((b) => b.index === index).map((b) => b.page ?? 0)
      ).size,
    [buttons]
  );

  const updateAssignment = useCallback(
    (index: number, page: number, updates: Partial<ButtonAssignment>) => {
      if (lockedIndexes.has(index)) {
        const others = globalButtons.filter((b) => b.index !== index);
        const current = globalButtons.find((b) => b.index === index);
        const updated = { index, ...(current ?? {}), ...updates };
        delete (updated as Record<string, unknown>).page;
        onViewChange({ ...viewConfig, global_buttons: [...others, updated] });
        return;
      }
      const existing = buttons.filter(
        (b) => !(b.index === index && (b.page ?? 0) === page)
      );
      const current = buttons.find(
        (b) => b.index === index && (b.page ?? 0) === page
      );
      const updated = { index, page, ...(current ?? {}), ...updates };
      onViewChange({ ...viewConfig, buttons: [...existing, updated] });
    },
    [buttons, globalButtons, lockedIndexes, viewConfig, onViewChange]
  );

  const clearAssignment = useCallback(
    (index: number, page: number) => {
      if (lockedIndexes.has(index)) {
        onViewChange({
          ...viewConfig,
          global_buttons: globalButtons.filter((b) => b.index !== index),
        });
        return;
      }
      onViewChange({
        ...viewConfig,
        buttons: buttons.filter(
          (b) => !(b.index === index && (b.page ?? 0) === page)
        ),
      });
    },
    [buttons, globalButtons, lockedIndexes, viewConfig, onViewChange]
  );

  const toggleLock = useCallback(
    (index: number, locked: boolean) => {
      if (locked) {
        // Lock: the key's current-page content becomes the deck-wide entry.
        // Page entries stay in config (hidden) so unlocking can't lose work.
        const template =
          buttons.find((b) => b.index === index && (b.page ?? 0) === editorPage) ?? {};
        const entry: ButtonAssignment = JSON.parse(JSON.stringify(template));
        delete (entry as Record<string, unknown>).page;
        entry.index = index;
        onViewChange({
          ...viewConfig,
          global_buttons: [...globalButtons.filter((b) => b.index !== index), entry],
        });
      } else {
        // Unlock: the assignment lands on the page being edited (so nothing
        // visible disappears); other pages' hidden entries come back.
        const entry = globalButtons.find((b) => b.index === index);
        const nextGlobals = globalButtons.filter((b) => b.index !== index);
        const nextButtons = buttons.filter(
          (b) => !(b.index === index && (b.page ?? 0) === editorPage)
        );
        if (entry) {
          const restored: ButtonAssignment = JSON.parse(JSON.stringify(entry));
          restored.page = editorPage;
          nextButtons.push(restored);
        }
        onViewChange({
          ...viewConfig,
          buttons: nextButtons,
          global_buttons: nextGlobals,
        });
      }
    },
    [buttons, globalButtons, editorPage, viewConfig, onViewChange]
  );

  // ── Clipboard / arrange (page entries only) ──
  const [clipboard, setClipboard] = useState<ButtonAssignment | null>(null);
  const copyAssignment = useCallback(
    (index: number, page: number) => {
      const current = getAssignment(index, page);
      if (!current) return;
      const { index: _i, page: _p, ...rest } = current;
      setClipboard(JSON.parse(JSON.stringify(rest)));
    },
    [getAssignment]
  );
  const pasteAssignment = useCallback(
    (index: number, page: number) => {
      if (!clipboard) return;
      updateAssignment(index, page, JSON.parse(JSON.stringify(clipboard)));
    },
    [clipboard, updateAssignment]
  );
  const moveAssignment = useCallback(
    (from: { index: number; page: number }, to: { index: number; page: number }) => {
      const source = buttons.find(
        (b) => b.index === from.index && (b.page ?? 0) === from.page
      );
      if (!source) return;
      const others = buttons.filter(
        (b) =>
          !(b.index === from.index && (b.page ?? 0) === from.page) &&
          !(b.index === to.index && (b.page ?? 0) === to.page)
      );
      onViewChange({
        ...viewConfig,
        buttons: [...others, { ...source, index: to.index, page: to.page }],
      });
      setSelection({ kind: "key", index: to.index });
      onSelectPage(to.page);
    },
    [buttons, viewConfig, onViewChange, onSelectPage]
  );
  const swapAssignments = useCallback(
    (a: { index: number; page: number }, b: { index: number; page: number }) => {
      const first = buttons.find(
        (x) => x.index === a.index && (x.page ?? 0) === a.page
      );
      const second = buttons.find(
        (x) => x.index === b.index && (x.page ?? 0) === b.page
      );
      const others = buttons.filter(
        (x) =>
          !(x.index === a.index && (x.page ?? 0) === a.page) &&
          !(x.index === b.index && (x.page ?? 0) === b.page)
      );
      const next = [...others];
      if (first) next.push({ ...first, index: b.index, page: b.page });
      if (second) next.push({ ...second, index: a.index, page: a.page });
      onViewChange({ ...viewConfig, buttons: next });
    },
    [buttons, viewConfig, onViewChange]
  );

  // ── Page operations ──
  const duplicatePage = useCallback(
    (fromPage: number) => {
      const target = pageCount; // a fresh page right after the last one
      const copies = buttons
        .filter((b) => (b.page ?? 0) === fromPage)
        .map((b) => ({ ...JSON.parse(JSON.stringify(b)), page: target }));
      if (copies.length === 0) return;
      onViewChange({ ...viewConfig, buttons: [...buttons, ...copies] });
      setEditorPage(target);
    },
    [buttons, pageCount, viewConfig, onViewChange]
  );
  const clearPage = useCallback(
    (page: number) => {
      onViewChange({
        ...viewConfig,
        buttons: buttons.filter((b) => (b.page ?? 0) !== page),
      });
      setSelection({ kind: "deck" });
    },
    [buttons, viewConfig, onViewChange]
  );
  // The last page can be deleted only when nothing else (a rule, a navigate
  // target) keeps it alive — content and name are removed together.
  const lastPageBlockers = useMemo(() => {
    if (pageCount <= 1) return true;
    const last = pageCount - 1;
    const rules = (viewConfig.auto_page as { page?: unknown }[] | undefined) ?? [];
    if (rules.some((r) => Number(r?.page) === last)) return true;
    let referenced = false;
    forEachNavigateTarget(viewConfig, (page) => {
      if (Number(page) === last) referenced = true;
    });
    return referenced;
  }, [viewConfig, pageCount]);
  const deleteLastPage = useCallback(() => {
    const last = pageCount - 1;
    const nextNames = { ...pageNames };
    delete nextNames[String(last)];
    onViewChange({
      ...viewConfig,
      buttons: buttons.filter((b) => (b.page ?? 0) !== last),
      page_names: nextNames,
    });
    setEditorPage(Math.max(0, last - 1));
    setSelection({ kind: "deck" });
  }, [buttons, pageNames, pageCount, viewConfig, onViewChange]);

  // ── Add page (+) with first-time locked nav keys ──
  const addPage = useCallback(() => {
    if (draftPage) {
      setEditorPage(pageCount); // already drafting — just go there
      return;
    }
    let nextView = viewConfig;
    if (pageCount === 1 && isVisual && hasGeometry && !hasAnyNavigate(viewConfig)) {
      // Both free slots scan backward from the bottom-right LCD key; only
      // slots empty on every page (and not locked) are eligible. Fewer than
      // two free slots -> skip silently, never shadow existing work.
      const free: number[] = [];
      for (let i = keyCount - 1; i >= 0 && free.length < 2; i--) {
        const used =
          lockedIndexes.has(i) || buttons.some((b) => b.index === i);
        if (!used) free.push(i);
      }
      if (free.length === 2) {
        const [nextIdx, prevIdx] = free; // rightmost = next page
        nextView = {
          ...viewConfig,
          global_buttons: [
            ...globalButtons,
            {
              index: prevIdx,
              icon: "chevron-left",
              bindings: { press: [{ action: "navigate", page: "__prev_page__" }] },
            },
            {
              index: nextIdx,
              icon: "chevron-right",
              bindings: { press: [{ action: "navigate", page: "__next_page__" }] },
            },
          ],
        };
        onViewChange(nextView);
        showInfo("Added locked page keys. Move or remove them anytime.");
      }
    }
    setDraftPage(true);
    setEditorPage(effectivePageCount(nextView));
  }, [draftPage, pageCount, isVisual, hasGeometry, viewConfig, keyCount, lockedIndexes, buttons, globalButtons, onViewChange]);

  // ── Dials ──
  const dials = (viewConfig.dials as DialAssignment[] | undefined) ?? [];
  const getDial = useCallback(
    (index: number): DialAssignment | undefined => dials.find((d) => d.index === index),
    [dials]
  );
  const updateDial = useCallback(
    (index: number, updates: Partial<DialAssignment>) => {
      const others = dials.filter((d) => d.index !== index);
      const current = dials.find((d) => d.index === index);
      onViewChange({
        ...viewConfig,
        dials: [...others, { index, ...(current ?? {}), ...updates }],
      });
    },
    [dials, viewConfig, onViewChange]
  );
  const clearDial = useCallback(
    (index: number) => {
      onViewChange({ ...viewConfig, dials: dials.filter((d) => d.index !== index) });
    },
    [dials, viewConfig, onViewChange]
  );

  // ── Unit + layout operations ──
  const renameDeck = useCallback(
    (serial: string, name: string) => {
      const next = { ...deckNames };
      if (name) {
        next[serial] = name;
      } else {
        delete next[serial];
      }
      onConfigChange({ ...config, deck_names: next });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [deckNames, config, onConfigChange]
  );
  const setDeckBrightness = useCallback(
    (serial: string, level: number | undefined) => {
      const next = { ...deckSettings };
      if (level === undefined) {
        const entry = { ...(next[serial] ?? {}) };
        delete entry.brightness;
        if (Object.keys(entry).length === 0) {
          delete next[serial];
        } else {
          next[serial] = entry;
        }
      } else {
        next[serial] = { ...(next[serial] ?? {}), brightness: level };
      }
      onConfigChange({ ...config, deck_settings: next });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [deckSettings, config, onConfigChange]
  );
  const giveOwnLayout = useCallback(() => {
    if (!activeSerial) return;
    const copy: Record<string, unknown> = {};
    for (const section of DECK_SECTION_KEYS) {
      if (config[section] !== undefined) {
        copy[section] = JSON.parse(JSON.stringify(config[section]));
      }
    }
    onConfigChange({ ...config, decks: { ...decksMap, [activeSerial]: copy } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSerial, config, decksMap, onConfigChange]);
  const useSharedLayout = useCallback(() => {
    if (!activeSerial) return;
    const next = { ...decksMap };
    delete next[activeSerial];
    onConfigChange({ ...config, decks: next });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSerial, config, decksMap, onConfigChange]);
  const moveLayoutTo = useCallback(
    (fromSerial: string, toSerial: string) => {
      const layoutToMove = decksMap[fromSerial];
      if (!layoutToMove || fromSerial === toSerial) return;
      const nextDecks = { ...decksMap };
      delete nextDecks[fromSerial];
      nextDecks[toSerial] = layoutToMove;
      const nextNames = { ...deckNames };
      if (nextNames[fromSerial] !== undefined) {
        nextNames[toSerial] = nextNames[fromSerial];
        delete nextNames[fromSerial];
      }
      const next: Record<string, unknown> = {
        ...config,
        decks: nextDecks,
        deck_names: nextNames,
      };
      const virtuals =
        (config.virtual_decks as { model?: string; serial?: string }[] | undefined) ?? [];
      if (virtuals.some((v) => v.serial === fromSerial)) {
        next.virtual_decks = virtuals.filter((v) => v.serial !== fromSerial);
      }
      onConfigChange(next);
      setSelectedSerial(toSerial);
      setSelection({ kind: "deck" });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [decksMap, deckNames, config, onConfigChange]
  );
  const forgetDeck = useCallback(
    (serial: string) => {
      const nextDecks = { ...decksMap };
      delete nextDecks[serial];
      const nextNames = { ...deckNames };
      delete nextNames[serial];
      const nextSettings = { ...deckSettings };
      delete nextSettings[serial];
      onConfigChange({
        ...config,
        decks: nextDecks,
        deck_names: nextNames,
        deck_settings: nextSettings,
      });
      setSelectedSerial(null);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [decksMap, deckNames, deckSettings, config, onConfigChange]
  );
  const removeVirtualDeck = useCallback(
    (serial: string) => {
      const virtuals =
        (config.virtual_decks as { model?: string; serial?: string }[] | undefined) ?? [];
      onConfigChange({
        ...config,
        virtual_decks: virtuals.filter((v) => v.serial !== serial),
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConfigChange]
  );
  const addVirtual = useCallback(
    (modelName: string) => {
      onConfigChange(addVirtualUnit(config, modelName).next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConfigChange]
  );
  const [showNetworkDialog, setShowNetworkDialog] = useState(false);
  const removeNetworkDeck = useCallback(
    (serial: string) => {
      const entries = networkEntriesOf(config);
      const addr = String(liveState[`${statePrefix}${serial}.address`] ?? "");
      onConfigChange({
        ...config,
        network_decks: entries.filter(
          (e) => networkEntryKey(e) !== addr && e.serial !== serial
        ),
      });
      setSelectedSerial(null);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, onConfigChange, liveState, statePrefix]
  );
  // Configured network decks that have never connected (no serial yet, no
  // live session at their address) — surfaced as cards so an entry that's
  // unreachable is visible, not silently absent.
  const connectedAddresses = new Set(
    deckSerials.map((s) => String(liveState[`${statePrefix}${s}.address`] ?? ""))
  );
  const pendingNetwork = networkEntries.filter((e) => {
    if (connectedAddresses.has(networkEntryKey(e))) return false;
    return !(e.serial && knownSerials.includes(e.serial));
  });

  // ── Section summary metas ──
  const autoPageRules = (viewConfig.auto_page as unknown[] | undefined) ?? [];
  const brightnessRules =
    (viewConfig.auto_brightness as unknown[] | undefined) ?? [];
  const idleDim = viewConfig.idle_dim as
    | { after_seconds?: number; level?: number }
    | undefined;
  const brightnessAutoParts: string[] = [];
  if (idleDim) brightnessAutoParts.push(`idle dim ${idleDim.level ?? 10}%`);
  if (brightnessRules.length) {
    brightnessAutoParts.push(
      `${brightnessRules.length} rule${brightnessRules.length === 1 ? "" : "s"}`
    );
  }
  const appearanceParts: string[] = [];
  if (typeof viewConfig.button_color === "string") appearanceParts.push("button color");
  if (typeof viewConfig.text_color === "string") appearanceParts.push("text color");

  const deckDisplayName = activeSerial
    ? deckNames[activeSerial] || model || activeSerial
    : "";
  const ownerName = isOwn ? deckDisplayName : null;
  const sharedWith = deckSerials.filter((s) => decksMap[s] === undefined);

  const selectedKeyIndex = selection.kind === "key" ? selection.index : null;
  const totalKeys = keyCount + touchKeyCount;

  // Strip zone pixel bounds (mirrors the runtime: explicit x/w, else an even
  // split; default = one zone per dial). Drives canvas clicks + touch echo.
  const stripZones =
    ((viewConfig.touchscreen as { zones?: TouchZone[] } | undefined)?.zones) ?? [];
  const zoneBounds = useMemo(() => {
    const count = stripZones.length > 0 ? stripZones.length : dialCount;
    if (count <= 0) return [];
    const slot = 800 / count;
    if (stripZones.length > 0) {
      return stripZones.map((z, i) => ({
        x: typeof z.x === "number" ? z.x : i * slot,
        w: typeof z.w === "number" ? z.w : slot,
      }));
    }
    return Array.from({ length: count }, (_, i) => ({ x: i * slot, w: slot }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewConfig.touchscreen, dialCount]);

  const inspector =
    selection.kind === "key" && selectedKeyIndex !== null ? (
      <ControlAssignmentPanel
        controlId={String(selectedKeyIndex)}
        allowedActions={SURFACE_ACTIONS}
        navigateOptions={navigateOptions}
        colorOnly={touchKeyCount > 0 && selectedKeyIndex >= keyCount}
        visualDeck={isVisual}
        keyCount={keyCount}
        assignment={getAssignment(selectedKeyIndex, editorPage)}
        onUpdate={(updates) => updateAssignment(selectedKeyIndex, editorPage, updates)}
        onClear={() => clearAssignment(selectedKeyIndex, editorPage)}
        onClose={() => setSelection({ kind: "deck" })}
        pageName={pageLabel(editorPage)}
        locked={isLocked(selectedKeyIndex)}
        onToggleLock={(locked) => toggleLock(selectedKeyIndex, locked)}
        lockShadowCount={shadowPageCount(selectedKeyIndex)}
        onPress={
          connected
            ? () => simulate({ type: "key", index: selectedKeyIndex })
            : undefined
        }
        arrange={{
          page: editorPage,
          maxPages: totalPages,
          totalKeys: totalKeys > 0 ? totalKeys : (rows || 3) * (columns || 5),
          pageLabel,
          clipboardReady: clipboard !== null,
          onCopy: () => copyAssignment(selectedKeyIndex, editorPage),
          onPaste: () => pasteAssignment(selectedKeyIndex, editorPage),
          onMove: (to) => moveAssignment({ index: selectedKeyIndex, page: editorPage }, to),
          onSwap: (to) => swapAssignments({ index: selectedKeyIndex, page: editorPage }, to),
        }}
      />
    ) : selection.kind === "dial" ? (
      <DialAssignmentPanel
        dialIndex={selection.index}
        dial={getDial(selection.index)}
        allowedActions={SURFACE_ACTIONS}
        navigateOptions={navigateOptions}
        onUpdate={(updates) => updateDial(selection.index, updates)}
        onClear={() => clearDial(selection.index)}
        onClose={() => setSelection({ kind: "deck" })}
        onSimulate={connected ? simulate : undefined}
        onOpenStrip={
          hasTouchscreen
            ? () => setSelection({ kind: "strip", zone: null })
            : undefined
        }
      />
    ) : selection.kind === "strip" ? (
      <RailPanel title="Touch Strip" onClose={() => setSelection({ kind: "deck" })}>
        <TouchscreenZonesEditor
          config={viewConfig}
          onConfigChange={onViewChange}
          allowedActions={SURFACE_ACTIONS}
          navigateOptions={navigateOptions}
          initialExpanded={selection.zone}
          dials={dials}
          dialCount={dialCount}
          onSimulate={connected ? simulate : undefined}
        />
      </RailPanel>
    ) : selection.kind === "screen" ? (
      <RailPanel title="Info Screen" onClose={() => setSelection({ kind: "deck" })}>
        <InfoStripEditor config={viewConfig} onConfigChange={onViewChange} />
      </RailPanel>
    ) : (
      <DeckInspector
        serial={activeSerial ?? ""}
        name={activeSerial ? deckNames[activeSerial] ?? "" : ""}
        model={model}
        connected={connected}
        isVirtual={isVirtual}
        deckCount={knownSerials.length}
        isOwn={isOwn}
        sharedCount={sharedWith.length}
        brightness={
          activeSerial ? deckSettings[activeSerial]?.brightness : undefined
        }
        fallbackBrightness={
          typeof config.brightness === "number" ? (config.brightness as number) : 70
        }
        onRename={(name) => activeSerial && renameDeck(activeSerial, name)}
        onBrightness={(level) => activeSerial && setDeckBrightness(activeSerial, level)}
        onIdentify={
          connected && activeSerial
            ? () =>
                api
                  .emitContextAction(pluginId, "identify_deck", { serial: activeSerial })
                  .catch(() => {})
            : undefined
        }
        onGiveOwnLayout={!isOwn && knownSerials.length > 1 ? giveOwnLayout : undefined}
        onUseSharedLayout={isOwn ? useSharedLayout : undefined}
        moveTargets={
          isOwn
            ? knownSerials
                .filter((s) => s !== activeSerial)
                .map((s) => ({
                  serial: s,
                  label: deckNames[s] || String(liveState[`${statePrefix}${s}.model`] ?? s),
                  hasOwn: decksMap[s] !== undefined,
                }))
            : []
        }
        onMoveLayoutTo={(to) => activeSerial && moveLayoutTo(activeSerial, to)}
        onRemoveVirtual={
          isVirtual && activeSerial ? () => removeVirtualDeck(activeSerial) : undefined
        }
        onForget={
          !connected && activeSerial ? () => forgetDeck(activeSerial) : undefined
        }
        transport={transport}
        address={address}
        networkStatus={networkStatus}
        onRemoveNetwork={
          transport === "network" && activeSerial
            ? () => removeNetworkDeck(activeSerial)
            : undefined
        }
        onAddNetwork={
          staticLayout.network ? () => setShowNetworkDialog(true) : undefined
        }
        virtualModels={staticLayout.virtual_models ?? []}
        deviceLabel={staticLayout.device_label || "device"}
        onAddVirtual={addVirtual}
        hasTouchscreen={hasTouchscreen}
        customZoneCount={stripZones.length}
        onOpenStrip={() => setSelection({ kind: "strip", zone: null })}
      />
    );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-lg)" }}>
      {/* Known decks (connected + remembered) — selection cards */}
      {knownSerials.length > 1 && (
        <DeckCards
          serials={knownSerials}
          connectedSerials={deckSerials}
          activeSerial={activeSerial ?? ""}
          statePrefix={statePrefix}
          deckNames={deckNames}
          decksMap={decksMap}
          liveState={liveState}
          onSelect={(serial) => {
            setSelectedSerial(serial);
          }}
        />
      )}

      {/* Network decks that haven't connected yet — visible with their
          live connection status, never silently absent */}
      {pendingNetwork.length > 0 && (
        <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
          {pendingNetwork.map((entry) => {
            const key = networkEntryKey(entry);
            const status = String(
              liveState[
                `${statePrefix}net.${key.replace(/[^A-Za-z0-9_-]/g, "_")}.status`
              ] ?? "connecting"
            );
            return (
              <button
                key={key}
                onClick={() => setShowNetworkDialog(true)}
                title="Manage network decks"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-start",
                  gap: "var(--space-2xs)",
                  padding: "var(--space-xs) var(--space-md)",
                  borderRadius: "var(--border-radius)",
                  background: "var(--bg-surface)",
                  border: "1px dashed var(--border-color)",
                  opacity: 0.75,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)" }}>
                  <span
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: "50%",
                      flexShrink: 0,
                      background:
                        status === "connecting"
                          ? "var(--color-warning, #f59e0b)"
                          : "var(--color-error, #ef4444)",
                    }}
                  />
                  {key}
                  <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)", padding: "0 var(--space-xs)" }}>
                    network
                  </span>
                </span>
                <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>{status}</span>
              </button>
            );
          })}
        </div>
      )}

      {showNetworkDialog && (
        <NetworkDeckDialog
          pluginId={pluginId}
          config={config}
          onConfigChange={onConfigChange}
          onClose={() => setShowNetworkDialog(false)}
        />
      )}

      <div style={{ display: "flex", gap: "var(--space-lg)", alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
          {/* Page tabs + the always-visible editing scope */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-md)",
              flexWrap: "wrap",
              marginBottom: "var(--space-sm)",
            }}
          >
            <PageTabsRow
              totalPages={totalPages}
              pageCount={pageCount}
              currentPage={editorPage}
              pageLabel={pageLabel}
              onSelect={onSelectPage}
              onAdd={addPage}
              onRename={(p, name) => renamePage(p, name)}
              onDuplicate={() => duplicatePage(editorPage)}
              onClearPage={() => clearPage(editorPage)}
              canDelete={editorPage === pageCount - 1 && !lastPageBlockers}
              onDelete={deleteLastPage}
              hasContent={buttons.some((b) => (b.page ?? 0) === editorPage)}
            />
            {knownSerials.length > 1 && (
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)" }}>
                {isOwn ? (
                  <>
                    Editing <strong style={{ color: "var(--text-secondary)" }}>{ownerName}'s own layout</strong>. Other decks aren't affected.
                  </>
                ) : (
                  <>
                    Editing the <strong style={{ color: "var(--text-secondary)" }}>shared layout</strong>, shown on every deck without its own.
                  </>
                )}
              </span>
            )}
          </div>

          {draftPage && editorPage >= pageCount && (
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--text-muted)", marginBottom: "var(--space-sm)" }}>
              New page. It's created (and reachable from the deck) as soon as
              you put something on it.
            </div>
          )}

          {hasGeometry ? (
            <BezelCanvas
              name={deckDisplayName}
              model={model}
              connected={connected}
              isVirtual={isVirtual}
              rows={rows}
              columns={columns}
              keyCount={keyCount}
              touchKeyCount={touchKeyCount}
              dialCount={dialCount}
              hasTouchscreen={hasTouchscreen}
              hasInfoScreen={hasInfoScreen}
              images={draftPage && editorPage >= pageCount ? {} : images}
              liveImagesValid={connected && isVisual && !(draftPage && editorPage >= pageCount)}
              touchKeyColors={Array.from({ length: touchKeyCount }, (_, i) =>
                String(liveState[`${sp}touch_key.${keyCount + i}`] ?? "")
              )}
              selection={selection}
              lockedIndexes={lockedIndexes}
              inputFlash={inputFlash}
              currentPage={editorPage}
              getAssignment={getAssignment}
              getDial={getDial}
              customZoneCount={stripZones.length}
              zoneBounds={zoneBounds}
              onSelect={setSelection}
              onSimulate={connected ? simulate : undefined}
            />
          ) : (
            <div
              style={{
                padding: "var(--space-xl)",
                border: "1px dashed var(--border-color)",
                borderRadius: "var(--border-radius)",
                color: "var(--text-muted)",
                fontSize: "var(--font-size-sm)",
                textAlign: "center",
                lineHeight: "var(--line-relaxed)",
              }}
            >
              {deckDisplayName} is not connected.
              <br />
              Reconnect it to edit; its layout is kept. Layout tools are in
              the panel on the right.
            </div>
          )}
        </div>

        {inspector}
      </div>

      {/* Layout-scoped extras, tucked below the bench */}
      <CollapsibleSection
        title="Page automation"
        subtitle="Jump to a page when system state changes"
        meta={
          autoPageRules.length
            ? `${autoPageRules.length} rule${autoPageRules.length === 1 ? "" : "s"}`
            : "off"
        }
        defaultOpen={false}
      >
        <AutoPageEditor
          layout={{ ...staticLayout, max_pages: totalPages }}
          config={viewConfig}
          onConfigChange={onViewChange}
        />
      </CollapsibleSection>
      <CollapsibleSection
        title="Brightness automation"
        subtitle="Idle dimming and state-driven levels (base brightness lives on the deck)"
        meta={brightnessAutoParts.length ? brightnessAutoParts.join(" · ") : "off"}
        defaultOpen={false}
      >
        <BrightnessEditor config={viewConfig} onConfigChange={onViewChange} />
      </CollapsibleSection>
      <CollapsibleSection
        title="Appearance"
        subtitle="Default key colors for this layout"
        meta={appearanceParts.length ? appearanceParts.join(" · ") : "defaults"}
        defaultOpen={false}
      >
        <AppearanceEditor
          viewConfig={viewConfig}
          onViewChange={onViewChange}
          inherits={isOwn}
        />
      </CollapsibleSection>
    </div>
  );
}

// ──── Rail Panel (inspector shell for strip / screen editors) ────

function RailPanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        width: 300,
        flexShrink: 0,
        background: "var(--bg-surface)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        padding: "var(--space-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
        maxHeight: "100%",
        overflow: "auto",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: "var(--font-weight-semibold)" }}>{title}</h4>
        <button onClick={onClose} style={{ color: "var(--text-muted)", cursor: "pointer" }}>
          <X size={14} />
        </button>
      </div>
      {children}
    </div>
  );
}

// ──── Deck Cards (known units: connected, virtual, remembered) ────

function DeckCards({
  serials,
  connectedSerials,
  activeSerial,
  statePrefix,
  deckNames,
  decksMap,
  liveState,
  onSelect,
}: {
  serials: string[];
  connectedSerials: string[];
  activeSerial: string;
  statePrefix: string;
  deckNames: Record<string, string>;
  decksMap: Record<string, Record<string, unknown>>;
  liveState: Record<string, unknown>;
  onSelect: (serial: string) => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "stretch", gap: "var(--space-sm)", flexWrap: "wrap" }}>
      {serials.map((serial) => {
        const isConnected = connectedSerials.includes(serial);
        const isActive = serial === activeSerial;
        const model = String(liveState[`${statePrefix}${serial}.model`] ?? "");
        const virtual = Boolean(liveState[`${statePrefix}${serial}.virtual`]);
        const own = decksMap[serial] !== undefined;
        const page = Number(liveState[`${statePrefix}${serial}.current_page`] ?? 0);
        const pageNames =
          ((own ? decksMap[serial] : undefined)?.page_names as Record<string, string> | undefined) ?? {};
        const status: string[] = [];
        if (!isConnected) {
          status.push(own ? "not connected · layout saved" : "not connected");
        } else {
          status.push(own ? "own layout" : "shared layout");
          status.push(`on ${pageNames[String(page)] || `Page ${page + 1}`}`);
        }
        return (
          <button
            key={serial}
            onClick={() => onSelect(serial)}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: "var(--space-2xs)",
              padding: "var(--space-xs) var(--space-md)",
              borderRadius: "var(--border-radius)",
              background: isActive ? "var(--accent-dim)" : "var(--bg-surface)",
              border: isActive ? "2px solid var(--accent)" : "1px solid var(--border-color)",
              opacity: isConnected ? 1 : 0.55,
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", fontSize: "var(--font-size-sm)", fontWeight: isActive ? "var(--font-weight-semibold)" : "var(--font-weight-normal)" }}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  flexShrink: 0,
                  background: isConnected ? "var(--color-success)" : "var(--text-muted)",
                }}
              />
              {deckNames[serial] || model || serial}
              {virtual && (
                <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)", border: "1px solid var(--border-color)", borderRadius: "var(--border-radius)", padding: "0 var(--space-xs)" }}>
                  virtual
                </span>
              )}
            </span>
            <span style={{ fontSize: "var(--font-size-2xs)", color: "var(--text-muted)" }}>
              {(deckNames[serial] && model ? `${model} · ` : "")}{status.join(" · ")}
            </span>
          </button>
        );
      })}
    </div>
  );
}
