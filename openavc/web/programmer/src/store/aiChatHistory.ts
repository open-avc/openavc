/**
 * A stored conversation, as the chat pane draws it.
 *
 * The cloud keeps every row of a request: what was asked, the reply (which
 * names every tool call it made), and each round's tool calls and tool results
 * as rows of their own. Only the question and the reply are drawn; the reply
 * already shows its tool calls, and the per-round rows, drawn, came out as
 * assistant bubbles of raw JSON.
 */

import type { ChatMessage } from "../api/cloudClient";
import type { ContentBlock, Message } from "./aiChatStore";

export function messagesFromConversation(rows: ChatMessage[]): Message[] {
  return rows
    .filter((row) => row.role === "user" || row.role === "assistant")
    .map((row) => {
      const msg: Message = {
        id: row.id,
        role: row.role as Message["role"],
        content: row.content,
        toolCalls: row.tool_calls as Message["toolCalls"],
        createdAt: row.created_at,
      };
      if (msg.role === "assistant") {
        const blocks: ContentBlock[] = [];
        if (msg.content) blocks.push({ type: "text", text: msg.content });
        for (const tc of msg.toolCalls ?? []) {
          blocks.push({ type: "tool", toolCall: { ...tc, status: "success" as const } });
        }
        msg.contentBlocks = blocks;
      }
      return msg;
    });
}
