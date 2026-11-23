import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "../../api/errors";

const mocks = vi.hoisted(() => ({
  emit: vi.fn(),
  actions: [] as unknown[],
}));

vi.mock("../../store/pluginStore", () => {
  const state = {
    get extensions() {
      return { views: [], device_panels: [], status_cards: [], context_actions: mocks.actions, panel_elements: [] };
    },
  };
  return { usePluginStore: (selector: (s: unknown) => unknown) => selector(state) };
});

vi.mock("../../api/restClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/restClient")>()),
  emitContextAction: mocks.emit,
}));

import { ContextActionRenderer } from "./PluginExtensions";

function action(pluginId: string, label: string, over: Record<string, unknown> = {}) {
  return {
    id: "refresh", label, context: "device", event: "action.refresh",
    plugin_id: pluginId, plugin_name: `Acme ${pluginId}`, ...over,
  };
}

const onWidgetPage = { page: "plugin", pluginId: "acme_widget" } as const;

beforeEach(() => {
  mocks.emit.mockReset();
  mocks.emit.mockResolvedValue({ status: "emitted" });
});

// Two plugins may give a context action the same id: an entry is named by its
// plugin first. Pressing one plugin's button holds only that button while its
// request runs, never the other plugin's of the same id.
describe("context actions of the same id in two plugins", () => {
  it("holds only the pressed plugin's button while its request runs", async () => {
    let finish: () => void = () => {};
    mocks.emit.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    mocks.actions = [action("acme_a", "Refresh A"), action("acme_b", "Refresh B")];
    render(<ContextActionRenderer place={{ page: "device", deviceId: "w1" }} />);

    fireEvent.click(screen.getByRole("button", { name: /Refresh A/ }));
    expect(mocks.emit).toHaveBeenCalledWith("acme_a", "refresh", { device_id: "w1" });
    expect(screen.getByRole("button", { name: /Refresh A/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Refresh B/ })).not.toBeDisabled();

    await act(async () => { finish(); });
    expect(screen.getByRole("button", { name: /Refresh A/ })).not.toBeDisabled();
  });
});

describe("a plugin's own page", () => {
  it("draws its global and plugin actions, and no other plugin's", () => {
    mocks.actions = [
      action("acme_widget", "Refresh Widget", { context: "global" }),
      action("acme_widget", "Identify Widget", { id: "identify", context: "plugin" }),
      action("acme_widget", "Widget Device", { id: "dev", context: "device" }),
      action("acme_gadget", "Refresh Gadget", { context: "global" }),
    ];
    render(<ContextActionRenderer place={onWidgetPage} heading="Actions" />);

    expect(screen.getByRole("heading", { name: "Actions" })).toBeInTheDocument();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Refresh Widget", "Identify Widget",
    ]);
  });

  it("draws nothing, heading included, for a plugin with no actions there", () => {
    mocks.actions = [action("acme_gadget", "Refresh Gadget", { context: "global" })];
    const { container } = render(<ContextActionRenderer place={onWidgetPage} heading="Actions" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("sends an action its name with only a leading action. taken off, and no payload", async () => {
    mocks.actions = [action("acme_widget", "Wipe", { context: "plugin", event: "action.reaction" })];
    render(<ContextActionRenderer place={onWidgetPage} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Wipe/ })); });
    expect(mocks.emit).toHaveBeenCalledWith("acme_widget", "reaction", {});
  });
});

describe("an action the server refuses", () => {
  it("says the refusal beside the buttons", async () => {
    mocks.emit.mockRejectedValue(new ApiError(404, JSON.stringify({ detail: "Plugin 'acme_widget' not in project" })));
    mocks.actions = [action("acme_widget", "Refresh Widget", { context: "global" })];
    render(<ContextActionRenderer place={onWidgetPage} />);

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Refresh Widget/ })); });
    expect(screen.getByRole("alert")).toHaveTextContent(
      `Couldn't run "Refresh Widget": Plugin 'acme_widget' not in project`,
    );
    expect(screen.getByRole("button", { name: /Refresh Widget/ })).not.toBeDisabled();
  });

  it("clears the refusal when the action is pressed again and runs", async () => {
    mocks.emit.mockRejectedValueOnce(new ApiError(400, JSON.stringify({ detail: "refused" })));
    mocks.actions = [action("acme_widget", "Refresh Widget", { context: "global" })];
    render(<ContextActionRenderer place={onWidgetPage} />);

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Refresh Widget/ })); });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Refresh Widget/ })); });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so for an action whose plugin names no event, and sends nothing", async () => {
    mocks.actions = [action("acme_widget", "Refresh Widget", { context: "global", event: undefined })];
    render(<ContextActionRenderer place={onWidgetPage} />);

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Refresh Widget/ })); });
    expect(mocks.emit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(`Couldn't run "Refresh Widget"`);
  });
});

describe("an action that asks first", () => {
  const wipe = () => action("acme_widget", "Wipe Presets", {
    id: "wipe", context: "plugin", event: "action.wipe", confirm: "Wipe every preset?",
  });

  it("asks its sentence and sends nothing on Cancel", async () => {
    mocks.actions = [wipe()];
    render(<ContextActionRenderer place={onWidgetPage} />);

    fireEvent.click(screen.getByRole("button", { name: /Wipe Presets/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Wipe every preset?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(mocks.emit).not.toHaveBeenCalled();
  });

  it("runs once the question is answered yes", async () => {
    mocks.actions = [wipe()];
    render(<ContextActionRenderer place={onWidgetPage} />);

    fireEvent.click(screen.getByRole("button", { name: /Wipe Presets/ }));
    const yes = screen.getAllByRole("button", { name: "Wipe Presets" })
      .find((b) => b.hasAttribute("data-confirm"));
    await act(async () => { fireEvent.click(yes!); });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(mocks.emit).toHaveBeenCalledWith("acme_widget", "wipe", {});
  });
});
