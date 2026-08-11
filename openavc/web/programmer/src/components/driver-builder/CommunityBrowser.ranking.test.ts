import { describe, it, expect } from "vitest";
import {
  expandDriverToCards,
  matchQuality,
  rankCards,
  scoreCard,
} from "./CommunityBrowser";
import type { CommunityDriver } from "../../api/types";

// Searching the catalog for a manufacturer by name used to be close to
// useless. The filter was a plain substring test and the order was alphabetical
// by brand, so a three-letter brand matched every card whose DESCRIPTION
// happened to contain those letters, and the brand you actually asked for sat
// in the middle of the pile. The fix ranks by where the match landed: a brand
// that starts with what you typed outranks a word that merely contains it,
// which outranks prose we wrote about some other product.
//
// Invented devices on purpose -- the point is the shape of the collision, not
// any real product. "orb" reproduces all four shapes at once:
//   Orb            brand is exactly the query
//   Orbit Labs     brand starts with the query
//   ORB-100        a model number starts with the query
//   Thorbeck       brand merely contains it, mid-word   (the "TurtleAV" shape)
//   "absorbs"      description contains it, mid-word    (the "releases" shape)

function driver(over: Partial<CommunityDriver> & { id: string }): CommunityDriver {
  return {
    name: "Widget",
    manufacturer: "Acme",
    author: "OpenAVC",
    category: "audio",
    transport: "tcp",
    version: "1.0.0",
    verified: false,
    description: "A widget.",
    file: `audio/${over.id}.avcdriver`,
    format: "avcdriver",
    tags: [],
    ...over,
  } as CommunityDriver;
}

const EXACT_BRAND = driver({
  id: "orb_amp",
  name: "Orb Amplifier",
  manufacturer: "Orb",
});
const BRAND_PREFIX = driver({
  id: "orbit_matrix",
  name: "Orbit Labs Matrix",
  manufacturer: "Orbit Labs",
});
const MODEL_PREFIX = driver({
  id: "zenith_dsp",
  name: "Zenith DSP",
  manufacturer: "Zenith",
  compatible_models: [
    { manufacturer: "Zenith", models: ["ORB-100", "ORB-200"], confidence: "full" },
  ],
});
const BRAND_MIDWORD = driver({
  id: "thorbeck_switch",
  name: "Thorbeck Switcher",
  manufacturer: "Thorbeck AV",
});
const DESCRIPTION_ONLY = driver({
  id: "acme_panel",
  name: "Acme Panel",
  manufacturer: "Acme",
  description: "A panel that absorbs the room's control traffic.",
});

const CORPUS = [
  // Deliberately in alphabetical order, which is the order the old code kept.
  DESCRIPTION_ONLY,
  EXACT_BRAND,
  BRAND_PREFIX,
  BRAND_MIDWORD,
  MODEL_PREFIX,
];

function search(drivers: CommunityDriver[], query: string, category = "All") {
  const cards = drivers.flatMap(expandDriverToCards);
  return rankCards(cards, query, category).map((c) => c.brand);
}

describe("matchQuality", () => {
  it("scores by where in the field the query landed", () => {
    expect(matchQuality("orb", "orb")).toBe(1);
    expect(matchQuality("Orbit Labs", "orb")).toBe(0.8);
    expect(matchQuality("Zenith ORB-100", "orb")).toBe(0.6);
    expect(matchQuality("Thorbeck", "orb")).toBe(0.25);
    expect(matchQuality("Kramer", "orb")).toBe(0);
  });

  it("takes the best occurrence, not the first", () => {
    // "absorbs the Orbit feed" -- mid-word first, word-start later. The later
    // one is the better signal and has to win, or scanning stops at the noise.
    expect(matchQuality("absorbs the Orbit feed", "orb")).toBe(0.6);
  });

  it("is unfazed by an absent field", () => {
    expect(matchQuality(undefined, "orb")).toBe(0);
    expect(matchQuality("", "orb")).toBe(0);
  });
});

describe("scoreCard", () => {
  it("puts a brand match above a description match", () => {
    const [brandCard] = expandDriverToCards(BRAND_PREFIX);
    const [proseCard] = expandDriverToCards(DESCRIPTION_ONLY);
    expect(scoreCard(brandCard, "orb")).toBeGreaterThan(scoreCard(proseCard, "orb"));
  });

  it("returns 0 for a card the query does not touch", () => {
    const [card] = expandDriverToCards(BRAND_PREFIX);
    expect(scoreCard(card, "kramer")).toBe(0);
  });
});

describe("Browse Community search ranking", () => {
  it("ranks the brand you typed first, prose about other gear last", () => {
    expect(search(CORPUS, "orb")).toEqual([
      "Orb",         // brand is the query
      "Orbit Labs",  // brand starts with it
      "Zenith",      // a model number starts with it
      "Thorbeck AV", // brand contains it mid-word
      "Acme",        // only the description contains it
    ]);
  });

  it("still finds everything the old substring filter found", () => {
    // Ranking reorders; it must not quietly drop a result. Losing the weak
    // matches would break "find the driver that mentions IR learning".
    expect(search(CORPUS, "orb")).toHaveLength(5);
  });

  it("leaves browse order alphabetical when nothing is typed", () => {
    expect(search(CORPUS, "")).toEqual([
      "Acme",
      "Orb",
      "Orbit Labs",
      "Thorbeck AV",
      "Zenith",
    ]);
    expect(search(CORPUS, "   ")).toEqual(search(CORPUS, ""));
  });

  it("keeps the dedicated driver above a generic one that matches as well", () => {
    // Both cards match the brand exactly, so the score ties and the existing
    // native-before-via rule decides -- the behaviour the multi-brand card
    // expansion was built for.
    const generic = driver({
      id: "generic_ptz",
      name: "Generic PTZ",
      manufacturer: "Generic",
      compatible_models: [
        { manufacturer: "Generic", models: ["Any"], confidence: "full" },
        { manufacturer: "Orb", models: ["Orb PTZ"], confidence: "untested" },
      ],
    });
    const ordered = rankCards(
      [EXACT_BRAND, generic].flatMap(expandDriverToCards),
      "orb",
      "All",
    );
    expect(ordered[0].brand).toBe("Orb");
    expect(ordered[0].isViaCard).toBe(false);
    expect(ordered[0].driver.id).toBe("orb_amp");
    expect(ordered[1].isViaCard).toBe(true);
  });

  it("applies the category filter independently of the search box", () => {
    const lighting = driver({
      id: "orbital_dmx",
      name: "Orbital DMX",
      manufacturer: "Orbital",
      category: "lighting",
    });
    expect(search([...CORPUS, lighting], "orb", "Lighting")).toEqual(["Orbital"]);
    expect(search([...CORPUS, lighting], "", "Lighting")).toEqual(["Orbital"]);
  });
});
