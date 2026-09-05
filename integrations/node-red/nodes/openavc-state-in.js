"use strict";

const { compile, parsePatterns } = require("../lib/glob");
const { serverOf, attachStatus } = require("../lib/node-common");

module.exports = function (RED) {
  function OpenAVCStateInNode(config) {
    RED.nodes.createNode(this, config);
    const server = serverOf(RED, this, config);
    if (!server) return;
    const conn = server.connection;

    const patterns = parsePatterns(config.patterns);
    const matches = compile(patterns.length ? patterns : ["*"]);

    const onState = (key, value, deleted) => {
      if (!matches(key)) return;
      const msg = { topic: key, payload: deleted ? null : value };
      if (deleted) msg.deleted = true;
      this.send(msg);
    };
    // On (re)connect, optionally replay what every matching key holds now, so
    // a flow that starts after the room is running still converges.
    const onSnapshot = (state) => {
      if (!config.replay) return;
      for (const [key, value] of Object.entries(state)) {
        if (matches(key)) this.send({ topic: key, payload: value, replay: true });
      }
    };

    conn.on("state", onState);
    conn.on("snapshot", onSnapshot);
    attachStatus(this, conn);
    if (config.replay && conn.status === "connected") onSnapshot(conn.state);

    this.on("close", (done) => {
      conn.removeListener("state", onState);
      conn.removeListener("snapshot", onSnapshot);
      done();
    });
  }

  RED.nodes.registerType("openavc-state-in", OpenAVCStateInNode);
};
