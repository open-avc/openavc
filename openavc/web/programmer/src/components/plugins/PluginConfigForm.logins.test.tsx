import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A plugin's login (a field the one secret rule calls a secret, at the top
// level or in a group) never shows a value. The server sends the "saved on
// this system" marker in its place; the form shows it as "Saved on this
// system" with Change, and a login typed there goes out on its own Save,
// never through the form's save-as-you-type.

import { SchemaFormRenderer, configValueAt, withConfigValueAt } from "./PluginConfigForm";
import { SAVED_LOGIN } from "../../views/devices/deviceLogins";
import type { SchemaField } from "../../api/types";

const SCHEMA: Record<string, SchemaField> = {
  host: { type: "string", label: "Host" },
  password: { type: "string", label: "Password" },
  connection: {
    type: "group",
    label: "Connection",
    fields: {
      api_key: { type: "string", label: "API key", secret: true } as SchemaField,
      region: { type: "string", label: "Region" },
    },
  },
};

function renderForm(
  values: Record<string, unknown>,
  { needsLogin = [] as string[], onSaveLogin = vi.fn(async () => true as boolean | void) } = {},
) {
  const onChange = vi.fn();
  render(
    <SchemaFormRenderer
      schema={SCHEMA}
      values={values}
      onChange={onChange}
      needsLogin={needsLogin}
      onSaveLogin={onSaveLogin}
    />,
  );
  return { onChange, onSaveLogin };
}

function noInputHolds(text: string) {
  for (const input of document.querySelectorAll("input")) {
    expect((input as HTMLInputElement).value).not.toBe(text);
  }
}

describe("a plugin login saved on this system", () => {
  it("shows Saved on this system with Change, never the marker or a value", () => {
    renderForm({ host: "broker.local", password: SAVED_LOGIN, connection: { api_key: SAVED_LOGIN } });

    expect(screen.getAllByText("Saved on this system")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Change Password" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change API key" })).toBeTruthy();
    noInputHolds(SAVED_LOGIN);
  });

  it("keeps a login typed after Change out of the form, and sends it only on its own Save", async () => {
    const { onChange, onSaveLogin } = renderForm({ password: SAVED_LOGIN });

    await userEvent.click(screen.getByRole("button", { name: "Change Password" }));
    const box = screen.getByLabelText("Password") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(box.getAttribute("autocomplete")).toBe("new-password");
    expect(box.placeholder).toBe("Leave blank to keep the saved one");
    await userEvent.type(box, "N3w-secret");

    expect(onChange).not.toHaveBeenCalled();
    expect(onSaveLogin).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Save Password" }));
    expect(onSaveLogin).toHaveBeenCalledWith(["password"], "N3w-secret");
    expect(onChange).not.toHaveBeenCalled();
    expect(await screen.findByText("Saved on this system")).toBeTruthy();
  });

  it("goes back to the saved one on Cancel, or on Save left blank, with nothing sent", async () => {
    const { onSaveLogin } = renderForm({ password: SAVED_LOGIN });

    await userEvent.click(screen.getByRole("button", { name: "Change Password" }));
    await userEvent.type(screen.getByLabelText("Password"), "half");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Saved on this system")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Change Password" }));
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Save Password" }));
    expect(screen.getByText("Saved on this system")).toBeTruthy();
    expect(onSaveLogin).not.toHaveBeenCalled();
  });

  it("keeps the box open with what was typed when the save did not go through", async () => {
    const onSaveLogin = vi.fn(async () => false);
    renderForm({ password: SAVED_LOGIN }, { onSaveLogin });

    await userEvent.click(screen.getByRole("button", { name: "Change Password" }));
    await userEvent.type(screen.getByLabelText("Password"), "N3w-secret");
    await userEvent.click(screen.getByRole("button", { name: "Save Password" }));

    expect(onSaveLogin).toHaveBeenCalled();
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("N3w-secret");
    expect(screen.queryByText("Saved on this system")).toBeNull();
  });
});

describe("a plugin login this system does not have", () => {
  it("is an empty box saying so, saved by its path in the group", async () => {
    const { onSaveLogin } = renderForm(
      { password: SAVED_LOGIN, connection: { api_key: SAVED_LOGIN } },
      { needsLogin: ["connection.api_key"] },
    );

    expect(screen.getAllByText("Saved on this system")).toHaveLength(1);
    expect(screen.getByText("Not saved on this system. Enter it here.")).toBeTruthy();
    const box = screen.getByLabelText("API key") as HTMLInputElement;
    expect(box.value).toBe("");
    const save = screen.getByRole("button", { name: "Save API key" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();

    await userEvent.type(box, "k-123456");
    await userEvent.click(save);
    expect(onSaveLogin).toHaveBeenCalledWith(["connection", "api_key"], "k-123456");
  });
});

describe("a plugin login with nothing saved yet", () => {
  it("is a password box with its own Save", async () => {
    const { onChange, onSaveLogin } = renderForm({ password: "" });

    const box = screen.getByLabelText("Password") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(screen.queryByText("Saved on this system")).toBeNull();
    await userEvent.type(box, "First-one");
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save Password" }));
    expect(onSaveLogin).toHaveBeenCalledWith(["password"], "First-one");
  });

  it("leaves every other field saving as it always has", async () => {
    const { onChange } = renderForm({ host: "", password: SAVED_LOGIN });
    await userEvent.type(screen.getAllByRole("textbox")[0], "b");
    expect(onChange).toHaveBeenCalledWith("host", "b");
  });
});

describe("a login field with no save of its own (a surface view's settings)", () => {
  it("is a masked box that never shows the saved one", () => {
    const onChange = vi.fn();
    render(
      <SchemaFormRenderer
        schema={{ password: { type: "string", label: "Password" } }}
        values={{ password: SAVED_LOGIN }}
        onChange={onChange}
      />,
    );
    const box = screen.getByLabelText("Password") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(box.value).toBe("");
    expect(box.placeholder).toBe("Saved on this system");
  });
});

describe("a config path", () => {
  it("sets and reads a value inside a group without touching the rest", () => {
    const config = { host: "a", connection: { api_key: SAVED_LOGIN, region: "eu" } };
    const next = withConfigValueAt(config, ["connection", "api_key"], "k");
    expect(next).toEqual({ host: "a", connection: { api_key: "k", region: "eu" } });
    expect(config.connection.api_key).toBe(SAVED_LOGIN);
    expect(configValueAt(next, ["connection", "api_key"])).toBe("k");
    expect(configValueAt(next, ["missing", "x"])).toBeUndefined();
  });
});
