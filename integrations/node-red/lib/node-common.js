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

// Mirror the connection's status on the node's dot, and let go on close.
function attachStatus(node, connection) {
  const show = (status) => node.status(STATUS[status] || STATUS.disconnected);
  show(connection.status);
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

// A configured property that may come from the message (typedInput).
function evaluate(RED, node, msg, value, type, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  try {
    const out = RED.util.evaluateNodeProperty(value, type || "str", node, msg);
    return out === undefined ? fallback : out;
  } catch (e) {
    throw new Error(`could not read ${type} "${value}": ${e.message}`);
  }
}

module.exports = { serverOf, attachStatus, reply, evaluate };
