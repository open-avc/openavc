import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import ts from "typescript";

/**
 * Every REST transport throws an `ApiError` whose `.message` is the historical
 * "API <status>: <body>" envelope, so stringifying the thrown value puts
 * `ApiError: API 400: {"detail":"..."}` in front of a person whose job is
 * aiming a projector. `parseApiError` unwraps it to the sentence the server
 * actually wrote, and for anything that is not an `ApiError` it returns the
 * same text the caller would have produced by hand (`.message` of an `Error`,
 * `String(v)` of anything else), so it is the one right answer everywhere.
 *
 * This guard used to be a regex over the places a message ends up: first the
 * toasts, then `setError` and its `setSomethingError` siblings. Each version
 * reported green over a form it could not see — a `${String(e)}` in a toast,
 * then `"Failed: " + String(e)`, a `String(e)` inside an object literal, an
 * `e instanceof Error ? e.message : ...` ternary, a setter called
 * `setCommandResult`, a zustand `set({ error })`, and a call whose `error:`
 * sits three lines below its `setPreview({`. Listing sinks cannot end that,
 * because there is always one more sink.
 *
 * So it asks the question from the other end: inside a `catch (x)` or a
 * `.catch((x) => ...)`, is the caught value being turned into text by hand?
 * `String(x)`, `${x}`, `x.message` and `"..." + x` are the four ways to do it,
 * and none of them depends on where the text goes next.
 *
 * Two places are left out on purpose. `api/` is the layer that builds these
 * errors and classifies them, so it reads raw messages by design. And a
 * `console.*` call is for whoever is debugging, not for the user.
 *
 * `api/` gets its own check instead, because `parseApiError` can only unwrap
 * what it recognises: an `ApiError`, or a message that opens with the
 * `API <status>:` envelope. Four hand-rolled fetches threw
 * `new Error(\`Upload failed: ${body}\`)` and the like, and every caller that
 * did the right thing still showed `{"detail":"..."}`, because there was
 * nothing left to unwrap. So a transport never builds an error out of the
 * response body it read, except inside an envelope its unwrapper parses.
 */

const SRC = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      out.push(...sourceFiles(path));
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(path);
    }
  }
  return out;
}

const isFunctionLike = (node: ts.Node): node is ts.SignatureDeclaration =>
  ts.isArrowFunction(node) ||
  ts.isFunctionExpression(node) ||
  ts.isFunctionDeclaration(node) ||
  ts.isMethodDeclaration(node);

function paramNames(fn: ts.SignatureDeclaration): string[] {
  return fn.parameters.flatMap((p) => (ts.isIdentifier(p.name) ? [p.name.text] : []));
}

/** Strip `(x)`, `x as T`, `x!` down to the expression underneath. */
function unwrap(node: ts.Expression): ts.Expression {
  let n = node;
  while (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n)) {
    n = n.expression;
  }
  return n;
}

function isConsoleCall(node: ts.Node): boolean {
  if (!ts.isCallExpression(node)) return false;
  const callee = node.expression;
  return (
    ts.isPropertyAccessExpression(callee) &&
    ts.isIdentifier(callee.expression) &&
    callee.expression.text === "console"
  );
}

/**
 * Line numbers (1-based) where a caught value is stringified by hand. The
 * walk carries the set of names bound by an enclosing catch, so an inner
 * function that reuses the name (`.map((e) => ...)`) shadows it.
 */
function rawCaughtRenderings(text: string, fileName: string): number[] {
  const sf = ts.createSourceFile(
    fileName,
    text,
    ts.ScriptTarget.Latest,
    true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const lines = new Set<number>();
  const flag = (node: ts.Node) =>
    lines.add(sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1);

  const visit = (node: ts.Node, caught: ReadonlySet<string>): void => {
    if (isConsoleCall(node)) return;

    const isCaught = (expr: ts.Expression | undefined): boolean => {
      if (!expr) return false;
      const inner = unwrap(expr);
      return ts.isIdentifier(inner) && caught.has(inner.text);
    };

    if (caught.size > 0) {
      // String(x)
      if (
        ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === "String" &&
        isCaught(node.arguments[0])
      ) {
        flag(node);
      }
      // `${x}`
      if (ts.isTemplateSpan(node) && isCaught(node.expression)) flag(node);
      // x.message, (x as Error).message -- unless something is read off it
      // in turn (x.message.includes(...) asks a question, it renders nothing)
      if (
        ts.isPropertyAccessExpression(node) &&
        node.name.text === "message" &&
        isCaught(node.expression) &&
        !ts.isPropertyAccessExpression(node.parent)
      ) {
        flag(node);
      }
      // "..." + x
      if (
        ts.isBinaryExpression(node) &&
        node.operatorToken.kind === ts.SyntaxKind.PlusToken &&
        (isCaught(node.left) || isCaught(node.right))
      ) {
        flag(node);
      }
    }

    // A catch clause binds its variable for the block.
    if (ts.isCatchClause(node)) {
      const decl = node.variableDeclaration;
      const next = new Set(caught);
      if (decl && ts.isIdentifier(decl.name)) next.add(decl.name.text);
      visit(node.block, next);
      return;
    }

    // promise.catch((x) => ...) binds the handler's first parameter.
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "catch"
    ) {
      visit(node.expression, caught);
      for (const arg of node.arguments) {
        if (isFunctionLike(arg) && arg.parameters.length > 0) {
          const next = new Set(caught);
          for (const name of paramNames(arg)) next.delete(name);
          const first = arg.parameters[0].name;
          if (ts.isIdentifier(first)) next.add(first.text);
          if (arg.body) visit(arg.body, next);
        } else {
          visit(arg, caught);
        }
      }
      return;
    }

    // Any other function that reuses a caught name shadows it.
    if (isFunctionLike(node) && caught.size > 0) {
      const shadowed = paramNames(node).filter((n) => caught.has(n));
      if (shadowed.length > 0) {
        const next = new Set(caught);
        for (const n of shadowed) next.delete(n);
        ts.forEachChild(node, (child) => visit(child, next));
        return;
      }
    }

    ts.forEachChild(node, (child) => visit(child, caught));
  };

  visit(sf, new Set());
  return [...lines].sort((a, b) => a - b);
}

const offendingLines = (snippet: string) => rawCaughtRenderings(snippet, "snippet.tsx");

// The envelopes an unwrapper parses: `parseApiError` reads `API <status>:`,
// `friendlyAIError` reads `AI API <status>:`.
const PARSED_ENVELOPE = /^(?:AI )?API $/;

/**
 * Line numbers where a transport throws an error built from a response body
 * it read with `.text()`: the call itself, or a variable it was stored in.
 * Reading a field off a parsed body (`body?.detail`) is the right thing and
 * is not counted; nor is `ApiError`, which is the right answer.
 */
function rawBodyThrows(text: string, fileName: string): number[] {
  const sf = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const lines = new Set<number>();

  const readsText = (node: ts.Node): boolean => {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "text" &&
      node.arguments.length === 0
    ) {
      return true;
    }
    return ts.forEachChild(node, readsText) ?? false;
  };

  const bodies = new Set<string>();
  const collect = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && readsText(node.initializer)) {
      bodies.add(node.name.text);
    }
    ts.forEachChild(node, collect);
  };
  collect(sf);

  const carriesBody = (node: ts.Node): boolean => {
    if (readsText(node)) return true;
    if (ts.isIdentifier(node) && bodies.has(node.text)) {
      return !(ts.isPropertyAccessExpression(node.parent) && node.parent.expression === node);
    }
    return ts.forEachChild(node, carriesBody) ?? false;
  };

  const visit = (node: ts.Node): void => {
    if (
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text.endsWith("Error") &&
      node.expression.text !== "ApiError"
    ) {
      const first = node.arguments?.[0];
      const enveloped =
        first !== undefined && ts.isTemplateExpression(first) && PARSED_ENVELOPE.test(first.head.text);
      if (!enveloped && (node.arguments ?? []).some(carriesBody)) {
        lines.add(sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return [...lines].sort((a, b) => a - b);
}

describe("error rendering", () => {
  it("never turns a caught error into text by hand", () => {
    const apiDir = join(SRC, "api") + sep;
    const offenders: string[] = [];
    for (const path of sourceFiles(SRC)) {
      if (path.startsWith(apiDir)) continue;
      const text = readFileSync(path, "utf8");
      const source = text.split("\n");
      for (const line of rawCaughtRenderings(text, path)) {
        offenders.push(`${path.slice(SRC.length + 1)}:${line}  ${source[line - 1].trim()}`);
      }
    }

    expect(
      offenders,
      "these turn a caught error into text by hand, which renders the raw ApiError "
        + "envelope; use parseApiError(e) from api/errors so the user sees the server's sentence",
    ).toEqual([]);
  });

  it("actually looks at the tree it is guarding", () => {
    // A guard that silently scans nothing passes forever. Pin that the walk
    // reaches real files.
    const files = sourceFiles(SRC);
    expect(files.length).toBeGreaterThan(100);
    expect(files.some((f) => f.endsWith("ActionListEditor.tsx"))).toBe(true);
  });

  it("sees every form a caught error has been rendered in", () => {
    // Each of these sat in the tree under an earlier version of this guard.
    const raw = [
      "try { run(); } catch (e) { showError(`Test failed: ${e}`); }",
      "try { run(); } catch (e) { showError(`Install failed: ${String(e)}`); }",
      "try { run(); } catch (e) { setError(String(e)); }",
      "try { run(); } catch (e) { showError(\"Update check failed: \" + String(e)); }",
      "try { run(); } catch (err) { showError(\"Failed: \" + err); }",
      "try { run(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }",
      "try { run(); } catch (e) { setInstallErrors((prev) => ({ ...prev, [id]: String(e) })); }",
      "try { run(); } catch (e) { set({ error: String(e), loading: false }); }",
      "try { run(); } catch (e) { setCommandResult(String(e)); }",
      "try { run(); } catch (e) { setStatusMsg({ kind: \"error\", text: (e as Error).message }); }",
      "try { run(); } catch (ex) { setRawResult(String(ex)); }",
      "p.catch((e) => setFetchError(`Unable to reach server: ${e.message || e}`));",
      "p.catch((reason: unknown) => alive && setLoadErr(String(reason)));",
    ];
    for (const snippet of raw) {
      expect(offendingLines(snippet), snippet).toEqual([1]);
    }

    // The case a line-anchored pattern could never see: the sink and the
    // stringified value on different lines.
    expect(
      offendingLines(
        [
          "p.catch((e: unknown) => {",
          "  setPreview({",
          "    result: null,",
          "    error: e instanceof Error ? e.message : String(e),",
          "  });",
          "});",
        ].join("\n"),
      ),
    ).toEqual([4]);
  });

  it("leaves alone what is not a caught error turned into text", () => {
    const fine = [
      "try { run(); } catch (e) { setError(parseApiError(e)); }",
      "try { run(); } catch (e) { showError(`Test failed: ${parseApiError(e)}`); }",
      "try { run(); } catch (e) { setError(null); }",
      "try { run(); } catch (e) { showError(`Saved ${errorCount} of them`); }",
      "try { run(); } catch (e) { console.error(\"load failed:\", e, String(e)); }",
      "try { run(); } catch (e) { if (e instanceof ApiError && e.status === 403) setLocked(true); }",
      "try { run(); } catch (e) { setRefreshError(e); }",
      "try { run(); } catch (e) { if (e.message.includes(\"[Plugin:\")) skip(); }",
      "try { run(); } catch (e) { setNames(list.map((e) => String(e))); }",
      "try { run(); } catch { setError(\"Could not load.\"); }",
      "entries.map((e) => `${e.level}: ${e.message}`);",
      "const label = String(e) + suffix;",
    ];
    for (const snippet of fine) {
      expect(offendingLines(snippet), snippet).toEqual([]);
    }
  });

  it("never throws a response body a caller cannot unwrap", () => {
    const apiDir = join(SRC, "api");
    const offenders: string[] = [];
    for (const path of sourceFiles(apiDir)) {
      const text = readFileSync(path, "utf8");
      const source = text.split("\n");
      for (const line of rawBodyThrows(text, path)) {
        offenders.push(`${path.slice(SRC.length + 1)}:${line}  ${source[line - 1].trim()}`);
      }
    }
    expect(sourceFiles(apiDir).some((f) => f.endsWith("systemClient.ts"))).toBe(true);
    expect(
      offenders,
      "these throw the raw response body in an Error parseApiError cannot unwrap; "
        + "throw new ApiError(res.status, body) so callers get the server's sentence",
    ).toEqual([]);
  });

  it("sees a transport throwing a raw body, and nothing else", () => {
    const raw = [
      "const body = await res.text();\nthrow new Error(`Upload failed: ${body}`);",
      "throw new Error(await res.text());",
      "const raw = await res.text();\nthrow new Error(\"Import failed: \" + raw);",
      "const body = await res.text();\nthrow new ConflictError(body);",
    ];
    for (const snippet of raw) {
      expect(rawBodyThrows(snippet, "snippet.ts").length, snippet).toBe(1);
    }
    const fine = [
      "throw new ApiError(res.status, await res.text());",
      "const body = await res.json();\nthrow new Error(body?.detail ?? \"Import failed\");",
      "const body = await res.text();\nthrow new Error(`AI API ${res.status}: ${body}`);",
      "const body = await res.text();\nthrow new Error(`API ${res.status}: ${body}`);",
      "throw new Error(`Export failed: ${res.status}`);",
      "throw new Error(await _parseUploadError(res));",
    ];
    for (const snippet of fine) {
      expect(rawBodyThrows(snippet, "snippet.ts"), snippet).toEqual([]);
    }
  });
});
