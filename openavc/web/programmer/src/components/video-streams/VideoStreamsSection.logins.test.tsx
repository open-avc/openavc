import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Video Panel sends a stream's password as the "saved on this system" marker,
// never the password. Edit Stream shows it as "Saved on this system" with
// Change; Save and Test send the marker back unless a new one was typed, and
// the plugin then uses the password it has.

const MARKER = "<saved on this system>";

const STREAM = {
  stream_id: "front_door",
  name: "Front Door",
  rtsp_url: "rtsp://192.0.2.50:554/stream1",
  username: "admin",
  password: MARKER,
  codec_hint: "h264",
  transcode: "auto",
  hardware_accel: "auto",
  status: "idle",
};

vi.mock("../../api/streamsClient", () => ({
  listStreams: vi.fn(),
  createStream: vi.fn(async () => ({})),
  updateStream: vi.fn(async () => ({})),
  deleteStream: vi.fn(),
  probeStream: vi.fn(async () => ({ success: true, codec: "h264" })),
  fetchSnapshot: vi.fn(),
  errorMessage: (e: unknown) => String(e),
}));
vi.mock("../../store/projectStore", () => ({
  useProjectStore: { getState: () => ({ load: vi.fn(async () => {}) }) },
}));
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: { liveState: Record<string, unknown> }) => unknown) =>
    selector({ liveState: {} }),
}));
vi.mock("../../store/toastStore", () => ({ showError: vi.fn(), showSuccess: vi.fn() }));

import * as streamsApi from "../../api/streamsClient";
import { VideoStreamsSection } from "./VideoStreamsSection";

const sent = () => vi.mocked(streamsApi.updateStream).mock.calls[0][1];

beforeEach(() => {
  vi.mocked(streamsApi.listStreams).mockResolvedValue([{ ...STREAM }]);
  vi.mocked(streamsApi.updateStream).mockClear();
  vi.mocked(streamsApi.createStream).mockClear();
  vi.mocked(streamsApi.probeStream).mockClear();
});

async function editStream() {
  render(<VideoStreamsSection embedded />);
  await userEvent.click(await screen.findByTitle("Edit"));
}

describe("Edit Stream and a saved password", () => {
  it("shows Saved on this system with Change, never the marker", async () => {
    await editStream();
    expect(screen.getByText("Saved on this system")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change Password" })).toBeTruthy();
    for (const input of document.querySelectorAll("input")) {
      expect((input as HTMLInputElement).value).not.toBe(MARKER);
    }
  });

  it("sends the marker back when the password is left alone, so the saved one is kept", async () => {
    await editStream();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(streamsApi.updateStream).toHaveBeenCalledTimes(1));
    expect(sent()).toMatchObject({ username: "admin", password: MARKER });
  });

  it("keeps it too when Change is left blank", async () => {
    await editStream();
    await userEvent.click(screen.getByRole("button", { name: "Change Password" }));
    const box = screen.getByLabelText("Password") as HTMLInputElement;
    expect(box.placeholder).toBe("Leave blank to keep the saved one");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(streamsApi.updateStream).toHaveBeenCalledTimes(1));
    expect(sent().password).toBe(MARKER);
  });

  it("sends a new one typed after Change", async () => {
    await editStream();
    await userEvent.click(screen.getByRole("button", { name: "Change Password" }));
    await userEvent.type(screen.getByLabelText("Password"), "N3w-pass");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(streamsApi.updateStream).toHaveBeenCalledTimes(1));
    expect(sent().password).toBe("N3w-pass");
  });

  it("tests with the saved password, by the stream's id", async () => {
    await editStream();
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(streamsApi.probeStream).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamsApi.probeStream).mock.calls[0][0]).toEqual({
      rtsp_url: STREAM.rtsp_url,
      username: "admin",
      password: MARKER,
      stream_id: "front_door",
    });
  });
});

describe("Add Stream", () => {
  it("is a plain password box, and says where a login in the address goes", async () => {
    vi.mocked(streamsApi.listStreams).mockResolvedValue([]);
    render(<VideoStreamsSection embedded />);
    await userEvent.click(await screen.findByRole("button", { name: /Add Stream/ }));

    expect(screen.queryByText("Saved on this system")).toBeNull();
    const box = screen.getByLabelText("Password") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(box.value).toBe("");
    expect(screen.getByText(/A login written into the address moves into Username and Password when you save\./)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(vi.mocked(streamsApi.probeStream)).not.toHaveBeenCalled(); // no address yet
  });
});
