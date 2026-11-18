/**
 * An unsaved project edit kept in this browser tab across a page reload.
 *
 * While the tab holds an edit the server does not have (store/saveState.ts
 * `holdsUnsavedEdit`: a save waiting, running, failed or in conflict), a copy
 * of the project is kept in the tab's sessionStorage with the revision it
 * was made on, written a second after the edit and again as the page goes
 * away. A landed save, a Reload Project and a discarded offer remove it.
 * sessionStorage is per tab, so one tab is never offered another's edit, and
 * the key names the system (the Remote Access path), since several systems
 * share one origin through the cloud.
 *
 * On the next load the copy is judged against the server's (`settleDraft`),
 * and again on Restore:
 * - the server still has the revision it was made on: offered back, and a
 *   restore puts it in the store and saves it against that revision;
 * - the server's revision has moved, and the copy names a save whose answer
 *   never came (the page went away first): the server is asked whether that
 *   save made its current revision. Yes: the copy is already there and is
 *   dropped, or it holds edits made after that save and is offered back as
 *   current against the server's revision. A copy is never compared with the
 *   server's by content for this, since the server fills in defaults and the
 *   driver list on every save;
 * - otherwise the revision was moved by another session or a restart, which
 *   starts a new run of revisions: offered as a conflict. A restore shows it
 *   in the conflict state with the revision it was made on, so the server's
 *   revision check refuses a save of it; it is never saved over the newer
 *   project.
 *
 * A login typed into a device's settings is in the clear in the tab's copy
 * until the server has moved it into this system's login file
 * (views/devices/deviceLogins.ts). The copy kept never holds one: each
 * device's connection password and each setting its driver declares secret
 * (`isSecretSetting`) is left empty, the saved-login marker aside. A device
 * whose driver's settings this tab does not know keeps none of its values.
 * A restore fills each value left out from the server's copy where it has
 * one, and names the devices whose login has to be entered again. Plugin
 * logins, the mesh key and saved command parameters never reach the tab's
 * copy in the clear: each has its own save, and the store re-reads the
 * server's copy, markers and all.
 */
import { create } from "zustand";
import type { DeviceConfig, DriverInfo, ProjectConfig } from "../api/types";
import * as api from "../api/restClient";
import { getTunnelPrefix } from "../api/base";
import { onDriverList } from "../api/driverClient";
import { isSecretSetting, PASSWORD_FIELD } from "../components/shared/secretSetting";
import { SAVED_LOGIN } from "../views/devices/deviceLogins";
import { useProjectStore } from "./projectStore";
import { holdsUnsavedEdit } from "./saveState";

export const DRAFT_KEY = "openavc.projectDraft";
const FORMAT = 1;
/** How long after an edit the copy is written, at most. */
const WRITE_DELAY_MS = 1000;
/** How soon the driver list is asked for again when a device's driver is not in it. */
const DRIVER_REFRESH_MS = 30_000;

/** One value a draft left out: a device's setting, in its config or its connection row. */
export interface LeftOut {
  deviceId: string;
  holder: "config" | "connection";
  field: string;
}

export interface ProjectDraft {
  format: typeof FORMAT;
  projectId: string;
  /** The ETag and revision the edit would have been saved against. */
  etag: string | null;
  revision: string | null;
  project: ProjectConfig;
  blanked: LeftOut[];
  /** The save whose answer had not come when the draft was written, if any
   *  (store/projectStoreSave.ts), and whether it sent this very project. */
  operation: string | null;
  sameAsSave: boolean;
}

/** The save out, or unanswered, as the draft was written. */
export interface SaveInFlight {
  operation: string | null;
  sameAsSave: boolean;
}

const NO_SAVE: SaveInFlight = { operation: null, sameAsSave: false };

/** The settings of a device on `driverId` that hold a login, or null when
 *  this tab does not know the driver's settings. */
export type SecretFieldsOf = (driverId: string) => ReadonlySet<string> | null;

type Values = Record<string, unknown>;

function isBlank(value: unknown): boolean {
  return value === undefined || value === null || value === "";
}

/** Whether a setting holds a value somebody typed: anything but blank or
 *  the saved-login marker. */
function holdsValue(value: unknown): boolean {
  return !isBlank(value) && value !== SAVED_LOGIN;
}

function blankHolder(
  holder: Values | undefined,
  secrets: ReadonlySet<string> | null,
  place: Omit<LeftOut, "field">,
  blanked: LeftOut[],
): Values | undefined {
  if (!holder) return holder;
  const kept: Values = { ...holder };
  for (const [field, value] of Object.entries(holder)) {
    if (secrets !== null && !secrets.has(field)) continue;
    if (!holdsValue(value)) continue;
    kept[field] = "";
    blanked.push({ ...place, field });
  }
  return kept;
}

/** The ETag and revision an edit would be saved against. */
export interface DraftBase {
  etag: string | null;
  revision: string | null;
}

/** The copy of `project` a draft keeps: every login typed in it left out. */
export function draftOf(
  project: ProjectConfig,
  base: DraftBase,
  secretsOf: SecretFieldsOf,
  save: SaveInFlight = NO_SAVE,
): ProjectDraft {
  const blanked: LeftOut[] = [];
  const drivers = new Map((project.devices ?? []).map((d) => [d.id, d.driver]));
  const secretsFor = (deviceId: string) => {
    const driver = drivers.get(deviceId);
    return driver === undefined ? null : secretsOf(driver);
  };
  const devices = (project.devices ?? []).map((device): DeviceConfig => ({
    ...device,
    config: blankHolder(device.config, secretsFor(device.id), { deviceId: device.id, holder: "config" }, blanked) ?? device.config,
  }));
  const connections: ProjectConfig["connections"] = {};
  for (const [deviceId, row] of Object.entries(project.connections ?? {})) {
    connections[deviceId] = blankHolder(row, secretsFor(deviceId), { deviceId, holder: "connection" }, blanked) ?? row;
  }
  return {
    format: FORMAT,
    projectId: project.project?.id ?? "",
    etag: base.etag,
    revision: base.revision,
    project: { ...project, devices, connections },
    blanked,
    ...save,
  };
}

function holderIn(project: ProjectConfig, place: LeftOut): Values | undefined {
  if (place.holder === "connection") return project.connections?.[place.deviceId];
  return project.devices?.find((d) => d.id === place.deviceId)?.config;
}

/** The value `server` holds where `draft` left one out, or undefined. */
function serverValue(server: ProjectConfig, place: LeftOut): unknown {
  return holderIn(server, place)?.[place.field];
}

/** The draft's project with each value it left out taken from `server`,
 *  where the server has one; the rest stay empty. */
export function filledFromServer(draft: ProjectDraft, server: ProjectConfig): ProjectConfig {
  const project = structuredClone(draft.project);
  for (const place of draft.blanked) {
    const value = serverValue(server, place);
    const holder = holderIn(project, place);
    if (holder && value !== undefined) holder[place.field] = value;
  }
  return project;
}

/** The names of the devices whose login a restore cannot fill in, in order. */
function devicesToReenter(draft: ProjectDraft, server: ProjectConfig): string[] {
  const names: string[] = [];
  for (const place of draft.blanked) {
    if (!isBlank(serverValue(server, place))) continue;
    const device = draft.project.devices.find((d) => d.id === place.deviceId);
    const name = device?.name || place.deviceId;
    if (!names.includes(name)) names.push(name);
  }
  return names;
}

function isObject(value: unknown): value is object {
  return typeof value === "object" && value !== null;
}

function sameList(a: unknown[], b: unknown): boolean {
  return Array.isArray(b) && a.length === b.length && a.every((item, i) => sameValue(item, b[i]));
}

/** The keys an object holds a value under (JSON leaves out the rest). */
function keysOf(o: object): string[] {
  return Object.keys(o).filter((k) => (o as Values)[k] !== undefined);
}

function sameRecord(a: object, b: object): boolean {
  if (Array.isArray(b)) return false;
  const aKeys = keysOf(a);
  return aKeys.length === keysOf(b).length && aKeys.every((k) => sameValue((a as Values)[k], (b as Values)[k]));
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!isObject(a) || !isObject(b)) return false;
  return Array.isArray(a) ? sameList(a, b) : sameRecord(a, b);
}

export type DraftJudgement = "current" | "stale" | "drop" | "ask";

/** What a draft is to the server's copy, from what the page has
 *  (module docstring); "ask" when only the server can tell (`settleDraft`). */
export function judgeDraft(
  draft: ProjectDraft,
  server: { project: ProjectConfig; revision: string | null },
): DraftJudgement {
  if (draft.projectId !== (server.project.project?.id ?? "")) return "drop";
  if (sameValue(filledFromServer(draft, server.project), server.project)) return "drop";
  if (draft.revision !== null && draft.revision === server.revision) return "current";
  return draft.operation && draft.etag ? "ask" : "stale";
}

/** The server's copy of the project, with its ETag and revision. */
export interface ServerCopy {
  project: ProjectConfig;
  etag: string | null;
  revision: string | null;
}

/** Sends the save named `operation` again (api/projectClient.ts saveProject). */
export type Resend = (project: ProjectConfig, etag: string, operation: string) => Promise<unknown>;

const resendSave: Resend = (project, etag, operation) => api.saveProject(project, etag, "edit", operation);

/**
 * What a draft is to the server's copy, asking the server when the page
 * cannot tell: the server's revision has moved past the draft's, and the
 * draft names a save whose answer never came. That save is sent again under
 * its id, against the draft's revision, with the server's own copy as its
 * body. The server answers a save it recognises as the one that made its
 * current revision with what that save did, and refuses anything else on the
 * revision, which has moved: it applies nothing either way. Recognised, the
 * draft is the server's copy (dropped) or that copy plus later edits
 * (current against the server's revision); refused, it is stale.
 */
export async function settleDraft(
  draft: ProjectDraft,
  server: ServerCopy,
  resend: Resend = resendSave,
): Promise<Exclude<DraftJudgement, "ask">> {
  const judgement = judgeDraft(draft, server);
  if (judgement !== "ask") return judgement;
  if (!draft.operation || !draft.etag) return "stale";
  try {
    await resend(server.project, draft.etag, draft.operation);
  } catch {
    return "stale";
  }
  return draft.sameAsSave ? "drop" : "current";
}

// --- storage -------------------------------------------------------------------

/** The tab's key for the draft of the system this page is served by. */
export function draftKey(): string {
  const system = getTunnelPrefix();
  return system ? `${DRAFT_KEY}:${system}` : DRAFT_KEY;
}

function tabStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readDraft(): ProjectDraft | null {
  try {
    const text = tabStorage()?.getItem(draftKey());
    if (!text) return null;
    const draft = JSON.parse(text) as ProjectDraft;
    const usable = draft?.format === FORMAT && typeof draft.project === "object" && Array.isArray(draft.blanked);
    return usable ? draft : null;
  } catch {
    return null;
  }
}

function writeDraft(draft: ProjectDraft): void {
  const storage = tabStorage();
  try {
    storage?.setItem(draftKey(), JSON.stringify(draft));
  } catch (e) {
    // Too large for the tab's storage, or storage refused: an older copy
    // must not be offered for this edit.
    clearDraft();
    console.warn("The unsaved project could not be kept in this tab:", e);
  }
}

export function clearDraft(): void {
  try {
    tabStorage()?.removeItem(draftKey());
  } catch {
    // Nothing kept, nothing to remove.
  }
}

// --- the offer -----------------------------------------------------------------

export interface DraftOffer {
  kind: "current" | "stale";
  draft: ProjectDraft;
  /** The devices whose login has to be entered again after a restore. */
  reenter: string[];
  /** A restore is under way: neither answer is taken again. */
  busy?: boolean;
}

export const useDraftOffer = create<{ offer: DraftOffer | null }>(() => ({ offer: null }));

/** What the conflict banner says about restored changes that cannot be saved. */
export const STALE_DRAFT_SENTENCE =
  "These changes were made before the project on this system changed, so they cannot be saved over it. " +
  "Reload Project to go back to the saved project.";

/** The server's copy now, or the store's when it cannot be had. */
async function currentServerCopy(): Promise<ServerCopy | null> {
  try {
    const raw = await api.getProject();
    const etag = raw._etag ?? null;
    delete raw._etag;
    return { project: raw, etag, revision: etag ? etag.replace(/"/g, "") : null };
  } catch {
    const { project, etag, revision } = useProjectStore.getState();
    return project ? { project, etag, revision } : null;
  }
}

/**
 * The offer's Restore, judged again against the server's copy now, since the
 * save the draft names can land, or another session save, while the offer is
 * open. Already on the server: nothing to restore, and the store shows the
 * server's copy. Current: back into the store and saved against the server's
 * revision. Stale: shown in the conflict state against the revision it was
 * made on, so the server's revision check refuses a save of it.
 */
export async function restoreDraft(): Promise<void> {
  const offer = useDraftOffer.getState().offer;
  if (!offer || offer.busy) return;
  useDraftOffer.setState({ offer: { ...offer, busy: true } });
  const server = await currentServerCopy();
  const verdict = server ? await settleDraft(offer.draft, server) : "stale";
  const store = useProjectStore.getState();
  // The offer closes once the store holds what the draft is now, so the
  // draft kept is written from that, never removed in between.
  const answered = () => useDraftOffer.setState({ offer: null });
  if (verdict === "drop" || !server) {
    answered();
    clearDraft();
    await store.load();
    return;
  }
  store.updateWithUndo(filledFromServer(offer.draft, server.project), "Restore unsaved changes");
  if (verdict === "current") {
    useProjectStore.setState({ etag: server.etag, revision: server.revision });
    answered();
    await useProjectStore.getState().save();
    return;
  }
  useProjectStore.setState({
    etag: offer.draft.etag,
    revision: offer.draft.revision,
    conflictDetected: true,
    error: STALE_DRAFT_SENTENCE,
  });
  answered();
}

/** The offer's Discard: the draft is gone, the server's project stays. */
export function discardDraft(): void {
  if (useDraftOffer.getState().offer?.busy) return;
  useDraftOffer.setState({ offer: null });
  clearDraft();
}

// --- keeping it ----------------------------------------------------------------

let driverSecrets = new Map<string, ReadonlySet<string>>();
let driversAskedAt = Number.NEGATIVE_INFINITY;

const knownSecrets: SecretFieldsOf = (driverId) => driverSecrets.get(driverId) ?? null;

function secretFieldsFrom(schema: Values | undefined): ReadonlySet<string> {
  const fields = new Set([PASSWORD_FIELD]);
  for (const [key, def] of Object.entries(schema ?? {})) {
    if (isSecretSetting(key, def)) fields.add(key);
  }
  return fields;
}

function rememberDrivers(drivers: DriverInfo[]): void {
  driverSecrets = new Map(drivers.map((d) => [d.id, secretFieldsFrom(d.config_schema)]));
}

// Every driver list the page fetches, so the copy kept never knows less
// than the form a login was typed into: a driver given a secret setting
// since this page first asked is read from the list that form drew.
onDriverList(rememberDrivers);

async function askForDrivers(): Promise<boolean> {
  driversAskedAt = Date.now();
  try {
    rememberDrivers(await api.listDrivers());
    return true;
  } catch {
    return false;
  }
}

type StoreState = ReturnType<typeof useProjectStore.getState>;

/** Whether the store holds the server's copy of the project yet. */
function loadedIn(s: StoreState): boolean {
  return s.project !== null && s.loadGeneration > 0;
}

/** Whether the store holds an edit the server does not have, on a revision
 *  it knows: what a draft is kept for. */
function keepsDraft(s: StoreState): boolean {
  return s.project !== null && s.etag !== null && holdsUnsavedEdit(s);
}

/** Judge the draft an earlier page kept against the first copy this page
 *  loaded: dropped, or offered. */
async function judgeStoredDraft(server: ServerCopy): Promise<void> {
  const draft = readDraft();
  if (!draft) return;
  const verdict = await settleDraft(draft, server);
  if (verdict === "drop") {
    clearDraft();
    return;
  }
  useDraftOffer.setState({ offer: { kind: verdict, draft, reenter: devicesToReenter(draft, server.project) } });
}

/**
 * Keep the store's unsaved edit in the tab from now on, and judge the draft
 * kept by an earlier page against the first copy of the project this page
 * loads. The stored draft is neither written over nor removed until it has
 * been judged and any offer answered. Returns the stop, which writes a draft
 * still waiting.
 */
export function startProjectDraft(): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  // A store that already holds the project (the app shown again after a
  // sign-in) is this page's own: there is nothing earlier to offer.
  let stage: "waiting" | "judging" | "keeping" =
    useProjectStore.getState().loadGeneration > 0 ? "keeping" : "waiting";
  let stopped = false;
  const holding = () => stopped || stage !== "keeping" || useDraftOffer.getState().offer !== null;
  const firstLoad = (s: StoreState) => stage === "waiting" && loadedIn(s);

  const write = () => {
    clearTimeout(timer);
    timer = undefined;
    if (holding()) return;
    const s = useProjectStore.getState();
    if (!s.project || !keepsDraft(s)) return;
    const save = { operation: s.saveOperation, sameAsSave: s.project === s.saveSending };
    writeDraft(draftOf(s.project, s, knownSecrets, save));
    const unknown = s.project.devices.some((d) => knownSecrets(d.driver) === null);
    if (unknown && Date.now() - driversAskedAt > DRIVER_REFRESH_MS) {
      void askForDrivers().then((asked) => asked && write());
    }
  };

  const keep = (s: StoreState) => {
    if (holding()) return;
    if (keepsDraft(s)) {
      timer ??= setTimeout(write, WRITE_DELAY_MS);
      return;
    }
    clearTimeout(timer);
    timer = undefined;
    clearDraft();
  };

  const unsubscribe = useProjectStore.subscribe((s) => {
    if (firstLoad(s) && s.project) {
      stage = "judging";
      void judgeStoredDraft({ project: s.project, etag: s.etag, revision: s.revision }).finally(() => {
        stage = "keeping";
        keep(useProjectStore.getState());
      });
      return;
    }
    keep(s);
  });
  // An answered offer hands the draft back to the store's own state.
  const unsubscribeOffer = useDraftOffer.subscribe((o) => {
    if (o.offer === null) keep(useProjectStore.getState());
  });

  const flush = () => {
    if (timer !== undefined) write();
  };
  window.addEventListener("pagehide", flush);
  // A draft written before the driver list came kept none of a device's
  // values; written again once it is here.
  void askForDrivers().then((asked) => asked && write());

  return () => {
    flush();
    stopped = true;
    unsubscribe();
    unsubscribeOffer();
    window.removeEventListener("pagehide", flush);
  };
}
