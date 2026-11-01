import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// A device that connects over SSH shows OpenAVC's own public key, with the
// one thing to do with it: add it to the device's account.

const getKeyMock = vi.fn();
vi.mock("../../api/deviceClient", () => ({
  getSshPublicKey: () => getKeyMock(),
}));

import { ApiError } from "../../api/errors";
import { SshKeySection, SSH_KEY_HELP, SSH_KEY_TITLE } from "./SshKeySection";

const KEY = {
  public_key: "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7acme openavc@acme-host",
  fingerprint: "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEF",
  key_type: "ssh-rsa",
  bits: 3072,
};

describe("SshKeySection", () => {
  it("shows the key, what to do with it, its fingerprint and a copy button", async () => {
    getKeyMock.mockImplementationOnce(() => Promise.resolve(KEY));
    render(<SshKeySection sectionStyle={{}} sectionTitleStyle={{}} />);
    expect(screen.getByText(SSH_KEY_TITLE)).toBeTruthy();
    expect(screen.getByText(SSH_KEY_HELP)).toBeTruthy();
    expect(await screen.findByText(KEY.public_key)).toBeTruthy();
    expect(screen.getByText(`Fingerprint ${KEY.fingerprint}`)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy the SSH key" })).toBeTruthy();
  });

  it("says why when the key cannot be made", async () => {
    const refusal = new ApiError(
      500,
      JSON.stringify({ detail: "OpenAVC couldn't create its SSH key. Check that the server can write to its data folder, then try again." }),
    );
    getKeyMock.mockImplementationOnce(() => Promise.reject(refusal));
    render(<SshKeySection sectionStyle={{}} sectionTitleStyle={{}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("couldn't create its SSH key");
  });

  it("uses no em dash in what it says", () => {
    expect(SSH_KEY_HELP).not.toMatch(/—/);
  });
});
