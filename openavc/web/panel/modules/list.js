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
 * Two rules light a row, and they differ: drawing the rows lights the picked
 * ones unless the list is static, while a new reading of the selection
 * (`showListSelection`) lights the row it names whatever the style. Both are
 * kept as they are.
 *
 * The page review reads this module's text for what a list reads off its
 * definition (tests/test_ui_page_review_mirrors.py credits it to the list's
 * renderer, the only one that hands it an element), so a definition is read
 * here only through a parameter named `element`.
 */

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
        this._takeReportedPick();
        for (const item of items) this.scrollArea.appendChild(this._row(item, items));
        const activeRow = this.scrollArea.querySelector('.list-item.active');
        if (activeRow) activeRow.scrollIntoView({ block: 'nearest' });
    }

    /** The selection the device reports, if it reports one, replaces the picks. */
    _takeReportedPick() {
        const picked = this._selection();
        if (picked === undefined || picked === null) return;
        this.selectedValues.clear();
        this.selectedValues.add(String(picked));
    }

    _row(item, items) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.style.minHeight = this.itemHeight + 'rem';
        row.style.backgroundColor = this.itemBg;
        row.textContent = item.label || item.value || '';
        row.dataset.value = item.value || '';
        if (this.listStyle === 'static') return row;
        if (this.selectedValues.has(String(item.value))) {
            row.style.backgroundColor = this.itemActiveBg;
            row.classList.add('active');
        }
        row.addEventListener('click', () => this.tap(item, items));
        return row;
    }

    /** A tap on a row: the pick it makes, then `ui.select` with its value
     *  (the list's action slot is `select`), then the rows again. */
    tap(item, items) {
        if (this.listStyle === 'selectable') this._pickOnly(String(item.value));
        else if (this.listStyle === 'multi_select') this._togglePick(String(item.value));
        this.host.send({ type: 'ui.select', element_id: this.element.id, value: item.value });
        this.draw(items);
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
    const reported = value !== undefined && value !== null;
    if (!offline && !reported) return;
    const { scrollArea, itemBg, itemActiveBg, selectedValues } = list;
    selectedValues.clear();
    if (!offline) selectedValues.add(String(value));
    scrollArea.querySelectorAll('.list-item').forEach((item) => {
        const isActive = selectedValues.has(item.dataset.value);
        item.style.backgroundColor = isActive ? itemActiveBg : itemBg;
        item.classList.toggle('active', isActive);
    });
}
