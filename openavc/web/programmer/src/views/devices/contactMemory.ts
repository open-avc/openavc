/**
 * The name and email a person left on a problem report or an audit's
 * "About you", remembered in this browser so the next report fills them in.
 * Never sent anywhere on its own; a private window or blocked storage just
 * starts empty.
 */

const KEY = "openavc.report.contact";

export interface Contact {
  name: string;
  email: string;
}

export function rememberedContact(): Contact {
  try {
    const raw = localStorage.getItem(KEY);
    const stored = raw ? JSON.parse(raw) : null;
    return {
      name: typeof stored?.name === "string" ? stored.name : "",
      email: typeof stored?.email === "string" ? stored.email : "",
    };
  } catch {
    return { name: "", email: "" };
  }
}

/** Keep what was typed; both empty forgets it. */
export function rememberContact(contact: Contact): void {
  const name = contact.name.trim();
  const email = contact.email.trim();
  try {
    if (!name && !email) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, JSON.stringify({ name, email }));
  } catch {
    /* storage blocked: the next report starts empty */
  }
}
