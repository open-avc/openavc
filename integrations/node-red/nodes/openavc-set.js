"use strict";

const { serverOf, attachStatus, reply, evaluate, describe } = require("../lib/node-common");

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
        // A node saved before the value field existed reads msg.payload; an
        // empty string is a value somebody typed, not an unset field.
        const type = config.valueType || "msg";
        const source = config.value === undefined || config.value === null ? "payload" : config.value;
        const value = await evaluate(RED, this, msg, source, type, undefined);
        if (value === undefined) {
          // A message with nothing to write is a wiring mistake, not a request
          // to blank the variable.
          throw new Error(`Nothing to write: ${describe(source, type)} is not on the message.`);
        }
        if (value !== null && !["string", "number", "boolean"].includes(typeof value)) {
          throw new Error("A state value must be a string, a number, a boolean or null.");
        }
        return conn.setState(key, value);
      });
    });
  }

  RED.nodes.registerType("openavc-set", OpenAVCSetNode);
};
