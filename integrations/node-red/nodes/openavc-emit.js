"use strict";

const { serverOf, attachStatus, reply, evaluate } = require("../lib/node-common");

const PREFIX = "custom.";

module.exports = function (RED) {
  function OpenAVCEmitNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;
    attachStatus(this, conn);

    this.on("input", (msg, send, done) => {
      reply(this, msg, send, done, async () => {
        let event = msg.event || config.event;
        if (!event) {
          throw new Error("An event name is required: set one on the node, or send msg.event.");
        }
        event = String(event).trim();
        // The server accepts custom.* from outside and nothing else; spare the
        // author the prefix.
        if (!event.startsWith(PREFIX)) event = PREFIX + event;
        let payload = evaluate(RED, this, msg, config.payload || "payload", config.payloadType || "msg", {});
        if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
          payload = { value: payload };
        }
        return conn.emitEvent(event, payload);
      });
    });
  }

  RED.nodes.registerType("openavc-emit", OpenAVCEmitNode);
};
