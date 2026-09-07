import { describe, it, expect } from "vitest";
import { undoHistoryIsStale, type UndoEntry, type UndoScope } from "./uiBuilderStore.helpers";
import type { ProjectConfig } from "../api/types";

/**
 * The UI Builder refetches the project whenever the server broadcasts
 * project.reloaded, and the engine broadcasts that on every apply and every
 * bookkeeping persist: a device's learned config, a discovery add, a fleet
 * push, an edit made in another session, a reconnect. Dropping the undo
 * history on all of them meant the next Ctrl+Z reverted an older change
 * instead of the one just made, with nothing on screen to say why.
 *
 * The rule here is what makes that decision: an entry is stale only once the
 * section it would write has moved underneath it.
 */

const project = (over: Record<string, unknown> = {}): ProjectConfig =>
  ({
    devices: [],
    macros: [],
    variables: [],
    ...over,
    ui: {
      pages: [{ id: "main", name: "Main", elements: [] }],
      settings: { theme: "dark" },
      ...((over.ui as Record<string, unknown>) ?? {}),
    },
  }) as unknown as ProjectConfig;

const entry = (snapshot: UndoScope): UndoEntry => ({ description: "Edit element", snapshot });

const PAGES = [{ id: "main", name: "Main", elements: [] }] as unknown as UndoScope["pages"];

describe("undoHistoryIsStale", () => {
  it("keeps the history when the refetch changed nothing the entries write", () => {
    // A device bookkeeping write: the project comes back with a new device
    // and an untouched ui. This is the case that lost the reported edit.
    const before = project();
    const after = project({ devices: [{ id: "projector_1" }] });

    expect(undoHistoryIsStale([entry({ pages: PAGES })], before, after)).toBe(false);
  });

  it("keeps the history when the refetch changed nothing at all", () => {
    // A plain reconnect after a network blip.
    expect(undoHistoryIsStale([entry({ pages: PAGES })], project(), project())).toBe(false);
  });

  it("drops the history when the section an entry writes has moved", () => {
    const before = project();
    const after = project({
      ui: { pages: [{ id: "main", name: "Renamed elsewhere", elements: [] }] },
    });

    expect(undoHistoryIsStale([entry({ pages: PAGES })], before, after)).toBe(true);
  });

  it("only weighs the sections the entries actually write", () => {
    // The theme changed elsewhere. A stack of page edits still applies
    // cleanly, because rolling one back writes ui.pages and nothing else.
    const before = project();
    const after = project({ ui: { settings: { theme: "light" } } });

    expect(undoHistoryIsStale([entry({ pages: PAGES })], before, after)).toBe(false);
    expect(undoHistoryIsStale([entry({ settings: { theme: "dark" } as never })], before, after))
      .toBe(true);
  });

  it("weighs every scope an entry can carry", () => {
    // One case per scope: change that section and only that section, and
    // the entry holding it must go stale. A scope this rule cannot read is
    // a scope that would silently overwrite somebody else's change.
    const cases: [UndoScope, Record<string, unknown>][] = [
      [{ pages: PAGES }, { ui: { pages: [] } }],
      [{ settings: {} as never }, { ui: { settings: { theme: "light" } } }],
      [{ master_elements: [] }, { ui: { master_elements: [{ id: "m1" }] } }],
      [{ page_groups: [] }, { ui: { page_groups: [{ id: "g1" }] } }],
      [{ custom_css: "" }, { ui: { custom_css: ".panel { color: red }" } }],
      [{ macros: [] }, { macros: [{ id: "lights_on" }] }],
      [{ variables: [] }, { variables: [{ name: "source" }] }],
    ];

    for (const [snapshot, changed] of cases) {
      const key = Object.keys(snapshot)[0];
      expect(
        undoHistoryIsStale([entry(snapshot)], project(), project(changed)),
        `${key} should go stale when ${key} changes`,
      ).toBe(true);
    }
  });

  it("covers every scope the rollback can write", () => {
    // Guard for the next scope added to UndoScope. A scope this rule cannot
    // read falls back to stale, which is the safe direction but is the
    // over-broad clear all over again for that scope: it would drop the
    // history on a refetch that changed nothing. So the tell is the
    // unchanged case, not the changed one.
    const scopes: (keyof UndoScope)[] = [
      "pages",
      "settings",
      "master_elements",
      "page_groups",
      "custom_css",
      "macros",
      "variables",
    ];

    for (const key of scopes) {
      const snapshot = { [key]: undefined } as UndoScope;
      expect(
        undoHistoryIsStale([entry(snapshot)], project(), project()),
        `scope "${key}" is not weighed by undoHistoryIsStale, so it always reads as stale`,
      ).toBe(false);
    }
  });

  it("does not go stale on a section that is absent one side and empty the other", () => {
    // The server omits master_elements/page_groups/custom_css when unused;
    // a refetch that fills them in with the empty value is not a change.
    const before = project();
    const after = project({ ui: { master_elements: [], page_groups: [], custom_css: "" } });

    expect(
      undoHistoryIsStale(
        [entry({ master_elements: [] }), entry({ page_groups: [] }), entry({ custom_css: "" })],
        before,
        after,
      ),
    ).toBe(false);
  });

  it("weighs redo entries too", () => {
    // A redo entry writes the same sections an undo entry does, so an
    // external change invalidates it the same way.
    const before = project();
    const after = project({ ui: { pages: [] } });

    expect(undoHistoryIsStale([entry({ pages: PAGES })], before, after)).toBe(true);
  });

  it("drops the history when either project is missing", () => {
    // Nothing to compare against, so the safe answer is stale.
    expect(undoHistoryIsStale([entry({ pages: PAGES })], null, project())).toBe(true);
    expect(undoHistoryIsStale([entry({ pages: PAGES })], project(), null)).toBe(true);
  });

  it("is not stale when there is nothing on the stack", () => {
    expect(undoHistoryIsStale([], project(), project({ ui: { pages: [] } }))).toBe(false);
  });
});
