import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
    lstat,
    mkdir,
    mkdtemp,
    readFile,
    readdir,
    rename,
    rm,
    stat,
    symlink,
    writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
    buildCalendarPlan,
    graphVersion,
    manifestPath,
    recoverCalendarAssets,
    validateCalendarAssets,
    versionedSpecifier,
    walkCalendarGraph,
    writeCalendarAssets,
} from "../../scripts/calendar-assets.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const writerUrl = pathToFileURL(path.join(repoRoot, "scripts/calendar-assets.mjs")).href;

async function fixture() {
    const root = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-assets-"));
    const sourceRoot = path.join(root, "static");
    const stateRoot = path.join(root, "transaction");
    const entry = path.join(sourceRoot, "entry.js");
    const manifestFile = path.join(sourceRoot, "manifest.json");
    await mkdir(sourceRoot);
    await writeFile(entry, [
        'import "./side.js";',
        'import "vendor-package";',
        'export { nested } from "./nested.js";',
        'export const load = () => import("./dynamic.js");',
    ].join("\n"));
    await writeFile(path.join(sourceRoot, "side.js"), 'import "./entry.js";\nexport const side = true;\n');
    await writeFile(path.join(sourceRoot, "nested.js"), "export const nested = 1;\n");
    await writeFile(path.join(sourceRoot, "dynamic.js"), "export const dynamic = true;\n");
    const options = { sourceRoot, stateRoot, entry, manifestFile };
    await writeCalendarAssets(options);
    return { root, options };
}

async function snapshot(options) {
    const result = await validateCalendarAssets(options);
    const files = [...result.modules.map((module) => module.sourcePath), result.manifestFile];
    const values = new Map();
    for (const target of files) {
        const bytes = await readFile(target);
        const metadata = await stat(target, { bigint: true });
        values.set(target, {
            bytes: bytes.toString("base64"),
            gid: metadata.gid.toString(),
            ino: metadata.ino.toString(),
            mode: (metadata.mode & 0o7777n).toString(),
            mtimeNs: metadata.mtimeNs.toString(),
            uid: metadata.uid.toString(),
        });
    }
    return values;
}

async function assertSnapshot(expected) {
    const actual = new Map();
    for (const target of expected.keys()) {
        const bytes = await readFile(target);
        const metadata = await stat(target, { bigint: true });
        actual.set(target, {
            bytes: bytes.toString("base64"),
            gid: metadata.gid.toString(),
            ino: metadata.ino.toString(),
            mode: (metadata.mode & 0o7777n).toString(),
            mtimeNs: metadata.mtimeNs.toString(),
            uid: metadata.uid.toString(),
        });
    }
    assert.deepEqual(actual, expected);
}

async function replacementCount(options, sourceOverrides) {
    const plan = await buildCalendarPlan({ ...options, sourceOverrides });
    let count = 0;
    for (const module of plan.finalModules) {
        if (module.source !== plan.liveModules.find((live) => live.path === module.path).source) count += 1;
    }
    if (!plan.manifestBytes.equals(await readFile(options.manifestFile))) count += 1;
    return count;
}

function waitForExit(child) {
    return new Promise((resolve, reject) => {
        child.once("error", reject);
        child.once("exit", (code, signal) => resolve({ code, signal }));
    });
}

async function killAfterFirstReplacement(options, sourceOverrides) {
    return killAfterReplacement(options, sourceOverrides, 0);
}

async function killAfterReplacement(options, sourceOverrides, faultIndex) {
    const script = [
        `import { writeCalendarAssets } from ${JSON.stringify(writerUrl)};`,
        `await writeCalendarAssets({ ...${JSON.stringify(options)}, sourceOverrides: new Map(${JSON.stringify([...sourceOverrides])}),`,
        `faultAfterReplace({ index }) { if (index === ${faultIndex}) process.kill(process.pid, "SIGKILL"); } });`,
    ].join("\n");
    const child = spawn(process.execPath, ["--input-type=module", "--eval", script], { stdio: "ignore" });
    return waitForExit(child);
}

async function directorySnapshot(root) {
    const entries = (await readdir(root, { recursive: true })).sort();
    const result = [];
    for (const relative of entries) {
        const metadata = await lstat(path.join(root, relative), { bigint: true });
        result.push([relative, metadata.mode.toString(), metadata.mtimeNs.toString(), metadata.size.toString()]);
    }
    return result;
}

test("the repository graph and raw-hash manifest are exact and reachable", async () => {
    const result = await validateCalendarAssets();
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    assert.equal(result.version, manifest.version);
    assert.equal(manifest.schema, 2);
    assert.ok(result.modules.length >= 20);
    assert.ok(result.modules.some(({ path: modulePath }) => modulePath.endsWith("events/ui-actions.js")));
    assert.equal(manifest.modules.length, result.modules.length);
    for (const module of result.modules) {
        for (const record of module.localImports) {
            assert.equal(record.specifier, versionedSpecifier(record.specifier, result.version));
        }
    }
});

test("missing targets and unversioned or stale graph data fail check mode", async () => {
    const first = await fixture();
    try {
        const source = await readFile(first.options.entry, "utf8");
        await writeFile(first.options.entry, source.replace(/[?]v=[0-9a-f]{64}/, ""));
        await assert.rejects(validateCalendarAssets(first.options), /missing or stale/);
    } finally {
        await rm(first.root, { recursive: true, force: true });
    }

    const second = await fixture();
    try {
        await rm(path.join(second.options.sourceRoot, "nested.js"));
        await assert.rejects(validateCalendarAssets(second.options), /does not exist/);
    } finally {
        await rm(second.root, { recursive: true, force: true });
    }

    const third = await fixture();
    try {
        const manifest = JSON.parse(await readFile(third.options.manifestFile, "utf8"));
        manifest.version = "0".repeat(64);
        await writeFile(third.options.manifestFile, `${JSON.stringify(manifest)}\n`);
        await assert.rejects(validateCalendarAssets(third.options), /manifest|stale/);
    } finally {
        await rm(third.root, { recursive: true, force: true });
    }
});

test("content changes produce a new entry and every nested module URL", async () => {
    const result = await validateCalendarAssets();
    const changedModules = result.modules.map((module) => (
        module.path.endsWith("integrations/share.js")
            ? { ...module, source: `${module.source}\n// cache-busting regression fixture\n` }
            : module
    ));
    const changedVersion = graphVersion(changedModules);
    assert.notEqual(changedVersion, result.version);
    assert.equal(versionedSpecifier("./entry.js", changedVersion), `./entry.js?v=${changedVersion}`);
    for (const module of result.modules) {
        for (const record of module.localImports) {
            assert.match(versionedSpecifier(record.specifier, changedVersion), new RegExp(`[?&]v=${changedVersion}(?:&|#|$)`));
        }
    }
});

test("query parsing works and external imports and unrelated static modules remain untouched", async () => {
    const version = "a".repeat(64);
    assert.equal(
        versionedSpecifier("./module.js?mode=compact&v=old#entry", version),
        `./module.js?mode=compact&v=${version}#entry`,
    );
    assert.equal(versionedSpecifier("react", version), "react");
    assert.equal(versionedSpecifier("https://example.test/module.js?v=old", version), "https://example.test/module.js?v=old");
    const globalSource = await readFile(path.join(repoRoot, "static/js/core/global.js"), "utf8");
    assert.doesNotMatch(globalSource, /cookie-consent[.]js[?]v=/);
});

test("graph walking handles side effects, exports, dynamic imports, and cycles", async () => {
    const { root, options } = await fixture();
    try {
        const result = await validateCalendarAssets(options);
        assert.deepEqual(result.modules.map((module) => module.path), ["dynamic.js", "entry.js", "nested.js", "side.js"]);
        assert.equal(result.modules.find((module) => module.path === "entry.js").localImports.length, 3);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("realpath confinement rejects entry, import, manifest symlink escapes and traversal", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-confinement-"));
    const sourceRoot = path.join(root, "static");
    const outside = path.join(root, "outside.js");
    await mkdir(sourceRoot);
    await writeFile(outside, "export const escaped = true;\n");
    await symlink(outside, path.join(sourceRoot, "entry-link.js"));
    await assert.rejects(
        walkCalendarGraph(path.join(sourceRoot, "entry-link.js"), { sourceRoot }),
        /outside the static root/,
    );
    await writeFile(path.join(sourceRoot, "entry.js"), 'import "./escaped.js";\n');
    await symlink(outside, path.join(sourceRoot, "escaped.js"));
    await assert.rejects(walkCalendarGraph(path.join(sourceRoot, "entry.js"), { sourceRoot }), /outside the static root/);
    await writeFile(path.join(sourceRoot, "entry.js"), 'import "../outside.js";\n');
    await assert.rejects(walkCalendarGraph(path.join(sourceRoot, "entry.js"), { sourceRoot }), /traverses outside/);
    await writeFile(path.join(sourceRoot, "entry.js"), "export const safe = true;\n");
    await symlink(outside, path.join(sourceRoot, "manifest.json"));
    await assert.rejects(
        buildCalendarPlan({ sourceRoot, entry: path.join(sourceRoot, "entry.js"), manifestFile: path.join(sourceRoot, "manifest.json") }),
        /manifest resolves outside/,
    );
    await rm(root, { recursive: true, force: true });
});

test("ordinary injected faults after every replacement roll back exact originals", async () => {
    const { root, options } = await fixture();
    try {
        const original = await snapshot(options);
        const nested = await readFile(path.join(options.sourceRoot, "nested.js"), "utf8");
        const sourceOverrides = new Map([["nested.js", `${nested}// changed\n`]]);
        const count = await replacementCount(options, sourceOverrides);
        assert.ok(count >= 2);
        for (let faultIndex = 0; faultIndex < count; faultIndex += 1) {
            let faultedKind = null;
            await assert.rejects(writeCalendarAssets({
                ...options,
                sourceOverrides,
                faultAfterReplace({ entry, index }) {
                    if (index === faultIndex) {
                        faultedKind = entry.kind;
                        throw new Error(`fault-${index}`);
                    }
                },
            }), new RegExp(`fault-${faultIndex}`));
            if (faultIndex === count - 1) assert.equal(faultedKind, "manifest");
            await assertSnapshot(original);
            await validateCalendarAssets(options);
            assert.equal(await lstat(options.stateRoot).catch((error) => error.code), "ENOENT");
        }
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("rejected writes preserve a pre-existing transaction for explicit recovery", async () => {
    const { root, options } = await fixture();
    try {
        await mkdir(options.stateRoot);
        const journalPath = path.join(options.stateRoot, "journal.json");
        await writeFile(journalPath, "preserve this recovery journal\n");
        await assert.rejects(writeCalendarAssets(options), /state was preserved.*recover/);
        assert.equal(await readFile(journalPath, "utf8"), "preserve this recovery journal\n");
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("lock contention leaves the existing lock untouched", async () => {
    const { root, options } = await fixture();
    try {
        const lockPath = `${options.stateRoot}.lock`;
        const lockBytes = "another transaction owns this lock\n";
        await writeFile(lockPath, lockBytes);
        await assert.rejects(writeCalendarAssets(options), /lock is already held/);
        assert.equal(await readFile(lockPath, "utf8"), lockBytes);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("corrupt recovery journals fail closed and preserve transaction artifacts", async () => {
    const { root, options } = await fixture();
    try {
        const nested = await readFile(path.join(options.sourceRoot, "nested.js"), "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([["nested.js", `${nested}// corrupt journal\n`]]));
        assert.equal(exit.signal, "SIGKILL");
        const journalPath = path.join(options.stateRoot, "journal.json");
        await writeFile(journalPath, "{ deliberately corrupt\n");
        await assert.rejects(recoverCalendarAssets(options), /recovery journal is invalid/);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
        assert.equal(await readFile(journalPath, "utf8"), "{ deliberately corrupt\n");
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("committed recovery journals require transaction ownership and live graph integrity", async () => {
    const { root, options } = await fixture();
    try {
        const original = await readFile(options.entry, "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([["entry.js", `${original}// forged commit\n`]]));
        assert.equal(exit.signal, "SIGKILL");
        const journalPath = path.join(options.stateRoot, "journal.json");
        const journal = JSON.parse(await readFile(journalPath, "utf8"));
        journal.phase = "committed";
        for (const entry of journal.entries) entry.applied = entry.changed;
        await writeFile(journalPath, `${JSON.stringify(journal, null, 2)}\n`);
        await assert.rejects(recoverCalendarAssets(options), /committed target|raw module hashes|stale/);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
        assert.notEqual(await readFile(options.entry, "utf8"), original);

        journal.transactionId = "11111111-1111-4111-8111-111111111111";
        await writeFile(journalPath, `${JSON.stringify(journal, null, 2)}\n`);
        await assert.rejects(recoverCalendarAssets(options), /does not match this transaction/);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("backup symlink escapes fail closed without moving the symlink into the live graph", async () => {
    const { root, options } = await fixture();
    try {
        const original = await readFile(options.entry, "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([["entry.js", `${original}// backup symlink\n`]]));
        assert.equal(exit.signal, "SIGKILL");
        const journal = JSON.parse(await readFile(path.join(options.stateRoot, "journal.json"), "utf8"));
        const entry = journal.entries.find((candidate) => candidate.path === "entry.js");
        const backup = path.join(options.stateRoot, entry.backup);
        const outside = path.join(root, "outside-original.js");
        await writeFile(outside, original);
        await rm(backup);
        await symlink(outside, backup);
        await assert.rejects(recoverCalendarAssets(options), /backup identity|symlink|regular/);
        assert.equal((await lstat(options.entry)).isFile(), true);
        assert.equal((await lstat(backup)).isSymbolicLink(), true);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("stage-file symlinks fail closed and preserve the recovery state", async () => {
    const { root, options } = await fixture();
    try {
        const original = await readFile(options.entry, "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([[
            "entry.js",
            `${original}// stage symlink\n`,
        ]]));
        assert.equal(exit.signal, "SIGKILL");
        const journal = JSON.parse(await readFile(path.join(options.stateRoot, "journal.json"), "utf8"));
        const staged = path.join(options.stateRoot, journal.entries.find((entry) => entry.path === "entry.js").staged);
        const outside = path.join(root, "outside-stage.js");
        const outsideBytes = "outside stage sentinel\n";
        await writeFile(outside, outsideBytes);
        await rm(staged);
        await symlink(outside, staged);
        await assert.rejects(recoverCalendarAssets(options), /symlink|regular/);
        assert.equal(await readFile(outside, "utf8"), outsideBytes);
        assert.equal((await lstat(staged)).isSymbolicLink(), true);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
        assert.notEqual(await readFile(options.entry, "utf8"), original);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("journal symlinks fail closed without reading the outside target", async () => {
    const { root, options } = await fixture();
    try {
        const original = await readFile(options.entry, "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([[
            "entry.js",
            `${original}// journal symlink\n`,
        ]]));
        assert.equal(exit.signal, "SIGKILL");
        const journalPath = path.join(options.stateRoot, "journal.json");
        const outside = path.join(root, "outside-journal.json");
        const outsideBytes = "outside journal sentinel\n";
        await writeFile(outside, outsideBytes);
        await rm(journalPath);
        await symlink(outside, journalPath);
        await assert.rejects(recoverCalendarAssets(options), /recovery journal is invalid/);
        assert.equal(await readFile(outside, "utf8"), outsideBytes);
        assert.equal((await lstat(journalPath)).isSymbolicLink(), true);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("live-target symlinks fail closed without touching their outside target", async () => {
    const { root, options } = await fixture();
    try {
        const original = await readFile(options.entry, "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([[
            "entry.js",
            `${original}// live symlink\n`,
        ]]));
        assert.equal(exit.signal, "SIGKILL");
        const outside = path.join(root, "outside-live.js");
        const outsideBytes = "outside live sentinel\n";
        await writeFile(outside, outsideBytes);
        await rm(options.entry);
        await symlink(outside, options.entry);
        await assert.rejects(recoverCalendarAssets(options), /symlink|target/);
        assert.equal(await readFile(outside, "utf8"), outsideBytes);
        assert.equal((await lstat(options.entry)).isSymbolicLink(), true);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("cleanup through a symlinked transaction root fails closed", async () => {
    const { root, options } = await fixture();
    try {
        const nested = await readFile(path.join(options.sourceRoot, "nested.js"), "utf8");
        const sourceOverrides = new Map([["nested.js", `${nested}// cleanup symlink\n`]]);
        const count = await replacementCount(options, sourceOverrides);
        const exit = await killAfterReplacement(options, sourceOverrides, count - 1);
        assert.equal(exit.signal, "SIGKILL");
        const journalPath = path.join(options.stateRoot, "journal.json");
        const journal = JSON.parse(await readFile(journalPath, "utf8"));
        journal.phase = "committed";
        for (const entry of journal.entries) entry.applied = entry.changed;
        await writeFile(journalPath, `${JSON.stringify(journal, null, 2)}\n`);
        const preservedState = path.join(root, "preserved-transaction");
        await rename(options.stateRoot, preservedState);
        const outside = path.join(root, "outside-cleanup");
        await mkdir(outside);
        const outsideFile = path.join(outside, "sentinel");
        await writeFile(outsideFile, "outside cleanup sentinel\n");
        await symlink(outside, options.stateRoot);
        await assert.rejects(recoverCalendarAssets(options), /state may not be a symlink/);
        assert.equal((await lstat(options.stateRoot)).isSymbolicLink(), true);
        assert.equal((await lstat(preservedState)).isDirectory(), true);
        assert.equal(await readFile(outsideFile, "utf8"), "outside cleanup sentinel\n");
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("backup traversal mutations fail closed and preserve the recovery journal", async () => {
    const { root, options } = await fixture();
    try {
        const original = await readFile(options.entry, "utf8");
        const exit = await killAfterFirstReplacement(options, new Map([[
            "entry.js",
            `${original}// backup traversal\n`,
        ]]));
        assert.equal(exit.signal, "SIGKILL");
        const journalPath = path.join(options.stateRoot, "journal.json");
        const journal = JSON.parse(await readFile(journalPath, "utf8"));
        journal.entries.find((entry) => entry.path === "entry.js").backup = "backup/../../outside-backup.js";
        const forgedBytes = `${JSON.stringify(journal, null, 2)}\n`;
        await writeFile(journalPath, forgedBytes);
        const outside = path.join(root, "outside-backup.js");
        const outsideBytes = "outside backup sentinel\n";
        await writeFile(outside, outsideBytes);
        await assert.rejects(recoverCalendarAssets(options), /escapes|invalid/);
        assert.equal(await readFile(outside, "utf8"), outsideBytes);
        assert.equal(await readFile(journalPath, "utf8"), forgedBytes);
        assert.equal((await lstat(options.stateRoot)).isDirectory(), true);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("SIGINT and SIGTERM roll back synchronously before the writer rejects", async () => {
    const { root, options } = await fixture();
    try {
        const original = await snapshot(options);
        const nested = await readFile(path.join(options.sourceRoot, "nested.js"), "utf8");
        for (const signal of ["SIGINT", "SIGTERM"]) {
            let sent = false;
            await assert.rejects(writeCalendarAssets({
                ...options,
                sourceOverrides: new Map([["nested.js", `${nested}// ${signal}\n`]]),
                async faultAfterReplace() {
                    if (sent) return;
                    sent = true;
                    process.kill(process.pid, signal);
                    await new Promise((resolve) => setImmediate(resolve));
                },
            }), new RegExp(`interrupted by ${signal}`));
            await assertSnapshot(original);
            await validateCalendarAssets(options);
        }
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("SIGKILL after every replacement is idempotently recoverable", async () => {
    const { root, options } = await fixture();
    try {
        const original = await snapshot(options);
        const nested = await readFile(path.join(options.sourceRoot, "nested.js"), "utf8");
        const changed = `${nested}// killed\n`;
        const count = await replacementCount(options, new Map([["nested.js", changed]]));
        for (let faultIndex = 0; faultIndex < count; faultIndex += 1) {
            const script = [
                `import { writeCalendarAssets } from ${JSON.stringify(writerUrl)};`,
                `await writeCalendarAssets({ ...${JSON.stringify(options)}, sourceOverrides: new Map([["nested.js", ${JSON.stringify(changed)}]]),`,
                `faultAfterReplace({ index }) { if (index === ${faultIndex}) process.kill(process.pid, "SIGKILL"); } });`,
            ].join("\n");
            const child = spawn(process.execPath, ["--input-type=module", "--eval", script], { stdio: "ignore" });
            const exit = await waitForExit(child);
            assert.equal(exit.signal, "SIGKILL");
            const recovered = await recoverCalendarAssets(options);
            assert.equal(recovered.recovered, true);
            await assertSnapshot(original);
            await validateCalendarAssets(options);
            assert.deepEqual(await recoverCalendarAssets(options), { recovered: false });
        }
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("check is read-only, clean writes are idempotent, and incomplete transactions are refused", async () => {
    const { root, options } = await fixture();
    try {
        const before = await directorySnapshot(root);
        await validateCalendarAssets(options);
        assert.deepEqual(await directorySnapshot(root), before);
        await writeCalendarAssets(options);
        assert.deepEqual(await directorySnapshot(root), before);
        await mkdir(options.stateRoot);
        await assert.rejects(validateCalendarAssets(options), /incomplete/);
        await assert.rejects(writeCalendarAssets(options), /incomplete/);
        await recoverCalendarAssets(options);
        await validateCalendarAssets(options);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("both calendar templates use the runtime manifest version for entry.js", async () => {
    const [calendar, share] = await Promise.all([
        readFile(path.join(repoRoot, "templates/calendar.html"), "utf8"),
        readFile(path.join(repoRoot, "templates/calendar_share.html"), "utf8"),
    ]);
    for (const template of [calendar, share]) {
        assert.match(template, /js\/calendar\/entry[.]js', v=calendar_asset_version/);
    }
});
