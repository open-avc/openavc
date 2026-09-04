// Vitest setup: register jest-dom matchers (toBeInTheDocument, …) and clean the
// rendered DOM between tests so each case starts from a blank document.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Node 22+ DECLARES `localStorage` on the global object and leaves it
// `undefined` unless the process was started with `--localstorage-file`. Vitest
// installs jsdom's globals onto that same object and does not overwrite one
// that is already declared, so jsdom's own storage never appears and a
// component reading `localStorage` gets `undefined` — not a ReferenceError, an
// undefined, which surfaces as "Cannot read properties of undefined (reading
// 'getItem')" from whichever component happens to read it first.
//
// CI runs Node 20, where no such global exists and jsdom's storage is used, so
// the suite is green there and red on a newer machine — and will go red on CI
// the day the runner image moves. Give the tests a real Storage rather than
// leaving it to the Node version. Per file, like jsdom's own, so nothing leaks
// between test files.
function installMemoryStorage(name: "localStorage" | "sessionStorage"): void {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => {
      store.delete(key);
    },
    setItem: (key, value) => {
      store.set(String(key), String(value));
    },
  };
  Object.defineProperty(globalThis, name, {
    value: storage,
    configurable: true,
    writable: true,
  });
}

if (typeof globalThis.localStorage === "undefined") installMemoryStorage("localStorage");
if (typeof globalThis.sessionStorage === "undefined") installMemoryStorage("sessionStorage");

afterEach(() => {
  cleanup();
});
