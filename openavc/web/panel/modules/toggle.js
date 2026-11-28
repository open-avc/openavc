/**
 * A toggle button's own indication: whether it is lit, whether it names
 * words for on and off, and which words it shows with the field an in-place
 * edit of them writes.
 *
 * Only a button whose press is a toggle, and which has no look of its own,
 * draws this (evaluateToggleLook); the lit look itself is modules/lit.js and
 * the button's face, its picture and its paint are modules/button.js.
 * `toggleLit` is THE verdict whether a toggle is on: its press
 * (modules/press.js `frameAtPress`) asks it too, with the same offline
 * judgment, so a press always goes by what the light shows, and
 * core/ui_events.py `resolve_press` is the server's copy.
 *
 * The page review's renderer scan credits all of this text to the button,
 * the one control that hands it a definition.
 */

import { sameValue } from './compare.js';

/** Where a toggle's two words are authored when its press says nothing else. */
const PRESS_WORDS = ['bindings', 'do', 'press', 0];

/**
 * Whether a toggle is lit, and so whether its press turns it off: its device
 * answers and the reading equals the value that means on (compare.js
 * `sameValue`, never while it has not arrived).
 */
export function toggleLit(binding, stateValue, offline) {
    return !offline && sameValue(stateValue, binding.value);
}

/** Whether a toggle names words of its own for on or off. */
export function toggleNamesWords(binding) {
    return binding.on_label != null || binding.off_label != null;
}

/**
 * A toggle's words and the field an edit of them writes: its on or off
 * word; an empty one, or none while its device is away, falls back to the
 * button's own label, so an edit of what is showing belongs to whichever one
 * actually drew.
 */
export function toggleWords(elementDef, binding, on, offline) {
    const word = offline ? null : (on ? binding.on_label : binding.off_label);
    if (word == null || word === '') return [elementDef.label || '', ['label']];
    const labelBase = binding._labelBase || PRESS_WORDS;
    return [word, [...labelBase, on ? 'on_label' : 'off_label']];
}
