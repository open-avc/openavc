import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../api/cloudClient";
import { messagesFromConversation } from "./aiChatHistory";

function row(role: string, content: string, toolCalls: unknown = null): ChatMessage {
  return {
    id: `${role}-${content.length}`,
    role,
    content,
    input_tokens: 0,
    output_tokens: 0,
    tool_calls: toolCalls as ChatMessage["tool_calls"],
    created_at: "2026-10-09T12:00:00Z",
  };
}

const CALL = { id: "tu_1", name: "get_device_info", input: { device_id: "matrix" } };

// One request as the cloud stores it: the question, a round's tool call and
// tool result as rows of their own, then the reply naming the call it made.
const STORED: ChatMessage[] = [
  row("user", "Add the matrix"),
  row("tool_use", JSON.stringify([CALL])),
  row("tool_result", JSON.stringify([{ type: "tool_result", tool_use_id: "tu_1", content: "{}" }])),
  row("assistant", "Added the matrix.", [CALL]),
];

describe("messagesFromConversation", () => {
  it("draws the question and the reply, not the stored tool rows", () => {
    const messages = messagesFromConversation(STORED);
    expect(messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(messages.some((m) => m.content.includes("tool_use_id"))).toBe(false);
  });

  it("puts the reply's text first, then each tool call it made", () => {
    const [, reply] = messagesFromConversation(STORED);
    expect(reply.contentBlocks).toEqual([
      { type: "text", text: "Added the matrix." },
      { type: "tool", toolCall: { ...CALL, status: "success" } },
    ]);
  });

  it("gives a user message no content blocks", () => {
    const [question] = messagesFromConversation(STORED);
    expect(question.contentBlocks).toBeUndefined();
    expect(question.content).toBe("Add the matrix");
  });
});
