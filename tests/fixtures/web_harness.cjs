"use strict";
/*
 * jsdom harness for the panel's web element (openavc/web/panel/panel.js).
 *
 * jsdom + esbuild 组合，打包真实的 panel.js，驱动 web 元素的行为：无 url 的
 * 占位、沙箱属性、show.value 绑定换源、无桥（不监听、不收推送）、编辑模式
 * 实时渲染。断言的是行为而非源码文本。
 *
 * Invoked as: node web_harness.cjs <abs path to panel.js>
 * Prints JSON results on stdout for tests/test_web_frame.py.
 */
const path = require("path");
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const panelPath = path.resolve(process.argv[2] || "");
if (!panelPath) {
  console.error("usage: node web_harness.cjs <panel.js>");
  process.exit(2);
}

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
Object.defineProperty(global, "navigator", { value: window.navigator, configurable: true });
for (const name of [
  "HTMLElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "getComputedStyle", "localStorage", "sessionStorage",
]) {
  global[name] = window[name];
}
global.requestAnimationFrame = window.requestAnimationFrame = (cb) => { cb(0); return 0; };
global.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
global.ResizeObserver = class {
  constructor(cb) { this.cb = cb; }
  observe() {}
  disconnect() {}
};
global.fetch = () => new Promise(() => {});
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
  stdin: { contents: `import ${JSON.stringify(panelPath)};`, resolveDir: path.dirname(panelPath), loader: "js" },
  bundle: true,
  format: "cjs",
  platform: "node",
  loader: { ".css": "empty" },
  write: false,
});
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

// The draw tree registers every rendered element into the surface's element
// map; these scenarios render directly, so they register the same way.
// 绘制树会把每个渲染的元素登记进 Surface 的元素表；这些场景直接渲染，
// 故按同样方式登记。
const registerEl = (id, el, def) => {
  app._surfaces.drawing.elementMap[id] = { el, elementDef: def, id };
};

const PLACEMENTS = { box: { x: 10, y: 10, w: 80, h: 80 } };
app.uiDef = {
  pages: [
    { id: "main", name: "Main",
      elements: [{ id: "box", type: "web", url: "https://camera.local/ui" }],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: PLACEMENTS, hidden: [] }] },
  ],
  settings: {},
};

check("no_url_draws_placeholder", () => {
  const frame = app.renderWebElement({ id: "bare", type: "web" });
  if (frame.querySelector("iframe")) throw new Error("drew an iframe with no url");
  if (!frame.textContent.includes("Web page (no URL chosen)")) throw new Error("placeholder label missing");
});

check("frame_renders_with_sandbox", () => {
  const elementDef = app.uiDef.pages[0].elements[0];
  const frame = app.renderWebElement(elementDef);
  registerEl("box", frame, elementDef);
  const iframe = frame.querySelector("iframe");
  if (!iframe) throw new Error("no iframe");
  if (iframe.getAttribute("sandbox") !== "allow-scripts") throw new Error(`sandbox: ${iframe.getAttribute("sandbox")}`);
  if (iframe.getAttribute("allow") !== "autoplay; encrypted-media; fullscreen") throw new Error(`allow: ${iframe.getAttribute("allow")}`);
  if (!iframe.src.includes("camera.local")) throw new Error(`src: ${iframe.src}`);
  // The element map entry is what overrides and the review walk (元素表条目
  // 是 overrides 与评审行走的东西).
  if (!app._surfaces.everyControl().box) throw new Error("element map entry missing");
});

check("ticked_tokens_reach_the_attribute", () => {
  const frame = app.renderWebElement({
    id: "box", type: "web", url: "https://camera.local/ui",
    sandbox_permissions: ["allow-forms", "allow-popups"],
  });
  const iframe = frame.querySelector("iframe");
  if (iframe.getAttribute("sandbox") !== "allow-scripts allow-forms allow-popups") {
    throw new Error(`sandbox: ${iframe.getAttribute("sandbox")}`);
  }
});

check("empty_state_keeps_the_element_url", () => {
  const frame = app.renderWebElement({
    id: "box", type: "web", url: "https://camera.local/ui",
    bindings: { show: { value: { key: "var.stream" } } },
  });
  app.state = {};
  app.evaluateAllBindings();
  const iframe = frame.querySelector("iframe");
  if (!iframe.src.includes("camera.local")) throw new Error(`empty state changed src: ${iframe.src}`);
});

check("state_set_switches_the_source", () => {
  const frame = app.renderWebElement({
    id: "box", type: "web", url: "https://camera.local/ui",
    bindings: { show: { value: { key: "var.stream" } } },
  });
  app.state = { "var.stream": "https://other.example/stream3" };
  app.evaluateAllBindings();
  const iframe = frame.querySelector("iframe");
  if (!iframe.src.includes("other.example")) throw new Error(`state value not applied: ${iframe.src}`);
});

check("state_at_draw_time_lands", () => {
  // A redraw mid-session: the key already holds the answer, and the first
  // evaluation must land on it, not skip because it is the first.
  // 会话中途重绘：键已持有答案，首次求值必须落到它，而不是因首次而跳过。
  const elementDef3 = {
    id: "box", type: "web", url: "https://camera.local/ui",
    bindings: { show: { value: { key: "var.stream" } } },
  };
  app.state = { "var.stream": "https://other.example/stream3" };
  const frame = app.renderWebElement(elementDef3);
  registerEl("box", frame, elementDef3);
  app.evaluateAllBindings();
  const iframe = frame.querySelector("iframe");
  if (!iframe.src.includes("other.example")) throw new Error(`state at draw time skipped: ${iframe.src}`);
});

check("no_bridge_frame_listens_for_nothing", () => {
  const elementDefNb = app.uiDef.pages[0].elements[0];
  const frame = app.renderWebElement(elementDefNb);
  registerEl("box", frame, elementDefNb);
  const el = app._surfaces.everyControl().box.el;
  // The registered node is the stable wrapper: no message listener, and a
  // state filter that would serve the remote page nothing.
  // 登记节点是稳定包装器：无消息监听器，状态过滤对远程页面什么也不给。
  if (el._pluginMessageHandler) throw new Error("no-bridge frame registered a message listener");
  if (el._stateFilter && el._stateFilter("device.anything.power")) throw new Error("no-bridge frame would receive state");
});

check("edit_mode_stays_live", () => {
  const was = app.editMode;
  app.editMode = true;
  const frame = app.renderWebElement(app.uiDef.pages[0].elements[0]);
  app.editMode = was;
  if (!frame.querySelector("iframe")) throw new Error("edit mode placeholdered a configured web element");
});

check("non_http_override_and_binding_refused", () => {
  // One rule for all three sources: a scheme the sandbox refuses is never
    // applied -- the frame keeps showing what it showed (三个来源同一规则：
    // 沙箱不允许的 scheme 永不应用，帧保持原样).
  const frame = app.renderWebElement({
    id: "box5", type: "web", url: "https://camera.local/ui",
    bindings: { show: { value: { key: "var.w5" } } },
  });
  const iframe = frame.querySelector("iframe");
  app.state = { "var.w5": "data:text/html,x", "ui.box5.url": "javascript:alert(1)" };
  registerEl("box5", frame, app.uiDef.pages[0].elements[0]);
  app.evaluateAllBindings();
  app.evaluateUiOverrides();
  if (!iframe.src.includes("camera.local")) throw new Error(`non-http url applied: ${iframe.src}`);
});

check("ui_override_switches_the_source", () => {
  // Controllable out of the box: state.set ui.box.url moves the frame with no
  // binding authored and no variable declared (开箱即受控：无需绑定与变量).
  const frame = app.renderWebElement({ id: "box2", type: "web", url: "https://camera.local/ui" });
  app.state = { "ui.box2.url": "https://other.example/override" };
  registerEl("box2", frame, app.uiDef.pages[0].elements[0]);
  app.evaluateUiOverrides();
  const iframe = frame.querySelector("iframe");
  if (!iframe.src.includes("other.example")) throw new Error(`override not applied: ${iframe.src}`);
});

check("override_beats_binding_beats_static", () => {
  // Both a binding and an override on one element: the override is drawn
  // after the bindings, so while it is set it wins; the order is the same
  // one every other override keeps (覆盖 > 绑定 > 静态，与其他覆盖一致).
  const frame = app.renderWebElement({
    id: "box3", type: "web", url: "https://camera.local/ui",
    bindings: { show: { value: { key: "var.stream3" } } },
  });
  app.state = { "var.stream3": "https://bound.example/a", "ui.box3.url": "https://override.example/b" };
  registerEl("box3", frame, app.uiDef.pages[0].elements[0]);
  app.evaluateAllBindings();
  app.evaluateUiOverrides();
  const iframe = frame.querySelector("iframe");
  if (!iframe.src.includes("override.example")) throw new Error(`override did not win: ${iframe.src}`);
});

check("override_clear_puts_the_element_url_back", () => {
  const frame = app.renderWebElement({ id: "box4", type: "web", url: "https://camera.local/ui" });
  app.state = { "ui.box4.url": "https://other.example/override" };
  registerEl("box4", frame, app.uiDef.pages[0].elements[0]);
  app.evaluateUiOverrides();
  app.state = {};
  app.evaluateUiOverrides();
  const iframe = frame.querySelector("iframe");
  if (!iframe.src.includes("camera.local")) throw new Error(`cleared override did not restore: ${iframe.src}`);
});

check("override_on_non_web_is_ignored", () => {
  // The entry runs on every control; a button has no frame and must come to
  // no harm. Routed through renderElement, the way the draw tree would.
  // 条目会作用于每个控件；按钮没有帧，不得受影响。经 renderElement 分发，
  // 与绘制树一致。
  const frame = app.renderElement({ id: "btn1", type: "button", label: "B" });
  app.state = { "ui.btn1.url": "https://other.example/x" };
  app.evaluateUiOverrides();
  if (!frame.textContent.includes("B")) throw new Error("button broken by a url override");
  if (frame.querySelector("iframe")) throw new Error("button grew an iframe");
});

console.log(JSON.stringify(results, null, 2));
