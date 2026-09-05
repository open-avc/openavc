"use strict";

/**
 * Routes the EDITOR calls to fill its dropdowns, served by Node-RED's admin
 * API and proxied through the deployed server node's connection -- so the
 * browser never holds the instance's credential.
 *
 * They need the server node to be deployed, because an undeployed config
 * node has no runtime instance and no credential to proxy with. The editor
 * says so when it gets the 404.
 *
 * Devices and state keys come from the connection's own state mirror, which
 * every connection has, key or no key. Commands and macros are not in the
 * state stream, so those two ask the REST API, which a claimed system only
 * answers with a key; the reply then says to add one, and that typing the
 * id still works.
 */

const NEEDS_KEY =
  "This system asks for an API key before it will list this. Add the key to the " +
  "server node (Settings › Access in the Programmer), or type the id.";

function registerAdminRoutes(RED) {
  if (RED._openavcAdminRoutes) return;
  RED._openavcAdminRoutes = true;

  const permission = RED.auth.needsPermission("openavc-server.read");

  function connectionFor(req, res) {
    const node = RED.nodes.getNode(req.params.id);
    if (!node || !node.connection) {
      res.status(404).json({ error: "Deploy the OpenAVC server node first, then reopen this dialog." });
      return null;
    }
    return node.connection;
  }

  async function answer(res, work) {
    try {
      res.json(await work());
    } catch (e) {
      const status = e.statusCode === 401 || e.statusCode === 403 ? 403 : 502;
      res.status(status).json({ error: status === 403 ? NEEDS_KEY : e.message });
    }
  }

  // The state mirror is the live truth for what devices exist: every device
  // publishes device.<id>.name, connected or not.
  function devicesFrom(state) {
    const out = [];
    for (const key of Object.keys(state).sort()) {
      const m = /^device\.([^.]+)\.name$/.exec(key);
      if (!m) continue;
      const id = m[1];
      out.push({
        id,
        name: state[key] || id,
        driver: state[`device.${id}.driver`] || "",
        connected: !!state[`device.${id}.connected`],
      });
    }
    return out;
  }

  function normaliseParams(params) {
    if (Array.isArray(params)) return params;
    if (params && typeof params === "object") {
      return Object.entries(params).map(([name, spec]) => ({ name, ...(spec || {}) }));
    }
    return [];
  }

  RED.httpAdmin.get("/openavc/:id/devices", permission, (req, res) => {
    const conn = connectionFor(req, res);
    if (!conn) return;
    answer(res, async () => {
      if (conn.status === "connected") return devicesFrom(conn.state);
      const data = await conn.fetchJson("/api/devices");
      return (data.devices || []).map((d) => ({
        id: d.id,
        name: d.name || d.id,
        driver: d.driver || "",
        connected: !!d.connected,
      }));
    });
  });

  RED.httpAdmin.get("/openavc/:id/devices/:device/commands", permission, (req, res) => {
    const conn = connectionFor(req, res);
    if (!conn) return;
    answer(res, async () => {
      const data = await conn.fetchJson(`/api/devices/${encodeURIComponent(req.params.device)}`);
      return Object.entries(data.commands || {}).map(([name, spec]) => ({
        name,
        description: (spec && (spec.label || spec.help || spec.description)) || "",
        params: normaliseParams(spec && (spec.params || spec.parameters)),
      }));
    });
  });

  RED.httpAdmin.get("/openavc/:id/macros", permission, (req, res) => {
    const conn = connectionFor(req, res);
    if (!conn) return;
    answer(res, async () => {
      const data = await conn.fetchJson("/api/project");
      return (data.macros || []).map((m) => ({ id: m.id, name: m.name || m.id }));
    });
  });

  RED.httpAdmin.get("/openavc/:id/state-keys", permission, (req, res) => {
    const conn = connectionFor(req, res);
    if (!conn) return;
    answer(res, async () => {
      if (conn.status === "connected") return Object.keys(conn.state).sort();
      const data = await conn.fetchJson("/api/state");
      return Object.keys(data.state || {}).sort();
    });
  });
}

module.exports = { registerAdminRoutes, NEEDS_KEY };
