import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// A device that connects over SSH shows OpenAVC's own public key, with the
// one thing to do with it: add it to the device's account.

const getKeyMock = vi.fn();
vi.mock("../../api/deviceClient", () => ({
  getSshPublicKey: () => getKeyMock(),
}));

import { ApiError } from "../../api/errors";
import { SshKeySection, SSH_KEY_HELP, SSH_KEY_TITLE, canUseSshKey } from "./SshKeySection";

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

// The card says "In Edit, choose key sign-in". A driver that signs in with a
// password only has no such choice, so its devices get no card.
describe("canUseSshKey", () => {
  const offersKey = {
    config_schema: { ssh_auth_method: { type: "enum", values: ["password", "key"] } },
    default_config: { ssh_auth_method: "password" },
  };
  const passwordOnly = {
    config_schema: { username: { type: "string" } },
    default_config: { ssh_auth_method: "password" },
  };

  it("shows for a driver that offers key sign-in, whatever the device is set to", () => {
    expect(canUseSshKey(offersKey, { ssh_auth_method: "password" })).toBe(true);
    expect(canUseSshKey(offersKey, {})).toBe(true);
  });

  it("does not show for a password-only driver", () => {
    expect(canUseSshKey(passwordOnly, {})).toBe(false);
    expect(canUseSshKey(passwordOnly, { ssh_auth_method: "Password" })).toBe(false);
  });

  it("shows when the device is set to key sign-in", () => {
    expect(canUseSshKey(passwordOnly, { ssh_auth_method: "key" })).toBe(true);
  });

  it("shows when nothing names a sign-in method, because the key is what signs in then", () => {
    expect(canUseSshKey({ config_schema: {}, default_config: {} }, {})).toBe(true);
    expect(canUseSshKey(undefined, undefined)).toBe(true);
  });

  it("reads a choice written as an option object", () => {
    const labelled = {
      config_schema: { ssh_auth_method: { values: [{ value: "password" }, { value: "key", label: "Key" }] } },
      default_config: { ssh_auth_method: "password" },
    };
    expect(canUseSshKey(labelled, {})).toBe(true);
  });
});
