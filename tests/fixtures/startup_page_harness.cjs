// Runs the server's startup page (openavc/middleware/startup_splash.py,
// _STARTUP_PAGE) in jsdom against scripted /api/startup-status answers, and
// prints what the screen showed after each one as JSON.
//
// Usage: node startup_page_harness.cjs <page.html> <scenario.json>
// The scenario is {"answers": [...], "press_after": <index or null>,
// "start_again": {"status": <int>, "body": {...}} or null}. Each answer is
// either a startup-status body or "unreachable" (the fetch fails, as it
// does while one process has ended and the next has not begun).
"use strict";

const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const html = fs.readFileSync(process.argv[2], "utf8");
const scenario = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

const timers = [];
const posts = [];
let reloads = 0;
let asked = 0;

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (err) => {
  // jsdom does not navigate: a reload is reported here instead.
  if (String(err.message).includes("navigation")) {
    reloads += 1;
  } else {
    process.stderr.write(String(err.stack || err) + "\n");
  }
});

function jsonReply(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://localhost:8080/panel",
  virtualConsole,
  beforeParse(window) {
    window.setTimeout = (fn) => {
      timers.push(fn);
      return timers.length;
    };
    window.fetch = (url, options) => {
      if (url === "/api/start-again") {
        posts.push({ url, method: options && options.method, headers: (options && options.headers) || {} });
        const reply = scenario.start_again || { status: 202, body: { status: "starting_again" } };
        return jsonReply(reply.status, reply.body);
      }
      const answer = scenario.answers[Math.min(asked, scenario.answers.length - 1)];
      asked += 1;
      if (answer === "unreachable") {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return jsonReply(200, answer);
    };
  },
});

const { document } = dom.window;

// An element the page does not have reads as not shown.
function visible(id) {
  const el = document.getElementById(id);
  return Boolean(el) && !el.hidden && el.style.display !== "none";
}

function text(id) {
  return visible(id) ? document.getElementById(id).textContent : null;
}

function screen() {
  return {
    message: document.getElementById("message").textContent,
    spinner: visible("spinner"),
    error: text("error"),
    next: text("next"),
    start_again: visible("again"),
    reloads,
  };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function main() {
  const screens = [];
  for (let i = 0; i < scenario.answers.length; i += 1) {
    const due = timers.shift();
    if (!due) {
      screens.push({ stopped_asking: true });
      break;
    }
    due();
    await settle();
    screens.push(screen());
    if (scenario.press_after === i) {
      document.getElementById("again").click();
      await settle();
      screens.push(Object.assign({ pressed: true }, screen()));
    }
  }
  process.stdout.write(JSON.stringify({ screens, posts, still_asking: timers.length > 0 }));
}

main().catch((err) => {
  process.stderr.write(String(err.stack || err) + "\n");
  process.exit(1);
});
