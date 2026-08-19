import { describe, it, expect, vi } from "vitest";
import { createThrottledWriter } from "./stateWriter";

// What shipped: one POST per change event. A range input fires per pixel of
// travel, so one drag was hundreds of writes, each superseded by the next.

function harness(windowMs = 60) {
  const sent: Array<[string, unknown]> = [];
  let now = 0;
  const timers: Array<{ at: number; fn: () => void; id: number }> = [];
  let nextId = 1;

  const schedule = ((fn: () => void, ms: number) => {
    const id = nextId++;
    timers.push({ at: now + ms, fn, id });
    return id as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;

  const unschedule = ((id: unknown) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i >= 0) timers.splice(i, 1);
  }) as typeof clearTimeout;

  const w = createThrottledWriter((k, v) => sent.push([k, v]), windowMs, schedule, unschedule);

  function advance(ms: number) {
    now += ms;
    for (;;) {
      const due = timers.filter((t) => t.at <= now).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      timers.splice(timers.indexOf(due), 1);
      due.fn();
    }
  }

  return { w, sent, advance };
}

describe("throttled state writer", () => {
  it("sends the first change immediately so the control feels live", () => {
    const { w, sent } = harness();
    w.write("volume", 1);
    expect(sent).toEqual([["volume", 1]]);
  });

  it("collapses a burst to the first and the last value", () => {
    const { w, sent, advance } = harness(60);
    for (let i = 1; i <= 200; i++) w.write("volume", i);
    expect(sent).toEqual([["volume", 1]]);

    advance(60);
    // 200 changes became 2 requests, and the device ends up holding 200 —
    // the value the user actually let go of.
    expect(sent).toEqual([["volume", 1], ["volume", 200]]);
  });

  it("never loses the last value of a gesture", () => {
    const { w, sent, advance } = harness(60);
    w.write("volume", 10);
    w.write("volume", 20);
    w.write("volume", 30);
    advance(1000);
    expect(sent[sent.length - 1]).toEqual(["volume", 30]);
  });

  it("keeps keys independent so a mute is not delayed behind a fader", () => {
    const { w, sent } = harness(60);
    w.write("fader", 1);
    w.write("fader", 2);
    w.write("mute", true);
    // The fader's open window must not hold up an unrelated key.
    expect(sent).toEqual([["fader", 1], ["mute", true]]);
  });

  it("flush sends what is still queued, for unmount", () => {
    const { w, sent } = harness(60);
    w.write("volume", 1);
    w.write("volume", 2);
    w.flush();
    expect(sent).toEqual([["volume", 1], ["volume", 2]]);
  });

  it("cancel drops what is queued", () => {
    const { w, sent, advance } = harness(60);
    w.write("volume", 1);
    w.write("volume", 2);
    w.cancel();
    advance(1000);
    expect(sent).toEqual([["volume", 1]]);
  });

  it("stops scheduling once the gesture ends", () => {
    const { w, sent, advance } = harness(60);
    w.write("volume", 1);
    advance(60);   // window closes with nothing pending
    advance(600);  // and nothing keeps firing after
    expect(sent).toEqual([["volume", 1]]);
  });

  it("defaults to real timers when none are injected", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<[string, unknown]> = [];
      const w = createThrottledWriter((k, v) => sent.push([k, v]));
      w.write("k", 1);
      w.write("k", 2);
      vi.advanceTimersByTime(60);
      expect(sent).toEqual([["k", 1], ["k", 2]]);
    } finally {
      vi.useRealTimers();
    }
  });
});
