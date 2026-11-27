/**
 * The list: rows a person taps to pick one (selectable), several
 * (multi_select) or none (static, or action, which only sends), from the
 * list's own items or from state keys matching a pattern, with the row the
 * device reports lit.
 *
 * A ListControl draws its rows and holds what drawing them depends on (the
 * row colours, the style, the picked values, the rows it last drew); the
 * panel registers it on the list's bindings as `_list` and asks
 * `rowsFromState` and `showListSelection` when state changes.
 *
 * The picked rows are lit, whatever the style: drawing the rows lights them,
 * a tap on a selectable or multi-select row lights it at once (before the
 * device answers), and a new reading of the selection (`showListSelection`)
 * lights the row it names, taking over from a tap. A static list is read-only:
 * it shows the row its selection reports and takes no tap. Which row a
 * reading names is compare.js `pickKey`, as a map's key is picked: the exact
 * value, then one apart from case, then one the reading equals as a boolean
 * or number. A pick is kept by its row's value as text (`rowKey`), so a row
 * whose value is 0 is "0", never blank.
 *
 * The page review reads this module's text for what a list reads off its
 * definition (tests/test_ui_page_review_mirrors.py credits it to the list's
 * renderer, the only one that hands it an element), so a definition is read
 * here only through a parameter named `element`.
 */

import { pickKey } from './compare.js';
import { nothingToShow } from './readings.js';

/** A row's value as the text its pick is kept by: a missing one is blank, 0 is "0". */
const rowKey = (value) => (value === undefined || value === null ? '' : String(value));

const DEFAULT_ITEM_BG = '#2a2a4e';
const DEFAULT_ITEM_ACTIVE_BG = '#42a5f5';

/** A list's rows, drawn into its element below the caption. */
export class ListControl {
    /** `style` is the list's style with the theme's defaults under it. */
    constructor(host, element, el, style) {
        this.host = host;
        this.element = element;
        this.listStyle = element.list_style || 'selectable';
        // 44px at the panel's rem base, the height a row has always had.
        this.itemHeight = element.item_height || 44 / host.remBasePx;
        this.itemBg = style.item_bg || DEFAULT_ITEM_BG;
        this.itemActiveBg = style.item_active_bg || DEFAULT_ITEM_ACTIVE_BG;
        this.selectedValues = new Set();
        // Whether the last reading of the selection found its device away.
        this.offline = false;
        this.drawn = { items: '', selection: undefined };
        this.scrollArea = document.createElement('div');
        this.scrollArea.className = 'list-scroll';
        el.appendChild(this.scrollArea);
        this.staticItems = element.items || element.options || [];
        this.renderItems = (items) => this.draw(items);
        this.draw(this.staticItems);
    }

    /** The value the list's selection binding reads now, if it has one. */
    _selection() {
        const key = this.element.bindings?.show?.value?.key;
        return key ? this.host.state[key] : undefined;
    }

    /** Draw `items` as rows, unless they and the selection are what was drawn last. */
    draw(items) {
        const drawn = { selection: this._selection(), items: JSON.stringify(items) };
        if (drawn.items === this.drawn.items && drawn.selection === this.drawn.selection) return;
        this.drawn = drawn;
        this.scrollArea.innerHTML = '';
        this._takeReportedPick(items);
        for (const item of items) this.scrollArea.appendChild(this._row(item));
        const activeRow = this.scrollArea.querySelector('.list-item.active');
        if (activeRow) activeRow.scrollIntoView({ block: 'nearest' });
    }

    /** The selection the device reports, if it reports one, replaces the picks
     *  with the row it names. While the last reading found its device away
     *  nothing is picked: rows drawn again light no memory, as a reading
     *  does (showListSelection). */
    _takeReportedPick(items) {
        if (this.offline) {
            this.selectedValues.clear();
            return;
        }
        const reading = this._selection();
        if (nothingToShow(reading)) return;
        this.selectedValues.clear();
        const row = pickKey(items.map((item) => rowKey(item.value)), reading);
        if (row !== null) this.selectedValues.add(row);
    }

    _row(item) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.style.minHeight = this.itemHeight + 'rem';
        row.style.backgroundColor = this.itemBg;
        row.textContent = item.label || item.value || '';
        row.dataset.value = rowKey(item.value);
        if (this.selectedValues.has(rowKey(item.value))) {
            row.style.backgroundColor = this.itemActiveBg;
            row.classList.add('active');
        }
        if (this.listStyle !== 'static') row.addEventListener('click', () => this.tap(item));
        return row;
    }

    /** A tap on a row: the pick it makes, then `ui.select` with its value
     *  (the list's action slot is `select`), then the rows lit for the pick at
     *  once -- painted, not drawn, since a draw would put the device's reading
     *  back over the tap; its next reading takes over. While the device is
     *  unreachable the tap still sends and lights nothing. */
    tap(item) {
        this.host.send({ type: 'ui.select', element_id: this.element.id, value: item.value });
        if (this.offline) return;
        if (this.listStyle === 'selectable') this._pickOnly(rowKey(item.value));
        else if (this.listStyle === 'multi_select') this._togglePick(rowKey(item.value));
        paintPicks(this);
    }

    _pickOnly(value) {
        this.selectedValues.clear();
        this.selectedValues.add(value);
    }

    _togglePick(value) {
        if (this.selectedValues.has(value)) this.selectedValues.delete(value);
        else this.selectedValues.add(value);
    }
}

/**
 * The rows a key pattern finds in `state`: each matching key's value is a
 * row's label, and what the `*` stood for its value (the whole value when the
 * pattern has no `*`). `*` is the only wildcard; every other character is
 * literal, so a bracket or a plus neither throws nor matches as a regex.
 */
export function rowsFromState(state, keyPattern) {
    const escaped = keyPattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp('^' + escaped.replace(/\\\*/g, '(.+)') + '$');
    const items = [];
    for (const [key, val] of Object.entries(state)) {
        const match = key.match(regex);
        if (match) items.push({ label: String(val), value: match[1] || String(val) });
    }
    return items;
}

/**
 * A reading of the list's selection: the row it names lit, whatever the list's
 * style. Nothing is lit while the device is unreachable -- left alone that is
 * the quietest of the wrong answers, a lit row that is only a memory. A
 * reading of nothing leaves the rows as they are.
 */
export function showListSelection(list, value, offline) {
    list.offline = Boolean(offline);
    if (!offline && nothingToShow(value)) return;
    const { scrollArea, selectedValues } = list;
    const rows = [...scrollArea.querySelectorAll('.list-item')];
    selectedValues.clear();
    const picked = offline ? null : pickKey(rows.map((row) => row.dataset.value), value);
    if (picked !== null) selectedValues.add(picked);
    paintPicks(list);
}

/** Each drawn row lit or not as the list's picks say. */
function paintPicks(list) {
    const { scrollArea, itemBg, itemActiveBg, selectedValues } = list;
    for (const row of scrollArea.querySelectorAll('.list-item')) {
        const isActive = selectedValues.has(row.dataset.value);
        row.style.backgroundColor = isActive ? itemActiveBg : itemBg;
        row.classList.toggle('active', isActive);
    }
}
