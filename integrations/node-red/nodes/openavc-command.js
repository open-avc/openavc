"use strict";

const { serverOf, attachStatus, reply, evaluate, describe } = require("../lib/node-common");

module.exports = function (RED) {
  function OpenAVCCommandNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;
    attachStatus(this, conn);

    this.on("input", (msg, send, done) => {
      reply(this, msg, send, done, async () => {
        const device = msg.device || config.device;
        const command = msg.command || config.command;
        if (!device || !command) {
          throw new Error("A device and a command are required: set them on the node, or send msg.device and msg.command.");
        }
        let params = msg.params;
        if (params === undefined) {
          params = await evaluate(RED, this, msg, config.params, config.paramsType, {});
          if (params === undefined) {
            throw new Error(`The parameters, ${describe(config.params, config.paramsType)}, are not on the message.`);
          }
        }
        if (params === null || typeof params !== "object" || Array.isArray(params)) {
          throw new Error("Command parameters must be an object, e.g. {\"input\": 2}.");
        }
        return conn.sendCommand(device, command, params);
      });
    });
  }

  RED.nodes.registerType("openavc-command", OpenAVCCommandNode);
};
