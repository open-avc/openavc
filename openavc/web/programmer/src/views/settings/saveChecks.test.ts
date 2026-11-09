import { describe, it, expect } from "vitest";
import { saveChecks, type SaveCheckInput } from "./saveChecks";

const TLS_ON = { enabled: true, port: 8443, auto_generate: true, cert_file: "", key_file: "" };
const AUTH = { programmer_username: "admin", programmer_password: "***", api_key: "" };

function check(over: Partial<SaveCheckInput> = {}) {
  return saveChecks({ tls: TLS_ON, httpPort: 8080, auth: AUTH, dirtyAuth: undefined, ...over });
}

describe("saveChecks", () => {
  it("refuses nothing on a saved, sound system", () => {
    expect(check()).toEqual({
      tlsPortError: null, tlsProvidedBlank: false, apiKeyNeedsPassword: false, passwordTooShort: false, blocked: false,
    });
  });

  it.each([
    [8080, "HTTPS port must differ from HTTP port"],
    [0, "Port must be between 1 and 65535"],
    [65536, "Port must be between 1 and 65535"],
    [1, null],
    [65535, null],
  ])("judges HTTPS port %i", (port, error) => {
    const result = check({ tls: { ...TLS_ON, port } });
    expect(result.tlsPortError).toBe(error);
    expect(result.blocked).toBe(error !== null);
  });

  it("names the clash with the HTTP port before the range", () => {
    expect(check({ tls: { ...TLS_ON, port: 70000 }, httpPort: 70000 }).tlsPortError)
      .toBe("HTTPS port must differ from HTTP port");
  });

  it("reports the HTTPS port's problem but does not refuse while HTTPS is off", () => {
    const result = check({ tls: { ...TLS_ON, enabled: false, port: 8080 } });
    expect(result.tlsPortError).toBe("HTTPS port must differ from HTTP port");
    expect(result.blocked).toBe(false);
  });

  it.each([
    [{ auto_generate: false, cert_file: "", key_file: "/k" }, true],
    [{ auto_generate: false, cert_file: "/c", key_file: " " }, true],
    [{ auto_generate: false, cert_file: "/c", key_file: "/k" }, false],
    [{ auto_generate: true, cert_file: "", key_file: "" }, false],
    [{ auto_generate: false, enabled: false, cert_file: "", key_file: "" }, false],
  ])("judges your own certificate's files %o", (over, blank) => {
    const result = check({ tls: { ...TLS_ON, ...over } });
    expect(result.tlsProvidedBlank).toBe(blank);
    expect(result.blocked).toBe(blank);
  });

  it("names an API key with no password, and refuses it only once a credential is edited", () => {
    const auth = { programmer_username: "", programmer_password: "", api_key: "***" };
    expect(check({ auth })).toMatchObject({ apiKeyNeedsPassword: true, blocked: false });
    expect(check({ auth, dirtyAuth: { api_key: "***" } })).toMatchObject({ apiKeyNeedsPassword: true, blocked: true });
    expect(check({ auth, dirtyAuth: { programmer_password: "" } })).toMatchObject({ blocked: true });
    expect(check({ auth, dirtyAuth: { programmer_username: "x" } })).toMatchObject({ blocked: false });
  });

  it("counts a stored password as set beside an API key", () => {
    const auth = { programmer_username: "", programmer_password: "***", api_key: "k" };
    expect(check({ auth, dirtyAuth: { api_key: "k" } })).toMatchObject({ apiKeyNeedsPassword: false, blocked: false });
  });

  it("refuses a short password typed in this edit, and nothing else about a password", () => {
    const typed = (programmer_password: string) =>
      check({ auth: { ...AUTH, programmer_password }, dirtyAuth: { programmer_password } });
    expect(typed("short")).toMatchObject({ passwordTooShort: true, blocked: true });
    expect(typed("longenough")).toMatchObject({ passwordTooShort: false, blocked: false });
    expect(typed("")).toMatchObject({ passwordTooShort: false, blocked: false });
    expect(typed("***")).toMatchObject({ passwordTooShort: false });
    expect(check({ auth: { ...AUTH, programmer_password: "short" } }).passwordTooShort).toBe(false);
  });
});
