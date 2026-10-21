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

  it("puts a reply stored without call positions as text first, then each tool call", () => {
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

  it("draws each call where it came in the reply, as it streamed", () => {
    const text = "I'll trace both references.\n\nOne more.\n\nBoth are used by the On button.";
    const first = { id: "tu_1", name: "check_references", input: { id: "a" }, text_offset: 27 };
    const second = { id: "tu_2", name: "check_references", input: { id: "b" }, text_offset: 38 };
    const [, reply] = messagesFromConversation([
      row("user", "What uses these?"),
      row("assistant", text, [second, first]),
    ]);
    expect(reply.contentBlocks).toEqual([
      { type: "text", text: "I'll trace both references." },
      { type: "tool", toolCall: { id: "tu_1", name: "check_references", input: { id: "a" }, status: "success" } },
      { type: "text", text: "One more." },
      { type: "tool", toolCall: { id: "tu_2", name: "check_references", input: { id: "b" }, status: "success" } },
      { type: "text", text: "Both are used by the On button." },
    ]);
    expect(reply.content).toBe(text);
  });

  it("draws calls made before any text first", () => {
    const call = { ...CALL, text_offset: 0 };
    const [, reply] = messagesFromConversation([row("user", "Go"), row("assistant", "Done.", [call])]);
    expect(reply.contentBlocks).toEqual([
      { type: "tool", toolCall: { ...CALL, status: "success" } },
      { type: "text", text: "Done." },
    ]);
  });
});
