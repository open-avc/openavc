/**
 * A stored conversation, as the chat pane draws it.
 *
 * The cloud keeps every row of a request: what was asked, the reply (which
 * names every tool call it made), and each round's tool calls and tool results
 * as rows of their own. Only the question and the reply are drawn; the reply
 * already shows its tool calls, and the per-round rows, drawn, came out as
 * assistant bubbles of raw JSON.
 *
 * The reply's text is stored as one piece. Each call it made carries
 * `text_offset`, how much of that text came before it, so the calls are drawn
 * where they came while the reply streamed. A reply stored before the cloud
 * recorded that draws its text first, then its calls.
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
        msg.contentBlocks = replyBlocks(msg.content, (row.tool_calls ?? []) as StoredCall[]);
      }
      return msg;
    });
}

interface StoredCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  text_offset?: number;
}

function replyBlocks(text: string, calls: StoredCall[]): ContentBlock[] {
  const tool = ({ text_offset: _at, ...call }: StoredCall): ContentBlock => (
    { type: "tool", toolCall: { ...call, status: "success" as const } }
  );
  const placed = calls.length > 0 && calls.every((c) => typeof c.text_offset === "number");
  if (!placed) {
    return [...(text ? [{ type: "text" as const, text }] : []), ...calls.map(tool)];
  }
  const blocks: ContentBlock[] = [];
  // Rounds are stored joined by a blank line; the one after a call starts with it.
  const push = (piece: string) => {
    const shown = piece.replace(/^\n\n/, "");
    if (shown) blocks.push({ type: "text", text: shown });
  };
  let from = 0;
  const inOrder = [...calls].sort((a, b) => (a.text_offset ?? 0) - (b.text_offset ?? 0));
  for (const call of inOrder) {
    const at = Math.min(Math.max(call.text_offset ?? 0, from), text.length);
    push(text.slice(from, at));
    blocks.push(tool(call));
    from = at;
  }
  push(text.slice(from));
  return blocks;
}
