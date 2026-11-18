/**
 * The question asked once, on the load after a reload that left a project
 * edit unsaved (store/projectDraft.ts): restore it or discard it. While the
 * system still has the revision the edit was made on, Restore puts it back
 * and saves it; once the project has moved, Open shows it in the conflict
 * state, where it cannot be saved over the newer project. Escape does not
 * answer it, so the edit is never discarded by a stray key.
 */
import { discardDraft, restoreDraft, useDraftOffer, type DraftOffer } from "../../store/projectDraft";
import { ConfirmDialog } from "./ConfirmDialog";

/** What the dialog says about the devices whose login a restore leaves empty. */
export function reenterSentence(names: string[]): string {
  if (names.length === 0) return "";
  const which = names.length === 1 ? "the login" : "the logins";
  return `Logins are not kept with unsaved changes. Enter ${which} again for ${names.join(", ")}.`;
}

export function draftOfferMessage(offer: DraftOffer): string {
  const unsaved = "Changes you made to this project were not saved before the page was reloaded.";
  if (offer.kind === "stale") {
    return (
      "Changes you made to this project were not saved before the page was reloaded, and the project " +
      "on this system has been saved or restarted since. You can open them to look at them, but they " +
      "cannot be saved over the newer project."
    );
  }
  const reenter = reenterSentence(offer.reenter);
  return reenter ? `${unsaved} ${reenter}` : unsaved;
}

export function ProjectDraftOffer() {
  const offer = useDraftOffer((s) => s.offer);
  if (!offer) return null;
  const stale = offer.kind === "stale";
  return (
    <ConfirmDialog
      title={stale ? "Unsaved changes from before the reload" : "Restore unsaved changes?"}
      message={draftOfferMessage(offer)}
      confirmLabel={stale ? "Open them" : "Restore"}
      cancelLabel="Discard"
      dismissable={false}
      onConfirm={restoreDraft}
      onCancel={discardDraft}
    />
  );
}
