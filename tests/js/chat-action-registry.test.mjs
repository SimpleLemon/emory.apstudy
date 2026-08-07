import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import * as acorn from "acorn";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const chatRoot = path.join(repoRoot, "static/js/chat");
const requiredActions = [
  "closeInlineProfilePopover",
  "dmPresenceStatus",
  "dmPresenceMarkup",
  "handleActiveRoomPresenceChange",
  "registerKnownUser",
  "renderApprovalNotice",
  "unreadAnnouncementMessages",
];

function parseModule(source, filePath) {
  return acorn.parse(source, {
    ecmaVersion: "latest",
    locations: true,
    sourceFile: filePath,
    sourceType: "module",
  });
}

function walkAst(node, visit) {
  if (!node || typeof node !== "object") return;
  visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (key === "loc" || key === "start" || key === "end") continue;
    if (Array.isArray(value)) {
      value.forEach((child) => walkAst(child, visit));
    } else if (value && typeof value === "object") {
      walkAst(value, visit);
    }
  }
}

function parseConsumedActions(source, filePath) {
  const ast = parseModule(source, filePath);
  const consumed = new Map();
  walkAst(ast, (node) => {
    if (
      node.type !== "MemberExpression" ||
      node.computed ||
      node.object?.type !== "Identifier" ||
      node.object.name !== "actions" ||
      node.property?.type !== "Identifier"
    ) return;
    const locations = consumed.get(node.property.name) || [];
    locations.push(`${filePath}:${node.loc.start.line}`);
    consumed.set(node.property.name, locations);
  });
  return consumed;
}

function parseRegisteredActions(source, filePath) {
  const ast = parseModule(source, filePath);
  const registryCalls = [];
  walkAst(ast, (node) => {
    if (
      node.type !== "CallExpression" ||
      node.callee?.type !== "MemberExpression" ||
      node.callee.computed ||
      node.callee.object?.type !== "Identifier" ||
      node.callee.object.name !== "Object" ||
      node.callee.property?.type !== "Identifier" ||
      node.callee.property.name !== "assign" ||
      node.arguments?.[0]?.type !== "Identifier" ||
      node.arguments[0].name !== "actions" ||
      node.arguments?.[1]?.type !== "ObjectExpression"
    ) return;
    registryCalls.push(node.arguments[1]);
  });

  assert.equal(
    registryCalls.length,
    1,
    `Expected exactly one Object.assign(actions, ...) registry in ${filePath}`,
  );

  const registered = new Map();
  for (const property of registryCalls[0].properties) {
    assert.equal(
      property.type,
      "Property",
      `Cannot structurally verify spread registry entry in ${filePath}:${property.loc.start.line}`,
    );
    assert.equal(
      property.computed,
      false,
      `Cannot structurally verify computed registry key in ${filePath}:${property.loc.start.line}`,
    );
    const key = property.key.type === "Identifier"
      ? property.key.name
      : property.key.type === "Literal" && typeof property.key.value === "string"
        ? property.key.value
        : null;
    assert.ok(key, `Unsupported registry key in ${filePath}:${property.loc.start.line}`);
    registered.set(key, `${filePath}:${property.loc.start.line}`);
  }
  return registered;
}

async function readChatSources() {
  const entries = await readdir(chatRoot, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".js"))
    .map((entry) => entry.name)
    .sort();
  const sources = await Promise.all(files.map(async (fileName) => {
    const filePath = path.join("static/js/chat", fileName);
    return [filePath, await readFile(path.join(chatRoot, fileName), "utf8")];
  }));
  return new Map(sources);
}

function registryCoverage(sources) {
  const consumed = new Map();
  for (const [filePath, source] of sources) {
    for (const [name, locations] of parseConsumedActions(source, filePath)) {
      consumed.set(name, [...(consumed.get(name) || []), ...locations]);
    }
  }
  const runtimeSource = sources.get("static/js/chat/runtime.js");
  assert.ok(runtimeSource, "Chat runtime source was not included in the structural sweep");
  const registered = parseRegisteredActions(runtimeSource, "static/js/chat/runtime.js");
  const missing = [...consumed.keys()]
    .filter((name) => !registered.has(name))
    .sort();
  return { consumed, registered, missing };
}

test("chat action registry structurally covers every consumed action", async () => {
  const sources = await readChatSources();
  const { consumed, registered, missing } = registryCoverage(sources);
  const missingDetails = missing
    .map((name) => `${name} (consumed at ${consumed.get(name).join(", ")})`)
    .join("\n");

  assert.equal(
    missing.length,
    0,
    `Consumed chat actions missing from Object.assign(actions, ...):\n${missingDetails}`,
  );
  assert.deepEqual(
    requiredActions.filter((name) => !registered.has(name)),
    [],
    "The confirmed chat action set must remain registered",
  );
  assert.ok(consumed.size > 0, "The structural sweep must find action member references");
});
