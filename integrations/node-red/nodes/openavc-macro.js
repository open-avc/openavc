"use strict";

const { serverOf, attachStatus, reply } = require("../lib/node-common");

module.exports = function (RED) {
  function OpenAVCMacroNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;
    attachStatus(this, conn);

    this.on("input", (msg, send, done) => {
      reply(this, msg, send, done, async () => {
        const macro = msg.macro || config.macro;
        if (!macro) {
          throw new Error("A macro is required: set one on the node, or send msg.macro.");
        }
        return conn.executeMacro(macro);
      });
    });
  }

  RED.nodes.registerType("openavc-macro", OpenAVCMacroNode);
};
