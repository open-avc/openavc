import { describe, it, expect } from "vitest";

import { readingText } from "./booleanWords";

// A reading a device reports as a float carries every digit the conversion
// left (-52.78131103515625 dB). A readout rounds it to the precision its
// driver declares (the step), or to two places when it declares none.
describe("readingText", () => {
  it("rounds to the declared step's places", () => {
    expect(readingText(-52.78131103515625, { step: 0.5 })).toBe("-52.8");
    expect(readingText(-52.78131103515625, { step: 1 })).toBe("-53");
    expect(readingText(12.3456, { step: 0.01 })).toBe("12.35");
  });

  it("rounds to two places when no step is declared", () => {
    expect(readingText(-52.78131103515625, undefined)).toBe("-52.78");
    expect(readingText(1.5, {})).toBe("1.5");
  });

  it("keeps a small reading readable", () => {
    expect(readingText(0.0012345, undefined)).toBe("0.00123");
  });

  it("leaves whole numbers, text and booleans as a readout shows them", () => {
    expect(readingText(85, { step: 0.5 })).toBe("85");
    expect(readingText("on", undefined)).toBe("on");
    expect(readingText(true, undefined)).toBe("Yes");
    expect(readingText(null, undefined)).toBe("");
  });
});
