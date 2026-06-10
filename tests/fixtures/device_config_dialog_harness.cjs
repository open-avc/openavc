"use strict";
// Loads the real Add/Edit Device dialog config logic
// (views/devices/deviceConfigCoerce.ts, bundled on the fly with the esbuild
// in web/programmer/node_modules) and checks:
//   - configFieldKind routes `secret: true` fields to the masked password
//     widget instead of the plaintext fallback,
//   - coerceConfigValue preserves declared string/password/secret values
//     exactly (no number/JSON sniffing that corrupted "0123" -> 123 or
//     19-digit codes past 2^53), with untyped fields only number-coerced
//     when the round-trip is lossless,
//   - splitConnectionFields sends host/port/etc. to the connections table
//     the way the device-update API does, so the Add dialog persists the
//     v0.5.0 layout.
// Prints JSON results; the Python wrapper skips when the Node toolchain or
// esbuild is absent.
const path = require("path");

const modPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [modPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, modPath, path.dirname(modPath));

const kind = moduleObj.exports.configFieldKind;
const coerce = moduleObj.exports.coerceConfigValue;
const split = moduleObj.exports.splitConnectionFields;

const results = {};
const missing = (name) => ({ pass: false, detail: `${name} is not exported` });
const ok = (r, value) => r && r.ok === true && r.value === value;

{
  // The headline defect: a Driver Builder `secret: true` field (declared
  // type string, like generic_http's passwords/API keys) fell through to
  // the plaintext input. It must route to the masked widget — and secret
  // must win over enum/number widgets, which would display the credential.
  results.h101_secret_fields_render_masked = !kind
    ? missing("configFieldKind")
    : {
        pass:
          kind({ type: "string", secret: true }) === "password" &&
          kind({ secret: true }) === "password" &&
          kind({ type: "password" }) === "password" &&
          kind({ type: "integer", secret: true }) === "password" &&
          kind({ values: ["a", "b"], secret: true }) === "password",
        detail: {
          stringSecret: kind({ type: "string", secret: true }),
          untypedSecret: kind({ secret: true }),
          intSecret: kind({ type: "integer", secret: true }),
        },
      };
}
{
  // Non-secret fields keep their existing widgets.
  results.h101_normal_fields_unchanged = !kind
    ? missing("configFieldKind")
    : {
        pass:
          kind({ type: "boolean" }) === "boolean" &&
          kind({ values: ["a", "b"] }) === "select" &&
          kind({ type: "integer" }) === "number" &&
          kind({ type: "number" }) === "number" &&
          kind({ type: "float" }) === "number" &&
          kind({ type: "text" }) === "textarea" &&
          kind({ type: "object" }) === "textarea" &&
          kind({ type: "json" }) === "textarea" &&
          kind({ type: "string" }) === "plain" &&
          kind(undefined) === "plain",
        detail: {
          string: kind({ type: "string" }),
          float: kind({ type: "float" }),
        },
      };
}
{
  // Declared string/password (and secret-flagged) values persist exactly as
  // typed: "0123" stayed a corrupted number 123 before, JSON-looking strings
  // became objects, and 19-digit codes lost precision past 2^53.
  results.h102_declared_string_password_never_sniffed = !coerce
    ? missing("coerceConfigValue")
    : {
        pass:
          ok(coerce("0123", "string"), "0123") &&
          ok(coerce('{"a":1}', "string"), '{"a":1}') &&
          ok(coerce("123456789012345678", "password"), "123456789012345678") &&
          ok(coerce("0123", "", true), "0123"),
        detail: {
          leadingZeros: coerce("0123", "string"),
          jsonString: coerce('{"a":1}', "string"),
          bigCode: coerce("123456789012345678", "password"),
          untypedSecret: coerce("0123", "", true),
        },
      };
}
{
  // Untyped (schema-less) fields still self-heal stringified numbers, but
  // only when the round-trip is lossless — "0123" and oversized codes stay
  // strings instead of silently corrupting.
  results.h102_untyped_lossless_number_only = !coerce
    ? missing("coerceConfigValue")
    : {
        pass:
          ok(coerce("8080", ""), 8080) &&
          ok(coerce("1.5", ""), 1.5) &&
          ok(coerce("0123", ""), "0123") &&
          ok(coerce("123456789012345678", ""), "123456789012345678"),
        detail: {
          port: coerce("8080", ""),
          leadingZeros: coerce("0123", ""),
          bigCode: coerce("123456789012345678", ""),
        },
      };
}
{
  // The coercions that already worked keep working: booleans are real
  // booleans, declared numerics coerce, object fields validate JSON,
  // untyped JSON objects still parse (schema-less edit back-compat).
  const objBad = coerce ? coerce("not json", "object") : null;
  const objGood = coerce ? coerce('{"a": 1}', "object") : null;
  const untypedObj = coerce ? coerce('{"b": 2}', "") : null;
  results.h102_existing_coercions_unchanged = !coerce
    ? missing("coerceConfigValue")
    : {
        pass:
          ok(coerce("true", "boolean"), true) &&
          ok(coerce("false", "boolean"), false) &&
          ok(coerce("5", "integer"), 5) &&
          ok(coerce("3.5", "number"), 3.5) &&
          ok(coerce("line1\nline2", "text"), "line1\nline2") &&
          objBad.ok === false &&
          objGood.ok === true &&
          typeof objGood.value === "object" &&
          objGood.value.a === 1 &&
          untypedObj.ok === true &&
          typeof untypedObj.value === "object" &&
          untypedObj.value.b === 2,
        detail: { objBad, objGood, untypedObj },
      };
}
{
  // The Add dialog persists via the whole-project save, so it must apply
  // the same connection-field split the device-update API does — host,
  // port, credentials and ssl go to project.connections, protocol fields
  // stay in device.config.
  if (!split) {
    results.m157_connection_fields_split = missing("splitConnectionFields");
  } else {
    const r = split({
      host: "192.168.1.50",
      port: 23,
      baudrate: 9600,
      username: "admin",
      password: "secret",
      base_url: "http://x",
      ssl: true,
      poll_interval: 5,
      commands: { power_on: "PWR ON" },
    });
    const connKeys = Object.keys(r.connection).sort();
    const cfgKeys = Object.keys(r.config).sort();
    const none = split({ poll_interval: 5 });
    results.m157_connection_fields_split = {
      pass:
        connKeys.join(",") === "base_url,baudrate,host,password,port,ssl,username" &&
        cfgKeys.join(",") === "commands,poll_interval" &&
        r.connection.host === "192.168.1.50" &&
        r.connection.ssl === true &&
        r.config.poll_interval === 5 &&
        Object.keys(none.connection).length === 0 &&
        none.config.poll_interval === 5,
      detail: { connKeys, cfgKeys },
    };
  }
}

process.stdout.write(JSON.stringify(results));
