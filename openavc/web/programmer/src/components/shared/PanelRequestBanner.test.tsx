import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The notice on every view while a panel is waiting. One request is named in
// full and can be answered from the notice; several are counted and sent to
// the Dashboard. Nothing shows while Panel access is Anyone on the network.

vi.mock("../../api/restClient", () => ({
  listPanelDevices: vi.fn(),
  approvePanelDevice: vi.fn(),
  denyPanelDevice: vi.fn(),
  revokePanelDevice: vi.fn(),
  renamePanelDevice: vi.fn(),
}));
vi.mock("../../store/toastStore", () => ({ showError: vi.fn() }));

import * as api from "../../api/restClient";
import type { PanelDevice } from "../../api/restClient";
import { usePanelDevicesStore } from "../../store/panelDevicesStore";
import { useNavigationStore } from "../../store/navigationStore";
import { PanelRequestBanner } from "./PanelRequestBanner";
import { panelRequestSentence } from "./panelDevicesCopy";

const ipad: PanelDevice = {
  id: "pd_ipad", status: "pending", name: "", platform: "iPad", address: "10.1.1.50",
  first_seen: "2026-09-25T14:02:11Z", last_seen: "2026-09-25T14:02:11Z", code: "482-915",
};
const more: PanelDevice[] = [
  ipad,
  { ...ipad, id: "pd_2", platform: "Android tablet", address: "10.1.1.51", code: "107-220" },
  { ...ipad, id: "pd_3", platform: "Windows PC", address: "10.1.1.52", code: "993-004" },
];

describe("PanelRequestBanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePanelDevicesStore.setState({
      access: "approved", pending: [], approved: [], denied: [], loaded: true, error: null,
    });
    useNavigationStore.setState({ activeView: "devices" });
  });

  it("names one waiting panel in full, with Approve, Deny and Show all", () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    render(<PanelRequestBanner />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "A panel is asking to connect: code 482-915, iPad at 10.1.1.50.",
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show all" })).toBeInTheDocument();
  });

  it("counts several, with Show all only", () => {
    usePanelDevicesStore.setState({ pending: more });
    render(<PanelRequestBanner />);
    expect(screen.getByRole("status")).toHaveTextContent("3 panels are asking to connect.");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deny" })).toBeNull();
    expect(screen.getByRole("button", { name: "Show all" })).toBeInTheDocument();
  });

  it("is absent with nothing waiting, and with Panel access set to anyone on the network", () => {
    const { rerender } = render(<PanelRequestBanner />);
    expect(screen.queryByRole("status")).toBeNull();
    usePanelDevicesStore.setState({ access: "open", pending: [ipad] });
    rerender(<PanelRequestBanner />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("Approve asks for a name, offering the platform and address, then approves", async () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    vi.mocked(api.approvePanelDevice).mockResolvedValue({ ...ipad, status: "approved", name: "Lobby iPad" });
    render(<PanelRequestBanner />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Name this panel" });
    const field = dialog.querySelector("input") as HTMLInputElement;
    expect(field.value).toBe("iPad at 10.1.1.50");
    await userEvent.clear(field);
    await userEvent.type(field, "Lobby iPad");
    // The dialog's own Approve, not the notice's behind it.
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    expect(api.approvePanelDevice).toHaveBeenCalledWith("pd_ipad", "Lobby iPad");
  });

  it("Deny denies that panel", async () => {
    usePanelDevicesStore.setState({ pending: [ipad] });
    vi.mocked(api.denyPanelDevice).mockResolvedValue({ ...ipad, status: "denied" });
    render(<PanelRequestBanner />);
    await userEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(api.denyPanelDevice).toHaveBeenCalledWith("pd_ipad");
  });

  it("Show all goes to the Dashboard", async () => {
    usePanelDevicesStore.setState({ pending: more });
    render(<PanelRequestBanner />);
    await userEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(useNavigationStore.getState().activeView).toBe("dashboard");
  });
});

describe("panelRequestSentence", () => {
  it("is the singular sentence for one and the count for several", () => {
    expect(panelRequestSentence([ipad])).toBe(
      "A panel is asking to connect: code 482-915, iPad at 10.1.1.50.",
    );
    expect(panelRequestSentence(more)).toBe("3 panels are asking to connect.");
    expect(panelRequestSentence(more.slice(0, 2))).toBe("2 panels are asking to connect.");
  });
});
