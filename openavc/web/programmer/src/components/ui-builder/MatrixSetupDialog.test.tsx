import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MatrixSetupDialog } from "./MatrixSetupDialog";
import type { MatrixProposalsResponse } from "../../api/matrixProposals";
import type { ProjectConfig, UIElement } from "../../api/types";

const getMatrixProposals = vi.hoisted(() => vi.fn());
vi.mock("../../api/matrixProposals", () => ({ getMatrixProposals }));

// A frame that switches its extracted audio with its own command -- two planes
// off one device, which is what puts the audio tick on screen at all.
const VIDEO_PLANE = {
  id: "output.input",
  device_id: "mx88",
  label: "Outputs · Video",
  destination_child_type: "output",
  route_property: "input",
  source_child_type: "input",
  command: "route",
  command_label: "Route Input to Output",
  audio_plane_id: "output.audio_input",
  confidence: "high" as const,
  from_roster: true,
  why: "",
  warnings: [],
  sources: [
    { value: 1, label: "Input 1" },
    { value: 2, label: "Input 2" },
  ],
  destinations: [
    { value: 1, label: "Output 1", route_key: "device.mx88.output.1.input" },
    { value: 2, label: "Output 2", route_key: "device.mx88.output.2.input" },
  ],
  route: [{ action: "device.command", device: "mx88", command: "route" }],
};

const AUDIO_PLANE = {
  ...VIDEO_PLANE,
  id: "output.audio_input",
  label: "Outputs · Extracted Audio",
  route_property: "audio_input",
  command: "audio_route",
  command_label: "Route Ex-Audio Input to Output",
  audio_plane_id: null,
  destinations: [
    { value: 1, label: "Output 1", route_key: "device.mx88.output.1.audio_input" },
    { value: 2, label: "Output 2", route_key: "device.mx88.output.2.audio_input" },
  ],
  route: [{ action: "device.command", device: "mx88", command: "audio_route" }],
};

const REPLY: MatrixProposalsResponse = {
  device_id: "mx88",
  live: true,
  proposals: [VIDEO_PLANE, AUDIO_PLANE] as MatrixProposalsResponse["proposals"],
};

const PROJECT = {
  devices: [{ id: "mx88", name: "Matrix", driver: "avproedge_acmx" }],
} as unknown as ProjectConfig;

/** A matrix already set up for break-away audio: a key per destination. */
const WITH_AUDIO = {
  id: "matrix_1",
  type: "matrix",
  matrix_config: {
    audio_follow_video: true,
    sources: [
      { value: 1, label: "Input 1" },
      { value: 2, label: "Input 2" },
    ],
    destinations: [
      {
        value: 1,
        label: "Output 1",
        route_key: "device.mx88.output.1.input",
        audio_route_key: "device.mx88.output.1.audio_input",
      },
      {
        value: 2,
        label: "Output 2",
        route_key: "device.mx88.output.2.input",
        audio_route_key: "device.mx88.output.2.audio_input",
      },
    ],
  },
} as unknown as UIElement;

const audioTick = () =>
  screen.getByRole("checkbox", { name: /switch audio separately/i });

const destinationsOf = (patch: Partial<UIElement>) =>
  (patch.matrix_config as { destinations: Record<string, unknown>[] }).destinations;

async function open(element: UIElement, onApply: ReturnType<typeof vi.fn>) {
  render(
    <MatrixSetupDialog
      element={element}
      project={PROJECT}
      onApply={onApply}
      onClose={vi.fn()}
    />,
  );
  // The dialog reads the device before it can show anything about audio.
  await screen.findByRole("checkbox", { name: /switch audio separately/i });
}

beforeEach(() => {
  getMatrixProposals.mockReset();
  getMatrixProposals.mockResolvedValue(REPLY);
});

// Ticking this wrote a key per destination and unticking it wrote them straight
// back, because each row carried its existing key through Apply untouched. With
// the properties panel hiding those fields on a written-out list, that left no
// way anywhere to take the audio readout off a matrix that had one.
describe("the matrix picker's break-away audio tick", () => {
  it("does not opt a new matrix into switching audio separately", async () => {
    const onApply = vi.fn();
    await open({ id: "matrix_1", type: "matrix" } as unknown as UIElement, onApply);

    expect(audioTick()).not.toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const patch = onApply.mock.calls[0][0] as Partial<UIElement>;
    for (const dest of destinationsOf(patch)) {
      expect(dest).not.toHaveProperty("audio_route_key");
    }
    expect(
      (patch.matrix_config as { audio_follow_video?: boolean }).audio_follow_video,
    ).toBe(false);
  });

  it("opens ticked on a matrix that already switches audio separately", async () => {
    await open(WITH_AUDIO, vi.fn());
    expect(audioTick()).toBeChecked();
  });

  it("takes the keys off when it is unticked", async () => {
    const onApply = vi.fn();
    await open(WITH_AUDIO, onApply);

    await userEvent.click(audioTick());
    expect(audioTick()).not.toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const patch = onApply.mock.calls[0][0] as Partial<UIElement>;
    const destinations = destinationsOf(patch);
    expect(destinations).toHaveLength(2);
    for (const dest of destinations) {
      expect(dest).not.toHaveProperty("audio_route_key");
      // The video half is untouched -- this takes the readout off, not the row.
      expect(dest.route_key).toMatch(/\.input$/);
    }
    expect(
      (patch.matrix_config as { audio_follow_video?: boolean }).audio_follow_video,
    ).toBe(false);
  });

  it("keeps the keys when it is left ticked", async () => {
    const onApply = vi.fn();
    await open(WITH_AUDIO, onApply);

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const patch = onApply.mock.calls[0][0] as Partial<UIElement>;
    expect(destinationsOf(patch).map((d) => d.audio_route_key)).toEqual([
      "device.mx88.output.1.audio_input",
      "device.mx88.output.2.audio_input",
    ]);
    expect(
      (patch.matrix_config as { audio_follow_video?: boolean }).audio_follow_video,
    ).toBe(true);
  });

  // No audio plane means no tick on screen, so the dialog was never asked and
  // must not answer: a key somebody wrote by hand stays where they put it.
  it("leaves a hand-authored key alone when the device has no audio plane", async () => {
    getMatrixProposals.mockResolvedValue({
      ...REPLY,
      proposals: [{ ...VIDEO_PLANE, audio_plane_id: null }],
    });
    const onApply = vi.fn();
    render(
      <MatrixSetupDialog
        element={WITH_AUDIO}
        project={PROJECT}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    const applyButton = await screen.findByRole("button", { name: "Apply" });
    await waitFor(() =>
      expect(
        screen.queryByRole("checkbox", { name: /switch audio separately/i }),
      ).toBeNull(),
    );

    await userEvent.click(applyButton);

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const patch = onApply.mock.calls[0][0] as Partial<UIElement>;
    expect(destinationsOf(patch).map((d) => d.audio_route_key)).toEqual([
      "device.mx88.output.1.audio_input",
      "device.mx88.output.2.audio_input",
    ]);
  });
});
