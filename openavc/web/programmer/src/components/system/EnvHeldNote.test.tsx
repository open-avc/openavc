import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EnvHeldNote } from "./EnvHeldNote";

afterEach(cleanup);

describe("firmware-owned setting guidance", () => {
  it("explains the owner without directing an integrator to service files", () => {
    render(<EnvHeldNote variable="OPENAVC_BIND" deploymentType="linux_package" configurationOwner="firmware" />);
    const note = screen.getByTestId("env-held-note");
    expect(note.textContent).toContain("managed by the product's firmware");
    expect(note.textContent).toContain("Contact product support");
    expect(note.textContent).not.toContain("OPENAVC_BIND");
    expect(note.textContent).not.toContain("systemctl");
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("retains ordinary Linux installation instructions", () => {
    render(<EnvHeldNote variable="OPENAVC_BIND" deploymentType="linux_package" />);
    expect(screen.getByTestId("env-held-note").textContent).toContain("sudo systemctl edit openavc");
    expect(screen.getByRole("link").getAttribute("href")).toContain("hardened-deployment");
  });
  it("adds no restriction message to an editable firmware setting", () => {
    render(<EnvHeldNote variable={undefined} deploymentType="linux_package" configurationOwner="firmware" />);
    expect(screen.queryByTestId("env-held-note")).toBeNull();
  });
});
