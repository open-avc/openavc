"use strict";

const { compile, parsePatterns } = require("../lib/glob");
const { serverOf, attachStatus } = require("../lib/node-common");

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

    conn.subscribeEvents(this.id, mine);
    conn.on("event", onEvent);
    attachStatus(this, conn);

    this.on("close", (done) => {
      conn.removeListener("event", onEvent);
      conn.unsubscribeEvents(this.id);
      done();
    });
  }

  RED.nodes.registerType("openavc-event-in", OpenAVCEventInNode);
};
