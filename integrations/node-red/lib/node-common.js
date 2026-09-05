"use strict";

const STATUS = {
  connected: { fill: "green", shape: "dot", text: "connected" },
  connecting: { fill: "yellow", shape: "ring", text: "connecting" },
  disconnected: { fill: "red", shape: "ring", text: "disconnected" },
};

// The server node this node is attached to, or null (with the node marked)
// when the config points at nothing.
function serverOf(RED, node, config) {
  const server = RED.nodes.getNode(config.server);
  if (!server || !server.connection) {
    node.status({ fill: "red", shape: "ring", text: "no server" });
    node.error("No OpenAVC server is configured on this node.");
    return null;
  }
  return server;
}

// Mirror the connection's status on the node's dot, with the reason a
// connection is down when there is one, and let go on close.
function attachStatus(node, connection) {
  const show = (status, detail) => {
    const base = STATUS[status] || STATUS.disconnected;
    node.status(status === "disconnected" && detail ? { ...base, text: `disconnected: ${detail}` } : base);
  };
  show(connection.status, connection.detail);
  connection.on("status", show);
  node.on("close", () => connection.removeListener("status", show));
}

// One request/reply node body: run `work`, put the result on msg.payload,
// send it, and report a failure to Catch nodes as well.
async function reply(node, msg, send, done, work) {
  let result;
  try {
    result = await work();
  } catch (e) {
    result = { success: false, error: e.message };
  }
  msg.payload = result;
  send(msg);
  if (result.success) done();
  else done(new Error(result.error));
}

// A configured property that may come from the message, the flow or global
// context, an environment variable, or a JSONata expression (typedInput).
// Resolves to `fallback` for an empty field, and to undefined for a message
// property that is not there -- the caller decides whether that is an error.
function evaluate(RED, node, msg, value, type, fallback) {
  // An empty field is "nothing configured" -- except a fixed string, where
  // "" is a value somebody typed (clearing a request variable, say).
  if (value === undefined || value === null) return Promise.resolve(fallback);
  if (value === "" && type !== "str") return Promise.resolve(fallback);
  return new Promise((resolve, reject) => {
    try {
      RED.util.evaluateNodeProperty(value, type || "str", node, msg, (err, out) => {
        if (err) reject(new Error(`could not read ${type} "${value}": ${err.message || err}`));
        else resolve(out);
      });
    } catch (e) {
      reject(new Error(`could not read ${type} "${value}": ${e.message}`));
    }
  });
}

// What a typedInput field is called in a sentence: `msg.payload`, `flow.x`.
function describe(value, type) {
  return type === "msg" || type === "flow" || type === "global" ? `${type}.${value}` : `the ${type} value`;
}

module.exports = { serverOf, attachStatus, reply, evaluate, describe };
