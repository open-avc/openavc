"use strict";

const { serverOf, attachStatus, reply, evaluate } = require("../lib/node-common");

module.exports = function (RED) {
  function OpenAVCSetNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;
    attachStatus(this, conn);

    this.on("input", (msg, send, done) => {
      reply(this, msg, send, done, async () => {
        const key = msg.key || config.key;
        if (!key) {
          throw new Error("A state key is required: set one on the node, or send msg.key.");
        }
        const value = evaluate(RED, this, msg, config.value || "payload", config.valueType || "msg", undefined);
        if (value !== null && !["string", "number", "boolean"].includes(typeof value)) {
          throw new Error("A state value must be a string, a number, a boolean or null.");
        }
        return conn.setState(key, value === undefined ? null : value);
      });
    });
  }

  RED.nodes.registerType("openavc-set", OpenAVCSetNode);
};
