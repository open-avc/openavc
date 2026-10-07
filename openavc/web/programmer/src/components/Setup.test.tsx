import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../api/base", () => ({ getTunnelPrefix: () => "" }));
vi.mock("../api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/auth")>()),
  loginWithPassword: vi.fn(async () => {}),
}));

import { Setup } from "./Setup";

function answer(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

async function submit() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("New password"), "commission123");
  await user.type(screen.getByLabelText("Confirm password"), "commission123");
  await user.click(screen.getByRole("button", { name: /set|create|continue/i }));
}

describe("First-run setup", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the server's sentence when the environment holds the username", async () => {
    const sentence =
      "The username is set by OPENAVC_PROGRAMMER_USERNAME in this server's environment. Enter the username it holds.";
    answer(422, { detail: sentence });
    const done = vi.fn();
    render(<Setup onComplete={done} />);
    await submit();
    expect(await screen.findByText(sentence)).toBeInTheDocument();
    expect(done).not.toHaveBeenCalled();
  });

  it("still says someone else set it up on a 409", async () => {
    answer(409, { detail: "This controller is already set up. Log in instead." });
    render(<Setup onComplete={vi.fn()} />);
    await submit();
    expect(await screen.findByText(/just set up by someone else/)).toBeInTheDocument();
  });
});
