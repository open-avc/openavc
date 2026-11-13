/**
 * Say why the last thing somebody pressed did not work.
 *
 * The reason has always existed -- the server answers a failed interaction
 * with a sentence written for a person -- and it used to go to a browser
 * console, on a panel screwed to a wall, in kiosk mode, with no keyboard.
 * Everything about how this draws is for that room rather than for a
 * desktop: a band across the whole width instead of a corner card, text
 * sized like the controls around it, and about five seconds to read it.
 *
 * One message at a time, replaced rather than stacked: somebody pressing a
 * dead button four times has one problem, not four, and a wall of cards is
 * how a panel stops being usable at the exact moment it is failing.
 *
 * The panel lends `host`: `settings()`, the project's ui settings, and
 * `touchedNode()`, the node of the control last pressed (or nothing), both
 * read when a message is drawn.
 */

const READ_FOR_MS = 5000;

/** The one band (`#panel-failure-message`), drawn on the document's body (held as `app._failureBand`). */
export class FailureBand {
    constructor(host) {
        this._host = host;
        this._timer = null;
    }

    show(text) {
        // One project-level switch, on by default. Off is for a room that draws
        // its own status and would rather we kept quiet -- not a per-element
        // setting, which would be authoring work for something that only ever
        // happens when something is already wrong.
        const settings = this._host.settings();
        if (settings && settings.show_error_messages === false) return;
        const message = String(text == null ? '' : text).trim();
        if (!message) return;

        let band = document.getElementById('panel-failure-message');
        if (!band) {
            band = document.createElement('div');
            band.id = 'panel-failure-message';
            // Announced rather than only drawn: a panel is the whole interface,
            // so somebody using a screen reader on one has nowhere else to find
            // out that the press did nothing.
            band.setAttribute('role', 'alert');
            band.addEventListener('click', () => this.dismiss());
            document.body.appendChild(band);
        }
        band.textContent = message;
        band.classList.toggle('at-top', this._touchedIsLow());
        band.classList.add('visible');

        clearTimeout(this._timer);
        this._timer = setTimeout(() => this.dismiss(), READ_FOR_MS);
    }

    dismiss() {
        clearTimeout(this._timer);
        this._timer = null;
        const band = document.getElementById('panel-failure-message');
        if (band) band.classList.remove('visible');
    }

    /**
     * Is the control that was just pressed in the bottom half of the screen?
     *
     * The band sits at the bottom, and moves to the top when the answer is yes.
     * A message drawn over the button somebody still has a finger on is the one
     * place it must not be: they cannot read it, and it looks like the button
     * changed under them. Unknown control -- nothing pressed yet, or it has
     * been re-rendered since -- keeps the default.
     */
    _touchedIsLow() {
        const el = this._host.touchedNode();
        if (!el || !el.getBoundingClientRect) return false;
        const box = el.getBoundingClientRect();
        if (!box.height && !box.width) return false;
        return (box.top + box.height / 2) > (window.innerHeight / 2);
    }
}
