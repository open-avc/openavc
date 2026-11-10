/**
 * What each control type binds, as the binding card offers it.
 *
 * Every control's bindings are grouped into two buckets the integrator reasons
 * about directly: SHOWS (what the control reflects from live state) and DOES
 * (what happens when it's touched). This descriptor drives BindingProperties:
 * which Value / Items / Appearance cards a control gets under SHOWS, and which
 * interaction action-lists it gets under DOES. The "Visible when…" card is
 * universal (every element can be conditionally shown) and is therefore not
 * listed here — BindingProperties always renders it.
 *
 * Validate reads the same table: a control whose Value can drive the device
 * (`value.link`) and whose interactions send no command is reported there.
 * Which show slots the panel itself honours per type is a separate,
 * generated table (`HONORED_SHOW_SLOTS` in api/uiBindingReach.gen.ts).
 */

export type ValueEditorKind = "slider" | "text";
export type LookEditorKind = "feedback" | "color" | "select_feedback";
export type InteractionEditorKind = "actions" | "select_change";

export interface ValueCapability {
  /** Which editor draws the Value source. `slider` = a state-key picker (the
   *  control's numeric/selection value); `text` = the label text editor. */
  editor: ValueEditorKind;
  /** Heading for the Value card (e.g. "Selected item" for lists). */
  label?: string;
  /** The control can drive its value back out — shows the device-aware LINK
   *  (two-way) switch. False for read-only displays (gauge, level meter). */
  link?: boolean;
}

export interface DoesCapability {
  /** The `do.<interaction>` key this action list is stored under. */
  interaction: string;
  /** Heading shown above the action list. */
  label: string;
  editor: InteractionEditorKind;
}

export interface BindingCapability {
  /** SHOWS → Value card. */
  value?: ValueCapability;
  /** SHOWS → Items card (list row population). */
  items?: boolean;
  /** SHOWS → Appearance card (state-driven look). */
  look?: LookEditorKind;
  /**
   * The element draws no words, so its Appearance card offers a background
   * colour and no text colour, and a new state starts empty rather than with a
   * colour seeded behind what the element shows.
   */
  lookWithoutText?: boolean;
  /** DOES → one action list per interaction. */
  does?: DoesCapability[];
  /** Buttons drive DOES through the unified ButtonBindingEditor (behavior mode +
   *  press/hold/release) rather than a plain action list. */
  buttonStyle?: boolean;
}

export const BINDING_CAPABILITIES: Record<string, BindingCapability> = {
  button: { look: "feedback", buttonStyle: true },
  camera_preset: {
    look: "feedback",
    does: [{ interaction: "press", label: "On press", editor: "actions" }],
  },
  // A label takes the same state-driven appearance a button does: the words and
  // the colour both track the state, which is how you draw ONLINE in green and
  // OFFLINE in red. Which per-state fields the card offers (words, icon,
  // picture) comes from the panel's own tables, in BindingProperties.
  label: { value: { editor: "text", label: "Text" }, look: "feedback" },
  // An image changes its picture per state: a script sets a variable to
  // `christmas` and the background becomes the holiday one.
  image: { look: "feedback", lookWithoutText: true },
  // A Page Nav takes a button's Appearance: colours, words and an icon per
  // state. Its lit tab is a property of its own (highlight_current), not a
  // binding: it follows the page on screen, not state.
  page_nav: { look: "feedback" },
  // A Container's frame takes a colour or a background picture per state.
  group: { look: "feedback", lookWithoutText: true },
  slider: {
    value: { editor: "slider", link: true },
    does: [{ interaction: "change", label: "On change", editor: "actions" }],
  },
  fader: {
    value: { editor: "slider", link: true },
    does: [{ interaction: "change", label: "On change", editor: "actions" }],
  },
  select: {
    value: { editor: "slider", link: true },
    look: "select_feedback",
    does: [{ interaction: "change", label: "On change", editor: "select_change" }],
  },
  text_input: {
    value: { editor: "slider", link: true },
    does: [{ interaction: "change", label: "On change", editor: "actions" }],
  },
  status_led: { look: "color" },
  gauge: { value: { editor: "slider", link: false } },
  level_meter: { value: { editor: "slider", link: false } },
  keypad: {
    does: [{ interaction: "submit", label: "On submit", editor: "actions" }],
  },
  list: {
    value: { editor: "slider", label: "Selected item", link: true },
    items: true,
    does: [{ interaction: "select", label: "On row tap", editor: "actions" }],
  },
  matrix: {
    does: [
      { interaction: "route", label: "Video route", editor: "actions" },
      { interaction: "audio_route", label: "Audio route", editor: "actions" },
      { interaction: "mute_route", label: "Mute", editor: "actions" },
      { interaction: "audio_mute_route", label: "Audio mute", editor: "actions" },
    ],
  },
  // clock / plugin / custom: "Visible when…" only.
  // A custom control talks to the room through its own message bridge, not
  // through bindings, so there is nothing here to give it.
};
