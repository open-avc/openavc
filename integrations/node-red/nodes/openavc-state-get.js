"use strict";

const { globToRegExp } = require("../lib/glob");
const { serverOf, attachStatus } = require("../lib/node-common");

module.exports = function (RED) {
  function OpenAVCStateGetNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;
    attachStatus(this, conn);

    this.on("input", (msg, send, done) => {
      const key = msg.key || config.key;
      if (!key) {
        done(new Error("A state key is required: set one on the node, or send msg.key."));
        return;
      }
      // The mirror is what the socket has told us; with the socket down it
      // is a guess, and a guess is not what a flow asked for.
      if (conn.status !== "connected") {
        done(new Error(`not connected to OpenAVC${conn.detail ? ` (${conn.detail})` : ""}`));
        return;
      }
      let value;
      if (/[*?]/.test(key)) {
        const re = globToRegExp(key);
        value = {};
        for (const k of Object.keys(conn.state).sort()) {
          if (re.test(k)) value[k] = conn.state[k];
        }
      } else {
        value = Object.prototype.hasOwnProperty.call(conn.state, key) ? conn.state[key] : null;
      }
      msg.topic = key;
      try {
        RED.util.setMessageProperty(msg, config.output || "payload", value, true);
      } catch (e) {
        done(new Error(`could not write msg.${config.output}: ${e.message}`));
        return;
      }
      send(msg);
      done();
    });
  }

  RED.nodes.registerType("openavc-state-get", OpenAVCStateGetNode);
};
