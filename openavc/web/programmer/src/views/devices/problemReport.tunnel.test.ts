/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "http://localhost:8001/tunnel/t1/programmer/" }
 *
 * A tab opened through Remote Access: its origin is the cloud's own, so the
 * browser never sends a report itself from here.
 */
import { describe, it, expect, vi } from "vitest";
import { inRemoteSession } from "../../api/auth";
import { sendReport } from "./problemReport";

describe("Send inside Remote Access", () => {
  it("never tries the browser after the server's network failure", async () => {
    const post = vi.fn();
    const fetchFile = vi.fn();
    expect(inRemoteSession()).toBe(true);
    const outcome = await sendReport({
      serverSend: async () => ({
        name: "openavc-problem-report-acme_widget-20261030-1042.zip",
        report_id: "6f1c2b9e-1111-4c4c-9a9a-2b2b2b2b2b2b",
        sent: false,
        reference: null,
        failure: "network",
        status: null,
        report_url: "https://cloud.openavc.com/api/v1/reports",
      }),
      fetchFile,
      post,
      remote: inRemoteSession(),
      kind: "problem_report",
      openavcVersion: "0.38.0",
    });
    expect(outcome).toMatchObject({ kind: "failed", failure: "network" });
    expect(fetchFile).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});
