"use strict";

/**
 * Routes the EDITOR calls to fill its dropdowns, served by Node-RED's admin
 * API and proxied through the deployed server node's connection -- so the
 * browser never holds the instance's credential.
 *
 * They need the server node to be deployed, because an undeployed config
 * node has no runtime instance and no credential to proxy with. The editor
 * says so when it gets the 404.
 */
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
      res.status(502).json({ error: e.message });
    }
  }

  RED.httpAdmin.get("/openavc/:id/devices", permission, (req, res) => {
    const conn = connectionFor(req, res);
    if (!conn) return;
    answer(res, async () => {
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
        description: (spec && spec.description) || "",
        params: (spec && (spec.params || spec.parameters)) || [],
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
      const data = await conn.fetchJson("/api/state");
      return Object.keys(data.state || {}).sort();
    });
  });
}

module.exports = { registerAdminRoutes };
