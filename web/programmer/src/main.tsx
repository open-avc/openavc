import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import App from "./App";
import { installFetchAuth } from "./api/auth";
import "./styles/global.css";

// Bundle Monaco locally instead of fetching from cdn.jsdelivr.net at runtime.
// OpenAVC ships on AV LANs and Pi kiosks with no outbound internet, so the
// default @monaco-editor/react loader (which fetches loader.js from the CDN)
// fails with ERR_NAME_NOT_RESOLVED and the script editor never initializes.
//
// Two pieces are required:
//   1. loader.config({ monaco }) - short-circuits the CDN fetch in
//      @monaco-editor/loader's init() by pre-supplying the monaco instance.
//   2. self.MonacoEnvironment.getWorker - Monaco throws
//      "You must define a function MonacoEnvironment.getWorkerUrl or
//      MonacoEnvironment.getWorker" the moment any worker is needed
//      (editor.worker is used internally for diff/link/word-range computation
//      even with Python only). Vite's `?worker` suffix builds each worker as
//      a code-split chunk served from /assets/ alongside the main bundle.
self.MonacoEnvironment = {
  getWorker(_workerId, label) {
    switch (label) {
      case "json":
        return new jsonWorker();
      case "css":
      case "scss":
      case "less":
        return new cssWorker();
      case "html":
      case "handlebars":
      case "razor":
        return new htmlWorker();
      case "typescript":
      case "javascript":
        return new tsWorker();
      default:
        return new editorWorker();
    }
  },
};
loader.config({ monaco });

// Patch fetch to add Authorization headers from stored credentials before
// any module makes a request (App.tsx already calls fetch on mount).
installFetchAuth();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
