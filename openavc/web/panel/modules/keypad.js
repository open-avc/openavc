/**
 * The keypad: a grid of keys that builds an entry and sends it as
 * `ui.submit`, on Enter, or by itself (auto send) once it has all its digits
 * or the person pauses; a display above shows the entry, or a dot a digit on
 * a PIN pad. Only what is drawn is masked: the entry sent is the digits.
 *
 * `KeypadControl` draws the display and the keys and holds the entry and the
 * pending auto send, which a page redraw drops with the keypad (its timer is
 * kept by the panel, which clears them all). The panel keeps the element, its
 * theme style and its caption, and lends `send`, `keepTimer` and `icon`, read
 * at call time.
 *
 * The page review's renderer scan (tests/test_ui_page_review_mirrors.py)
 * credits all of this text to the keypad, the one renderer that hands it a
 * definition, and reads the digit count and send delay defaults off it: the
 * page review and the Builder use the same two numbers.
 */

/** Numeric: Clear and Enter beside the 0. Phone: star and hash, and no Enter. */
const NUMERIC_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'C', '0', 'enter'];
const PHONE_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'];
const KEY_NAMES = { C: 'Clear', enter: 'Enter', '*': 'Star', '#': 'Hash' };

export class KeypadControl {
    constructor(element, host) {
        this.element = element;
        this.host = host;
        this.digits = element.digits ?? 4;
        this.autoSend = element.auto_send ?? false;
        this.autoSendDelay = element.auto_send_delay_ms ?? 1500;
        this.keys = (element.keypad_style || 'numeric') === 'phone' ? PHONE_KEYS : NUMERIC_KEYS;
        this.showDisplay = element.show_display !== false;
        this.masked = element.mask_display === true;
        this.entry = '';
        this.display = null;
        this.pending = null;
    }

    /** Draw the display (unless hidden) and the keys into `el`. */
    mount(el) {
        if (this.showDisplay) {
            this.display = document.createElement('div');
            this.display.className = 'keypad-display';
            this.display.textContent = '';
            el.appendChild(this.display);
        }
        const grid = document.createElement('div');
        grid.className = 'keypad-grid';
        for (const key of this.keys) grid.appendChild(this._key(key));
        el.appendChild(grid);
    }

    _key(key) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'keypad-key';
        // Enter is the panel's own arrow, never a font's return symbol. Few
        // fonts carry U+23CE, so each device drew a different stand-in at a
        // different width, and that one key set the keypad's minimum width.
        // The stylesheet sizes the arrow.
        if (key === 'enter') btn.appendChild(this.host.icon('corner-down-left'));
        else btn.textContent = key;
        btn.setAttribute('aria-label', KEY_NAMES[key] || `Key ${key}`);
        if (key === 'C') btn.classList.add('keypad-clear');
        if (key === 'enter') btn.classList.add('keypad-enter');
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            this.press(key);
        });
        return btn;
    }

    press(key) {
        if (key === 'C') this._clear();
        else if (key === 'enter') this._submit();
        else this._type(key);
        this._show();
    }

    _clear() {
        this.entry = '';
        if (this.pending) { clearTimeout(this.pending); this.pending = null; }
    }

    /**
     * A digit, while the entry is under the keypad's count (a count that is
     * not a number takes none); an auto send goes now if that fills it, else
     * after the pause.
     */
    _type(key) {
        if (this.entry.length < this.digits) this._append(key);
    }

    _append(key) {
        this.entry += key;
        if (this.autoSend && this.entry.length >= this.digits) {
            this._submit();
        } else if (this.autoSend) {
            if (this.pending) clearTimeout(this.pending);
            this.pending = setTimeout(() => this._submit(), this.autoSendDelay);
            this.host.keepTimer(this.pending);
        }
    }

    _submit() {
        if (!this.entry) return;
        this.host.send({ type: 'ui.submit', element_id: this.element.id, value: this.entry });
        this.entry = '';
        this._show();
    }

    _show() {
        if (!this.display) return;
        this.display.textContent = this.masked ? '•'.repeat(this.entry.length) : this.entry;
    }
}
