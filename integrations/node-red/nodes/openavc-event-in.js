"use strict";

const { compile, parsePatterns } = require("../lib/glob");
const { serverOf, attachStatus } = require("../lib/node-common");

// What a connection with no API key is shown: the events a panel already
// reflects. Mirrors PANEL_VISIBLE_EVENT_PREFIXES in the server's
// core/event_bus.py (a test there pins the two together). A pattern that can
// never match any of these is a node that will sit silent, so it is said.
const PANEL_VISIBLE_PREFIXES = ["custom.", "ui.", "device.", "macro.", "system."];

function unreachableAsPanel(patterns) {
  return patterns.filter((p) => {
    const head = p.split(".")[0];
    if (/[*?]/.test(head)) return false; // a wildcard first segment may match
    return !PANEL_VISIBLE_PREFIXES.some((prefix) => p.startsWith(prefix));
  });
}

module.exports = function (RED) {
  function OpenAVCEventInNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;

    const patterns = parsePatterns(config.patterns);
    const mine = patterns.length ? patterns : ["custom.*"];
    // The server holds one subscription per connection (the union of every
    // node's patterns), so each node filters again for its own.
    const matches = compile(mine);

    const onEvent = (event, payload, timestamp) => {
      if (!matches(event)) return;
      this.send({ topic: event, payload, timestamp });
    };

    // An OpenAVC older than the event doors ignores the subscription in
    // silence; the connection notices and this node says so where it is seen.
    const onLegacy = () => this.status({ fill: "yellow", shape: "ring", text: "OpenAVC too old for events (needs 0.33)" });

    conn.subscribeEvents(this.id, mine);
    conn.on("event", onEvent);
    conn.on("legacy", onLegacy);
    attachStatus(this, conn);
    if (conn.legacy) onLegacy();

    const hidden = conn.role === "panel" ? unreachableAsPanel(mine) : [];
    const onHidden = (status) => {
      if (status === "connected") this.status({ fill: "yellow", shape: "ring", text: `needs an API key for ${hidden[0]}` });
    };
    if (hidden.length) {
      this.warn(
        `Without an API key on the server node, events matching ${hidden.map((p) => `'${p}'`).join(", ")} are not ` +
          `delivered (a keyless connection sees ${PANEL_VISIBLE_PREFIXES.map((p) => p + "*").join(", ")}). ` +
          "Add the key to the server node to hear them."
      );
      onHidden(conn.status);
      conn.on("status", onHidden);
    }

    this.on("close", (done) => {
      conn.removeListener("event", onEvent);
      conn.removeListener("legacy", onLegacy);
      conn.removeListener("status", onHidden);
      conn.unsubscribeEvents(this.id);
      done();
    });
  }

  RED.nodes.registerType("openavc-event-in", OpenAVCEventInNode);
};

module.exports.PANEL_VISIBLE_PREFIXES = PANEL_VISIBLE_PREFIXES;
module.exports.unreachableAsPanel = unreachableAsPanel;
