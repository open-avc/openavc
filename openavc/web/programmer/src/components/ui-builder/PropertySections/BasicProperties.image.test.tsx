import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectConfig, UIElement } from "../../../api/types";
import { BasicProperties } from "./BasicProperties";

vi.mock("../../../api/restClient", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../api/restClient")>(),
  listAssets: vi.fn(async () => ({ assets: [
    { name: "logo.png", type: "image", size: 1000 },
  ] })),
}));

const project = { devices: [], macros: [], ui: { pages: [] } } as unknown as ProjectConfig;
const probes: ImageProbe[] = [];

class ImageProbe {
  src = "";
  naturalWidth = 0;
  naturalHeight = 0;
  onload: (() => void) | null = null;
  constructor() { probes.push(this); }
  finish(width = 800, height = 400) {
    this.naturalWidth = width;
    this.naturalHeight = height;
    act(() => { this.onload?.(); });
  }
}

function Editor({ aspect, onPatch }: {
  aspect?: number;
  onPatch?: (patch: Partial<UIElement>) => void;
} = {}) {
  const [element, setElement] = useState<UIElement>({
    id: "logo", type: "image", aspect_lock: aspect,
  } as UIElement);
  return <>
    <BasicProperties element={element} project={project} pages={[]}
      // Match the Builder's immutable, render-scoped patch callback. Retaining
      // it across an image load must not replace newer source or label edits.
      onChange={(patch) => {
        onPatch?.(patch);
        setElement({ ...element, ...patch });
      }} />
    <button onClick={() => setElement({ ...element, aspect_lock: 3 })}>Set ratio</button>
    <button onClick={() => setElement({ id: "other", type: "image" } as UIElement)}>Select another</button>
    <output data-testid="element">{JSON.stringify(element)}</output>
  </>;
}

const saved = () => JSON.parse(screen.getByTestId("element").textContent!);
const urlField = () => screen.getByPlaceholderText("Or enter external URL...");
const selectUrl = (src: string) => fireEvent.change(urlField(), { target: { value: src } });
const latestProbe = () => probes[probes.length - 1];

beforeEach(() => {
  probes.length = 0;
  vi.stubGlobal("Image", ImageProbe);
});
afterEach(() => vi.unstubAllGlobals());

describe("image source and intrinsic aspect", () => {
  it("keeps the first asset selection when the dimensions arrive", async () => {
    render(<Editor />);
    fireEvent.click(screen.getByRole("button", { name: "Choose Image" }));
    fireEvent.click(await screen.findByRole("img", { name: "logo.png" }));
    expect(saved().src).toBe("assets://logo.png");
    expect(latestProbe().src).toContain("logo.png");
    latestProbe().finish();
    expect(saved()).toMatchObject({ src: "assets://logo.png", aspect_lock: 2 });
    expect(screen.getByRole("img", { name: "logo.png" })).toBeInTheDocument();
  });

  it("keeps an external URL and alt text edited while the image loads", () => {
    render(<Editor />);
    selectUrl("https://example.test/first.png");
    const earlier = latestProbe();
    fireEvent.change(screen.getByPlaceholderText("Describe the image"), {
      target: { value: "Room logo" },
    });
    earlier.finish();
    latestProbe().finish(600, 200);
    expect(saved()).toMatchObject({
      src: "https://example.test/first.png", label: "Room logo", aspect_lock: 3,
    });
  });

  it("ignores an older image that loads after a replacement", () => {
    render(<Editor />);
    selectUrl("https://example.test/first.png");
    const earlier = latestProbe();
    selectUrl("https://example.test/second.png");
    latestProbe().finish(300, 600);
    earlier.finish();
    expect(saved()).toMatchObject({ src: "https://example.test/second.png", aspect_lock: 0.5 });
  });

  it("does not apply dimensions after clearing the source", () => {
    render(<Editor />);
    selectUrl("https://example.test/first.png");
    const earlier = latestProbe();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    earlier.finish();
    expect(saved().src).toBeUndefined();
    expect(saved().aspect_lock).toBeUndefined();
  });

  it("preserves an explicit ratio entered during loading", () => {
    render(<Editor />);
    selectUrl("https://example.test/first.png");
    const earlier = latestProbe();
    fireEvent.click(screen.getByRole("button", { name: "Set ratio" }));
    earlier.finish();
    expect(saved()).toMatchObject({ src: "https://example.test/first.png", aspect_lock: 3 });
  });

  it("does not update a different element selected during loading", () => {
    render(<Editor />);
    selectUrl("https://example.test/first.png");
    const earlier = latestProbe();
    fireEvent.click(screen.getByRole("button", { name: "Select another" }));
    earlier.finish();
    expect(saved()).toEqual({ id: "other", type: "image" });
  });

  it.each([0, 1.5])("leaves an existing aspect setting of %s alone", (aspect) => {
    render(<Editor aspect={aspect} />);
    selectUrl("https://example.test/first.png");
    expect(probes).toHaveLength(0);
    expect(saved()).toMatchObject({ src: "https://example.test/first.png", aspect_lock: aspect });
  });

  it("does not update after the properties panel unmounts", () => {
    const onChange = vi.fn();
    const { unmount } = render(<Editor onPatch={onChange} />);
    selectUrl("https://example.test/first.png");
    expect(probes.length).toBeGreaterThan(0);
    unmount();
    probes.forEach((probe) => probe.finish());
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith({ src: "https://example.test/first.png" });
  });
});
