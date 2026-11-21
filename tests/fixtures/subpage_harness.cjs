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

check("container_without_default_receives_first_switch", () => {
  // A container with no default_page registers empty and is still a switch
  // target: showSubpage must find it and give it its first sub-page.
  // 没有默认页的容器也要注册（即使为空），并且仍是切换目标：showSubpage 必须
  // 找得到它并给它第一个子页面。
  const pageSurface = new (app._surfaces.page.constructor)("main");
  app._surfaces.page = pageSurface;
  const frame = app.renderSubpageContainer({ id: "bare_box", type: "subpage_container" });
  const entry = app._surfaces.containers.get("bare_box");
  if (!entry) throw new Error("empty container not registered");
  if (entry.host.querySelector('[data-element-id="lbl_a"]')) throw new Error("drew something with no target");
  app.showSubpage("bare_box", "sub_a");
  const after = app._surfaces.containers.get("bare_box");
  if (!after || after.pageId !== "sub_a") throw new Error("first switch did not land");
  if (!after.host.querySelector('[data-element-id="lbl_a"]')) throw new Error("first switch drew nothing");
  if (frame.parentElement) throw new Error("frame unexpectedly attached");
  pageSurface.end();
});

check("stale_memory_falls_back_to_default", () => {
  // Memory naming a deleted sub-page must not wedge the container on an empty
  // box forever: the stale entry is dropped and the default gets its turn.
  // 记忆指向已删除的子页面时不能让容器永远空着：清除失效记忆，让默认页顶上。
  const pageSurface = new (app._surfaces.page.constructor)("main");
  app._surfaces.page = pageSurface;
  app._subpageShown.set("box", "ghost_sub");
  app.renderSubpageContainer(app.uiDef.pages[0].elements[0]);
  const entry = app._surfaces.containers.get("box");
  if (!entry || entry.pageId !== "sub_a") throw new Error(`stale memory not recovered: ${entry && entry.pageId}`);
  if (!entry.host.querySelector('[data-element-id="lbl_a"]')) throw new Error("default not drawn after stale memory");
  pageSurface.end();
});

check("cycle_draws_empty_instead_of_overflowing", () => {
  // A sub-page whose own container names it (directly or through a partner)
  // must draw as an empty box, not recurse until the stack dies: the render
  // returns, the outer sub-page still draws, and the cycle logs a warning.
  // 子页面经由自己的容器指名自己（直接或经由伙伴容器）时必须画成空框，而
  // 不是递归到栈死：renderSubpageContainer 正常返回，外层子页面照常绘制。
  const pageSurface = new (app._surfaces.page.constructor)("main");
  app._surfaces.page = pageSurface;
  app.uiDef = {
    pages: [
      { id: "main", name: "Main",
        elements: [{ id: "loop_box", type: "subpage_container", default_page: "sub_loop" }],
        layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: PLACEMENTS, hidden: [] }] },
      { id: "sub_loop", name: "Loop", page_type: "subpage",
        elements: [{ id: "inner_box", type: "subpage_container", default_page: "sub_loop" }],
        layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: PLACEMENTS, hidden: [] }] },
    ],
    settings: {},
  };
  const frame = app.renderSubpageContainer(app.uiDef.pages[0].elements[0]);
  const host = frame.querySelector(".subpage-host");
  if (!host) throw new Error("no host in cycle container");
  // The outer container shows sub_loop's own label-less elements -- the INNER
  // container's render must have bailed out, not recursed.
  // 外层容器显示 sub_loop 的元素——内层容器的渲染必须就此打住，而不是递归。
  const inner = host.querySelector('[data-element-id="inner_box"]');
  if (!inner) throw new Error("sub_loop's own elements not drawn");
  if (inner.querySelector('[data-element-id="inner_box"]')) throw new Error("cycle recursed");
  if (frame.parentElement) throw new Error("frame unexpectedly attached");
  pageSurface.end();
});

check("fallback_skips_subpages_on_the_glass", () => {
  // A project holding ONLY sub-pages has no destination at all: the fallback
  // must not hand `pages[0]` to the glass, and edit mode keeps it for
  // authoring a panel that has no regular page yet.
  // 只有子页面的项目根本没有目的地：兜底不能把 pages[0] 递给玻璃；编辑模式
  // 保留它，供创作尚无普通页面的面板。
  const wasEditMode = app.editMode;
  app.editMode = false;
  app.uiDef = {
    pages: [
      { id: "sub_only", name: "Only", page_type: "subpage", elements: [],
        layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: {}, hidden: [] }] },
    ],
    settings: {},
  };
  app.currentPage = "";
  let resolved = app._pageToDraw();
  if (resolved !== null) throw new Error(`glass fallback drew ${resolved && resolved.id}`);
  app.editMode = true;
  resolved = app._pageToDraw();
  if (!resolved || resolved.id !== "sub_only") throw new Error("edit mode lost its sub-page preview");
  app.editMode = wasEditMode;
});

console.log(JSON.stringify(results, null, 2));
