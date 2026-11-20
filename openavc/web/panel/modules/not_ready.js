/**
 * A socket the server was not ready to serve: accepted, then closed with
 * NOT_READY_CLOSE and the phase as the reason, `starting`, `failed` or
 * `stopping` (middleware/startup_splash.py). THE rule for what the panel does
 * next (`afterNotReady`).
 *
 * A start takes seconds, so a server still starting is asked again every
 * STARTING_RETRY_MS. A start that failed is shown by loading the page again:
 * until a start succeeds the server answers every page's address with its own
 * page, which says why it could not start, counts down to its next try or
 * offers Start again, and reloads itself onto the panel once a start succeeds.
 * Two panels back off instead, as after any drop, and so does a stop: the
 * Builder's own frame, whose Programmer shows the reason, and a panel opened
 * through Remote Access, since the server's page asks the system's own
 * address how the start is going, which the cloud does not forward, so it
 * would never come back.
 */

import { basePath } from './base_path.js';

/** The server's close for a socket it is not ready to serve. */
export const NOT_READY_CLOSE = 4503;

/** How often a socket turned away while the server starts tries again. */
export const STARTING_RETRY_MS = 1000;

/** Ask again in STARTING_RETRY_MS. */
export const ASK_AGAIN_SOON = 'ask-again-soon';
/** Load the page again, which the server answers with its own page. */
export const SHOW_SERVER_PAGE = 'show-server-page';

/**
 * What the panel does after `event` closed its socket: ASK_AGAIN_SOON,
 * SHOW_SERVER_PAGE, or null to back off as after any drop. `embedded`: the
 * panel is the Builder's own frame.
 */
export function afterNotReady(event, embedded) {
    if (event.code !== NOT_READY_CLOSE) return null;
    if (event.reason === 'starting') return ASK_AGAIN_SOON;
    const shownHere = !embedded && basePath() === '';
    return event.reason === 'failed' && shownHere ? SHOW_SERVER_PAGE : null;
}
