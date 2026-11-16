import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// A plugin's CONFIG_SCHEMA field, or a group's fields, that is not an
// object: OpenAVC refuses to start such a plugin and its card says which
// field, but its settings form is still drawn. The form passes over that
// field and draws the rest, as the server's readers do.

import { SchemaFormRenderer } from "./PluginConfigForm";
import type { SchemaField } from "../../api/types";

function renderForm(schema: Record<string, unknown>) {
  render(
    <SchemaFormRenderer
      schema={schema as Record<string, SchemaField>}
      values={{}}
      onChange={vi.fn()}
    />,
  );
}

describe("a settings field that is not an object", () => {
  it("is passed over, and the fields around it are drawn", () => {
    renderForm({
      host: { type: "string", label: "Host" },
      port: null,
      mode: "fast",
      advanced: {
        type: "group",
        label: "Advanced",
        fields: { retries: { type: "integer", label: "Retries" }, timeout: 5 },
      },
    });

    expect(screen.getByText("Host")).toBeTruthy();
    expect(screen.getByText("Advanced")).toBeTruthy();
    expect(screen.getByText("Retries")).toBeTruthy();
    expect(document.querySelectorAll("input")).toHaveLength(2);
  });

  it("draws a group whose fields are not an object with nothing in it", () => {
    renderForm({
      host: { type: "string", label: "Host" },
      advanced: { type: "group", label: "Advanced", fields: ["retries"] },
    });

    expect(screen.getByText("Advanced")).toBeTruthy();
    expect(document.querySelectorAll("input")).toHaveLength(1);
  });
});
