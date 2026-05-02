/**
 * Centralized doc anchors for the Driver Builder.
 *
 * Each entry points at the published docs site, which mirrors
 * `openavc/docs/creating-drivers.md` and `openavc-drivers/docs/writing-simulators.md`.
 * Anchors follow Starlight's GitHub-style slugification of the headers in
 * those files, so the doc source is the source of truth.
 *
 * If the docs site URL ever changes, update DOCS_BASE here and every
 * link gets repointed.
 */

const DOCS_BASE = "https://docs.openavc.com";

export const DOCS = {
  // General tab
  general: `${DOCS_BASE}/creating-drivers/#top-level-fields`,
  helpFields: `${DOCS_BASE}/creating-drivers/#top-level-fields`,

  // Connection tab sub-sections
  transport: `${DOCS_BASE}/creating-drivers/#step-by-step-walkthrough`,
  auth: `${DOCS_BASE}/creating-drivers/#auth-section`,
  onConnect: `${DOCS_BASE}/creating-drivers/#on_connect-section`,
  frameParser: `${DOCS_BASE}/creating-drivers/#frame_parser-advanced`,
  configSchema: `${DOCS_BASE}/creating-drivers/#config_schema-entry`,

  // Behavior tab sub-sections
  stateVariables: `${DOCS_BASE}/creating-drivers/#state_variables-entry`,
  commands: `${DOCS_BASE}/creating-drivers/#commands-entry`,
  responses: `${DOCS_BASE}/creating-drivers/#responses-entry`,
  polling: `${DOCS_BASE}/creating-drivers/#polling-section`,
  deviceSettings: `${DOCS_BASE}/creating-drivers/#device_settings-entry`,

  // Single-section tabs
  discovery: `${DOCS_BASE}/creating-drivers/#discovery-hints`,
  simulation: `${DOCS_BASE}/writing-simulators/`,
};
