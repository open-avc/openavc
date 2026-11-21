"use strict";
/*
 * jsdom harness for the panel's sub-page container (openavc/web/panel/panel.js).
 *
 * jsdom + esbuild 组合，打包真实的 panel.js（其 modules/ 一并打入），在 jsdom
 * 文档里实例化面板应用，然后驱动子页面容器的三条行为：初始绘制、切换帧、
 * 导航拦截。断言的是行为而非源码文本。
 *
 * Invoked as: node subpage_harness.cjs <abs path to panel.js>
 * Prints JSON results on stdout for tests/test_subpage_container.py.
 */
const path = require("path");
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const panelPath = path.resolve(process.argv[2] || "");
if (!panelPath) {
  console.error("usage: node subpage_harness.cjs <panel.js>");
  process.exit(2);
}

// An async start() that never finishes must not crash the run: network stubs
// answer inertly and rejections are swallowed.
// 永不结束的异步 start() 不能拖垮测试：网络桩惰性应答，未处理的拒绝被吞掉。
process.on("unhandledRejection", () => {});

// --- jsdom, installed as globals before the bundle loads ---------------------
const dom = new JSDOM(
  `<!DOCTYPE html><html><body>
     <div id="panel-root"></div>
     <div id="connection-status"></div>
     <div id="loading-state" style="display:none"></div>
     <div id="offline-overlay" style="display:none"></div>
   </body></html>`,
  { url: "http://localhost:8080/panel", pretendToBeVisual: true },
);
const { window } = dom;
global.window = window;
global.document = window.document;
global.location = window.location;
// Node exposes its own `navigator` as a getter-only global; the panel reads it.
// Node 自带的 navigator 是只读全局；面板会读取它。
Object.defineProperty(global, "navigator", { value: window.navigator, configurable: true });
for (const name of [
  "HTMLElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "getComputedStyle", "localStorage", "sessionStorage",
]) {
  global[name] = window[name];
}
global.requestAnimationFrame = window.requestAnimationFrame = (cb) => { cb(0); return 0; };
global.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
// The contain fit sizes itself from a placed node; jsdom lays nothing out, so
// the observer records instead of firing.
// contain 适配依赖已落位的节点；jsdom 不做布局，观察器只登记不触发。
global.ResizeObserver = class {
  constructor(cb) { this.cb = cb; }
  observe() {}
  disconnect() {}
};
global.fetch = () => new Promise(() => {});
// The session bridge binds window.fetch at import time; jsdom ships none.
// 会话桥在导入时绑定 window.fetch；jsdom 不自带。
window.fetch = global.fetch;
global.WebSocket = class {
  constructor() { this.readyState = 0; }
  send() {}
  addEventListener() {}
  removeEventListener() {}
  close() {}
};
global.window.WebSocket = global.WebSocket;
global.window.fetch = global.fetch;

// --- bundle the real panel ---------------------------------------------------
const built = esbuild.buildSync({
  stdin: {
    contents: `import "${panelPath}";`,
    resolveDir: path.dirname(panelPath),
    loader: "js",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  loader: { ".css": "empty" },
  write: false,
});
// eslint-disable-next-line no-new-func
const run = new Function("require", "module", "exports", built.outputFiles[0].text);
const mod = { exports: {} };
run(require, mod, mod.exports);

const results = {};
const check = (name, fn) => {
  try {
    fn();
    results[name] = { pass: true, detail: "" };
  } catch (e) {
    results[name] = { pass: false, detail: `${e.message}` };
  }
};

const app = window.__openavcPanel;
if (!app) {
  results["app_booted"] = { pass: false, detail: "window.__openavcPanel missing" };
  console.log(JSON.stringify(results, null, 2));
  process.exit(0);
}
results["app_booted"] = { pass: true, detail: "" };

// One host page with the container, two sub-pages with one control each.
// 宿主页带一个容器，两个子页面各有一个控件。
const PLACEMENTS = {
  box: { x: 10, y: 10, w: 80, h: 80 },
  lbl_a: { x: 5, y: 5, w: 40, h: 10 },
  lbl_b: { x: 5, y: 20, w: 40, h: 10 },
};
app.uiDef = {
  pages: [
    {
      id: "main", name: "Main",
      elements: [{ id: "box", type: "subpage_container", default_page: "sub_a" }],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: PLACEMENTS, hidden: [] }],
    },
    {
      id: "sub_a", name: "Sub A", page_type: "subpage",
      elements: [{ id: "lbl_a", type: "label", text: "A" }],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: PLACEMENTS, hidden: [] }],
    },
    {
      id: "sub_b", name: "Sub B", page_type: "subpage",
      elements: [{ id: "lbl_b", type: "label", text: "B" }],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: PLACEMENTS, hidden: [] }],
    },
  ],
  settings: {},
};

check("initial_draw_shows_default", () => {
  const frame = app.renderSubpageContainer(app.uiDef.pages[0].elements[0]);
  const host = frame.querySelector(".subpage-host");
  if (!host) throw new Error("no .subpage-host in the container frame");
  if (!host.querySelector('[data-element-id="lbl_a"]')) throw new Error("default sub-page's label not drawn");
  if (host.querySelector('[data-element-id="lbl_b"]')) throw new Error("wrong sub-page drawn");
  const surface = app._surfaces.containers.get("box");
  if (!surface) throw new Error("container surface not registered");
  if (surface.pageId !== "sub_a") throw new Error(`surface shows ${surface.pageId}, expected sub_a`);
});

check("switch_frame_swaps_content", () => {
  app.showSubpage("box", "sub_b");
  const host = app._surfaces.containers.get("box").host;
  if (!host.querySelector('[data-element-id="lbl_b"]')) throw new Error("switched sub-page not drawn");
  if (host.querySelector('[data-element-id="lbl_a"]')) throw new Error("previous sub-page still shown");
  if (app._surfaces.containers.get("box").pageId !== "sub_b") throw new Error("surface pageId not updated");
  if (app._subpageShown.get("box") !== "sub_b") throw new Error("memory not updated");
});

check("switch_to_a_regular_page_is_refused", () => {
  const before = app._surfaces.containers.get("box").pageId;
  app.showSubpage("box", "main");
  if (app._surfaces.containers.get("box").pageId !== before) throw new Error("regular page accepted as a sub-page");
});

check("switch_to_unknown_id_is_ignored", () => {
  const before = app._surfaces.containers.get("box").pageId;
  app.showSubpage("box", "ghost");
  if (app._surfaces.containers.get("box").pageId !== before) throw new Error("unknown id accepted");
});

check("navigation_to_a_subpage_does_nothing", () => {
  app.currentPage = "main";
  app.navigateToPage("sub_a");
  if (app.currentPage !== "main") throw new Error("navigated to a sub-page");
});

check("host_surface_end_takes_the_container_down", () => {
  const surface = app._surfaces.containers.get("box");
  if (!surface) throw new Error("container gone before its host ended");
  surface.hostSurface.end();
  if (app._surfaces.containers.has("box")) throw new Error("container entry survived its host");
});

check("switch_outside_a_draw_lives_on_the_original_host", () => {
  // Re-open the container on a FRESH page surface (a page draw always makes
  // one; its memory still names sub_b, so it opens there), then switch to the
  // OTHER sub-page while NOT drawing: the new surface must still end with the
  // surface the container was drawn on (the page's), not with whatever
  // `drawing` answers outside a draw pass.
  // 在全新的页面 Surface 上重新打开容器（页面绘制总会新建 Surface；记忆仍指
  // 向 sub_b，故从它打开），再在非绘制期切换到另一个子页面：新 Surface 仍须
  // 随容器绘制所在的宿主（页面）结束，而不是随非绘制期 drawing 的应答。
  const pageSurface = new (app._surfaces.page.constructor)("main");
  app._surfaces.page = pageSurface;
  const frame = app.renderSubpageContainer(app.uiDef.pages[0].elements[0]);
  const surface = app._surfaces.containers.get("box");
  app.showSubpage("box", "sub_a");
  const next = app._surfaces.containers.get("box");
  if (!next || next === surface) throw new Error("switch did not replace the surface");
  // Ending the page surface must end BOTH the replaced surface (already ended)
  // and the live one, and clear the map.
  // 结束页面 Surface 必须同时清掉被替换的（已结束）与存活的条目。
  pageSurface.end();
  if (app._surfaces.containers.has("box")) throw new Error("entry survived its original host after a switch");
  if (frame.parentElement) throw new Error("frame unexpectedly attached");
});

console.log(JSON.stringify(results, null, 2));
