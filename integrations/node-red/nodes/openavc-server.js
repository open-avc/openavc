"use strict";

const { OpenAVCConnection } = require("../lib/connection");
const { registerAdminRoutes } = require("../lib/admin");

module.exports = function (RED) {
  registerAdminRoutes(RED);

  function OpenAVCServerNode(config) {
    RED.nodes.createNode(this, config);
    this.host = config.host;
    this.port = Number(config.port) || 8080;
    this.tls = !!config.tls;
    this.tlsVerify = !!config.tlsVerify;
    const apiKey = (this.credentials && this.credentials.apiKey) || "";
    this.clientName = String(config.clientName || "").trim();

    // A Node-RED TLS configuration (a company CA, a client certificate)
    // applies its options over the two checkboxes, its own verify setting
    // included.
    let tlsOptions = null;
    if (this.tls && config.tlsConfig) {
      const tlsNode = RED.nodes.getNode(config.tlsConfig);
      if (tlsNode && typeof tlsNode.addTLSOptions === "function") {
        tlsOptions = {};
        tlsNode.addTLSOptions(tlsOptions);
      }
    }

    this.connection = new OpenAVCConnection({
      host: this.host,
      port: this.port,
      tls: this.tls,
      tlsVerify: this.tlsVerify,
      tlsOptions,
      apiKey,
      name: this.clientName,
      logger: {
        log: (m) => this.log(m),
        warn: (m) => this.warn(m),
        error: (m) => this.error(m),
      },
    });
    this.connection.on("error-frame", (frame) => this.warn(`OpenAVC: ${frame.message}`));
    this.connection.connect();

    this.on("close", (_removed, done) => {
      this.connection.close();
      done();
    });
  }

  RED.nodes.registerType("openavc-server", OpenAVCServerNode, {
    credentials: { apiKey: { type: "password" } },
  });
};
