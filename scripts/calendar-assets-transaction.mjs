import { createHash, randomUUID } from "node:crypto";
import { O_NOFOLLOW, O_RDONLY } from "node:constants";
import {
    link,
    lstat,
    mkdir,
    open,
    realpath,
    rename,
    rm,
    stat,
    unlink,
} from "node:fs/promises";
import path from "node:path";

import { buildCalendarPlan, validateCalendarAssets } from "./calendar-assets-graph.mjs";

const JOURNAL_SCHEMA = 2;
const TRANSACTION_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const JOURNAL_PHASES = new Set(["prepared", "applying", "validating", "committed", "rolling-back", "rolled-back"]);

function isWithin(root, candidate) {
    const relative = path.relative(root, candidate);
    return relative === "" || (relative && !relative.startsWith("..") && !path.isAbsolute(relative));
}

async function exists(target) {
    try {
        return await lstat(target);
    } catch (error) {
        if (error.code === "ENOENT") return null;
        throw error;
    }
}

async function syncDirectory(directory) {
    const handle = await open(directory, "r");
    try {
        await handle.sync();
    } finally {
        await handle.close();
    }
}

async function writeSynced(target, bytes, mode = 0o600) {
    const handle = await open(target, "wx", mode);
    try {
        await handle.writeFile(bytes);
        await handle.sync();
    } finally {
        await handle.close();
    }
}

async function readBytesNoFollow(target, label) {
    let handle;
    try {
        handle = await open(target, O_RDONLY | O_NOFOLLOW);
    } catch (error) {
        if (error.code === "ELOOP") throw new Error(`Calendar transaction ${label} may not be a symlink.`, { cause: error });
        throw error;
    }
    try {
        const metadata = await handle.stat({ bigint: true });
        if (!metadata.isFile()) throw new Error(`Calendar transaction ${label} is not a regular file.`);
        return await handle.readFile();
    } finally {
        await handle.close();
    }
}

async function assertNoSymlinkPath(target, root, label) {
    const resolvedTarget = path.resolve(target);
    const resolvedRoot = path.resolve(root);
    if (!isWithin(resolvedRoot, resolvedTarget)) {
        throw new Error(`Calendar transaction ${label} path escapes its root.`);
    }
    try {
        const rootMetadata = await lstat(resolvedRoot);
        if (rootMetadata.isSymbolicLink()) {
            throw new Error(`Calendar transaction ${label} root may not be a symlink: ${resolvedRoot}`);
        }
        if (!rootMetadata.isDirectory()) {
            throw new Error(`Calendar transaction ${label} root is not a directory: ${resolvedRoot}`);
        }
    } catch (error) {
        if (error.code !== "ENOENT") throw error;
        return;
    }
    let current = resolvedRoot;
    const relative = path.relative(resolvedRoot, resolvedTarget);
    for (const part of relative ? relative.split(path.sep) : []) {
        current = path.join(current, part);
        let metadata;
        try {
            metadata = await lstat(current);
        } catch (error) {
            if (error.code === "ENOENT") return;
            throw error;
        }
        if (metadata.isSymbolicLink()) {
            throw new Error(`Calendar transaction ${label} path may not contain a symlink: ${current}`);
        }
        if (current !== resolvedTarget && !metadata.isDirectory()) {
            throw new Error(`Calendar transaction ${label} parent is not a directory: ${current}`);
        }
    }
}

async function mkdirConfined(target, root, label, mode = 0o700) {
    const resolvedTarget = path.resolve(target);
    const resolvedRoot = path.resolve(root);
    if (!isWithin(resolvedRoot, resolvedTarget)) {
        throw new Error(`Calendar transaction ${label} path escapes its root.`);
    }
    await assertNoSymlinkPath(resolvedRoot, path.parse(resolvedRoot).root, label);
    let current = resolvedRoot;
    const relative = path.relative(resolvedRoot, resolvedTarget);
    for (const part of relative ? relative.split(path.sep) : []) {
        current = path.join(current, part);
        try {
            const metadata = await lstat(current);
            if (metadata.isSymbolicLink()) {
                throw new Error(`Calendar transaction ${label} path may not contain a symlink: ${current}`);
            }
            if (!metadata.isDirectory()) {
                throw new Error(`Calendar transaction ${label} path is not a directory: ${current}`);
            }
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
            await mkdir(current, { mode });
        }
    }
}

async function atomicWrite(target, bytes, mode, suffix) {
    const temporary = `${target}.calendar-assets-${process.pid}-${suffix}.tmp`;
    try {
        await writeSynced(temporary, bytes, mode);
        await rename(temporary, target);
        await syncDirectory(path.dirname(target));
    } finally {
        try {
            await unlink(temporary);
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
        }
    }
}

function sha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

async function transactionLocations(sourceRoot, stateRoot) {
    const lexical = path.resolve(stateRoot || path.join(sourceRoot, ".calendar-assets-transaction"));
    const state = path.join(await realpath(path.dirname(lexical)), path.basename(lexical));
    await assertNoSymlinkPath(path.dirname(state), path.parse(state).root, "transaction");
    return { state, lock: `${state}.lock`, journal: path.join(state, "journal.json") };
}

export async function assertNoCalendarAssetTransaction({ sourceRoot, stateRoot }) {
    const resolvedRoot = await realpath(path.resolve(sourceRoot));
    const locations = await transactionLocations(resolvedRoot, stateRoot);
    if (await exists(locations.state) || await exists(locations.lock)) {
        throw new Error("Calendar asset transaction is incomplete; run `node scripts/calendar-assets.mjs --recover`.");
    }
    return locations;
}

async function acquireLock(lock, metadata) {
    let handle;
    try {
        handle = await open(lock, "wx", 0o600);
    } catch (error) {
        if (error.code === "EEXIST") throw new Error("Calendar asset transaction lock is already held.");
        throw error;
    }
    try {
        await handle.writeFile(`${JSON.stringify(metadata)}\n`);
        await handle.sync();
    } finally {
        await handle.close();
    }
    await syncDirectory(path.dirname(lock));
}

async function releaseLock(lock) {
    try {
        await unlink(lock);
        await syncDirectory(path.dirname(lock));
    } catch (error) {
        if (error.code !== "ENOENT") throw error;
    }
}

async function writeJournal(locations, journal) {
    const bytes = Buffer.from(`${JSON.stringify(journal, null, 2)}\n`, "utf8");
    const temporary = `${locations.journal}.next`;
    try {
        const existing = await exists(temporary);
        if (existing) await unlink(temporary);
        await writeSynced(temporary, bytes, 0o600);
        await rename(temporary, locations.journal);
        await syncDirectory(locations.state);
    } finally {
        try {
            await unlink(temporary);
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
        }
    }
}

async function metadataFor(target) {
    let handle;
    try {
        handle = await open(target, O_RDONLY | O_NOFOLLOW);
    } catch (error) {
        if (error.code === "ELOOP") throw new Error(`Calendar transaction file may not be a symlink: ${target}`, { cause: error });
        throw error;
    }
    let bytes;
    let metadata;
    try {
        metadata = await handle.stat({ bigint: true });
        if (!metadata.isFile()) throw new Error(`Calendar transaction path is not a regular file: ${target}`);
        bytes = await handle.readFile();
    } finally {
        await handle.close();
    }
    return {
        atimeNs: metadata.atimeNs.toString(),
        gid: Number(metadata.gid),
        ino: metadata.ino.toString(),
        mode: Number(metadata.mode & 0o7777n),
        mtimeNs: metadata.mtimeNs.toString(),
        sha256: sha256(bytes),
        size: Number(metadata.size),
        uid: Number(metadata.uid),
    };
}

async function readTextNoFollow(target, label) {
    let handle;
    try {
        handle = await open(target, O_RDONLY | O_NOFOLLOW);
    } catch (error) {
        if (error.code === "ELOOP") throw new Error(`Calendar transaction ${label} may not be a symlink.`, { cause: error });
        throw error;
    }
    try {
        const metadata = await handle.stat({ bigint: true });
        if (!metadata.isFile()) throw new Error(`Calendar transaction ${label} is not a regular file.`);
        return await handle.readFile("utf8");
    } finally {
        await handle.close();
    }
}

function sameMetadata(actual, expected) {
    return Boolean(actual && expected)
        && actual.sha256 === expected.sha256
        && actual.size === expected.size
        && actual.mode === expected.mode
        && actual.uid === expected.uid
        && actual.gid === expected.gid
        && actual.ino === expected.ino
        && actual.mtimeNs === expected.mtimeNs;
}

function assertMetadataShape(metadata, label) {
    if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
        throw new Error(`Calendar transaction ${label} metadata is invalid.`);
    }
    const keys = Object.keys(metadata).sort();
    if (JSON.stringify(keys) !== JSON.stringify(["atimeNs", "gid", "ino", "mode", "mtimeNs", "sha256", "size", "uid"])) {
        throw new Error(`Calendar transaction ${label} metadata is invalid.`);
    }
    if (!/^[0-9a-f]{64}$/.test(metadata.sha256)
        || !["atimeNs", "ino", "mtimeNs"].every((key) => /^\d+$/.test(metadata[key]))
        || !["gid", "mode", "size", "uid"].every((key) => Number.isSafeInteger(metadata[key]) && metadata[key] >= 0)
        || metadata.mode > 0o7777) {
        throw new Error(`Calendar transaction ${label} metadata is invalid.`);
    }
}

function assertExactKeys(value, keys, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)
        || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) {
        throw new Error(`Calendar transaction ${label} is invalid.`);
    }
}

async function stagePlan(plan, locations) {
    const stageRoot = path.join(locations.state, "stage");
    await mkdirConfined(stageRoot, locations.state, "stage");
    for (const module of plan.finalModules) {
        const target = path.join(stageRoot, module.path);
        await mkdirConfined(path.dirname(target), stageRoot, "stage");
        const liveMode = Number((await stat(module.sourcePath, { bigint: true })).mode & 0o7777n);
        await writeSynced(target, Buffer.from(module.source, "utf8"), liveMode);
    }
    const stagedManifest = path.join(stageRoot, path.relative(plan.sourceRoot, plan.manifestFile));
    await mkdirConfined(path.dirname(stagedManifest), stageRoot, "stage");
    const manifestMetadata = await exists(plan.manifestFile);
    await writeSynced(stagedManifest, plan.manifestBytes, manifestMetadata ? manifestMetadata.mode & 0o7777 : 0o644);
    await syncDirectory(stageRoot);
    await validateCalendarAssets({
        sourceRoot: stageRoot,
        entry: path.join(stageRoot, plan.entryPath),
        manifestFile: stagedManifest,
    });
    return { stageRoot, stagedManifest };
}

async function prepareJournal(plan, locations, stageRoot, transactionId) {
    const backupRoot = path.join(locations.state, "backup");
    await mkdirConfined(backupRoot, locations.state, "backup");
    const destinations = [
        ...plan.finalModules.map((module) => ({
            kind: "module",
            relativePath: module.path,
            stagedPath: path.join(stageRoot, module.path),
        })),
        {
            kind: "manifest",
            relativePath: path.relative(plan.sourceRoot, plan.manifestFile).split(path.sep).join("/"),
            stagedPath: path.join(stageRoot, path.relative(plan.sourceRoot, plan.manifestFile)),
        },
    ];
    const entries = [];
    for (const destination of destinations) {
        const target = path.join(plan.sourceRoot, destination.relativePath);
        await assertNoSymlinkPath(target, plan.sourceRoot, "target");
        const original = await exists(target) ? await metadataFor(target) : null;
        const stagedBytes = await readBytesNoFollow(destination.stagedPath, "staged file");
        const changed = !original || original.sha256 !== sha256(stagedBytes);
        const backupPath = path.join(backupRoot, destination.relativePath);
        if (original && changed) {
            await mkdirConfined(path.dirname(backupPath), backupRoot, "backup");
            await assertNoSymlinkPath(backupPath, backupRoot, "backup");
            await link(target, backupPath);
            const handle = await open(backupPath, "r");
            try {
                await handle.sync();
            } finally {
                await handle.close();
            }
        }
        entries.push({
            applied: false,
            backup: original && changed ? path.relative(locations.state, backupPath).split(path.sep).join("/") : null,
            changed,
            kind: destination.kind,
            original,
            path: destination.relativePath,
            staged: path.relative(locations.state, destination.stagedPath).split(path.sep).join("/"),
            stagedSha256: sha256(stagedBytes),
        });
    }
    await syncDirectory(backupRoot);
    const journal = {
        schema: JOURNAL_SCHEMA,
        phase: "prepared",
        sourceRoot: plan.sourceRoot,
        stateRoot: locations.state,
        manifest: path.relative(plan.sourceRoot, plan.manifestFile).split(path.sep).join("/"),
        transactionId,
        version: plan.version,
        entries,
    };
    await writeJournal(locations, journal);
    return journal;
}

function assertJournalPath(relativePath, root, label) {
    if (typeof relativePath !== "string" || path.isAbsolute(relativePath)) {
        throw new Error(`Calendar transaction ${label} path is invalid.`);
    }
    const target = path.resolve(root, relativePath);
    if (!isWithin(root, target)) throw new Error(`Calendar transaction ${label} path escapes its root.`);
    return target;
}

async function assertConfinedParent(target, root, label) {
    await assertNoSymlinkPath(path.dirname(target), root, label);
}

async function matchesOriginal(target, original) {
    if (!original) return !(await exists(target));
    const current = await exists(target);
    if (!current?.isFile()) return false;
    const metadata = await metadataFor(target);
    return sameMetadata(metadata, original);
}

async function restoreFromBackup(locations, entry, target, index) {
    const backupRoot = path.join(locations.state, "backup");
    const backup = assertJournalPath(entry.backup, locations.state, "backup");
    if (!isWithin(backupRoot, backup)) throw new Error(`Calendar transaction backup escapes backup/: ${entry.path}`);
    await assertConfinedParent(backup, backupRoot, "backup");
    await assertNoSymlinkPath(backup, backupRoot, "backup");
    await assertNoSymlinkPath(target, path.dirname(target), "target");
    const backupMetadata = await metadataFor(backup);
    if (!sameMetadata(backupMetadata, entry.original)) {
        throw new Error(`Calendar transaction backup identity is invalid: ${entry.path}`);
    }
    const temporary = `${target}.calendar-assets-restore-${process.pid}-${index}.tmp`;
    try {
        await link(backup, temporary);
        const temporaryMetadata = await metadataFor(temporary);
        if (!sameMetadata(temporaryMetadata, entry.original)) {
            throw new Error(`Calendar transaction backup identity is invalid: ${entry.path}`);
        }
        await rename(temporary, target);
        await syncDirectory(path.dirname(target));
    } finally {
        try {
            await unlink(temporary);
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
        }
    }
}

async function rollbackJournal(locations, journal) {
    journal.phase = "rolling-back";
    await writeJournal(locations, journal);
    for (let index = 0; index < journal.entries.length; index += 1) {
        const entry = journal.entries[index];
        if (!entry.changed) continue;
        const target = assertJournalPath(entry.path, journal.sourceRoot, "target");
        await assertConfinedParent(target, journal.sourceRoot, "target");
        await assertNoSymlinkPath(target, journal.sourceRoot, "target");
        if (!(await matchesOriginal(target, entry.original))) {
            if (!entry.original) {
                try {
                    await unlink(target);
                    await syncDirectory(path.dirname(target));
                } catch (error) {
                    if (error.code !== "ENOENT") throw error;
                }
            } else {
                await restoreFromBackup(locations, entry, target, index);
            }
        }
        if (!(await matchesOriginal(target, entry.original))) {
            throw new Error(`Calendar transaction could not restore the original file: ${entry.path}`);
        }
        entry.applied = false;
        await writeJournal(locations, journal);
    }
    journal.phase = "rolled-back";
    await writeJournal(locations, journal);
}

async function cleanupState(locations) {
    const stateMetadata = await exists(locations.state);
    if (stateMetadata && (!stateMetadata.isDirectory() || stateMetadata.isSymbolicLink())) {
        throw new Error("Calendar asset transaction state must be a non-symlink directory.");
    }
    await assertNoSymlinkPath(locations.state, path.dirname(locations.state), "cleanup");
    await rm(locations.state, { recursive: true, force: true });
    await syncDirectory(path.dirname(locations.state));
}

function installSignalTrap() {
    let signal = null;
    const onInterrupt = () => { signal = "SIGINT"; };
    const onTerminate = () => { signal = "SIGTERM"; };
    process.on("SIGINT", onInterrupt);
    process.on("SIGTERM", onTerminate);
    return {
        check() {
            if (signal) throw new Error(`Calendar asset transaction interrupted by ${signal}.`);
        },
        remove() {
            process.off("SIGINT", onInterrupt);
            process.off("SIGTERM", onTerminate);
        },
    };
}

async function validateTransactionFilesystem(sourceRoot, state) {
    const stateParent = path.dirname(state);
    await assertNoSymlinkPath(stateParent, path.parse(state).root, "transaction");
    const sourceMetadata = await stat(sourceRoot, { bigint: true });
    const stateParentMetadata = await stat(stateParent, { bigint: true });
    if (sourceMetadata.dev !== stateParentMetadata.dev) {
        throw new Error("Calendar asset transaction state must be on the same filesystem as static/.");
    }
    const stateMetadata = await exists(state);
    if (stateMetadata) {
        if (stateMetadata.isSymbolicLink()) throw new Error("Calendar asset transaction state may not be a symlink.");
        throw new Error("Calendar asset transaction is incomplete; run --recover.");
    }
}

export async function writeCalendarAssets(options) {
    const plan = await buildCalendarPlan(options);
    const locations = await transactionLocations(plan.sourceRoot, options.stateRoot);
    const transactionId = randomUUID();
    await acquireLock(locations.lock, {
        pid: process.pid,
        sourceRoot: plan.sourceRoot,
        stateRoot: locations.state,
        transactionId,
    });
    const trap = installSignalTrap();
    let journal = null;
    let committed = false;
    try {
        await validateTransactionFilesystem(plan.sourceRoot, locations.state);
        await mkdirConfined(locations.state, path.dirname(locations.state), "state");
        await syncDirectory(path.dirname(locations.state));
        const { stageRoot } = await stagePlan(plan, locations);
        trap.check();
        journal = await prepareJournal(plan, locations, stageRoot, transactionId);
        if (!journal.entries.some((entry) => entry.changed)) {
            await cleanupState(locations);
            return validateCalendarAssets(plan);
        }
        journal.phase = "applying";
        await writeJournal(locations, journal);
        const changes = journal.entries.filter((entry) => entry.changed);
        for (let index = 0; index < changes.length; index += 1) {
            trap.check();
            const entry = changes[index];
            const target = assertJournalPath(entry.path, plan.sourceRoot, "target");
            const staged = assertJournalPath(entry.staged, locations.state, "staged");
            await assertConfinedParent(target, plan.sourceRoot, "target");
            await assertConfinedParent(staged, locations.state, "staged");
            await assertNoSymlinkPath(target, plan.sourceRoot, "target");
            await assertNoSymlinkPath(staged, locations.state, "staged");
            const mode = entry.original?.mode ?? 0o644;
            await atomicWrite(target, await readBytesNoFollow(staged, "staged file"), mode, index);
            entry.applied = true;
            await writeJournal(locations, journal);
            if (options.faultAfterReplace) {
                await options.faultAfterReplace({ entry, index, total: changes.length });
            }
            trap.check();
        }
        journal.phase = "validating";
        await writeJournal(locations, journal);
        const result = await validateCalendarAssets(plan);
        journal.phase = "committed";
        await writeJournal(locations, journal);
        committed = true;
        await cleanupState(locations);
        return result;
    } catch (error) {
        if (!committed && journal) {
            try {
                await rollbackJournal(locations, journal);
                await cleanupState(locations);
            } catch (rollbackError) {
                throw new AggregateError([error, rollbackError], "Calendar asset write failed and requires --recover.");
            }
        } else if (!journal && await exists(locations.state)) {
            throw new Error(`${error.message} Calendar asset transaction state was preserved; run \`node scripts/calendar-assets.mjs --recover\`.`,
                { cause: error });
        }
        throw error;
    } finally {
        trap.remove();
        await releaseLock(locations.lock);
    }
}

async function readJournal(locations) {
    try {
        return JSON.parse(await readTextNoFollow(locations.journal, "recovery journal"));
    } catch (error) {
        if (error.code === "ENOENT") return null;
        throw new Error("Calendar asset recovery journal is invalid.", { cause: error });
    }
}

function processIsAlive(pid) {
    if (!Number.isSafeInteger(pid) || pid <= 0) return false;
    try {
        process.kill(pid, 0);
        return true;
    } catch (error) {
        return error.code === "EPERM";
    }
}

async function removeStaleLock(lock) {
    const lockMetadata = await exists(lock);
    if (!lockMetadata) return;
    if (!lockMetadata.isFile()) throw new Error("Calendar asset transaction lock must be a regular file; inspect it manually.");
    let data;
    try {
        data = JSON.parse(await readTextNoFollow(lock, "lock"));
    } catch (error) {
        throw new Error("Calendar asset transaction lock is invalid; inspect it manually.", { cause: error });
    }
    assertExactKeys(data, ["pid", "sourceRoot", "stateRoot", "transactionId"], "lock metadata");
    if (!Number.isSafeInteger(data.pid) || data.pid <= 0
        || typeof data.sourceRoot !== "string"
        || typeof data.stateRoot !== "string"
        || !TRANSACTION_ID_PATTERN.test(data.transactionId)) {
        throw new Error("Calendar asset transaction lock is invalid; inspect it manually.");
    }
    if (processIsAlive(data.pid)) throw new Error("Calendar asset transaction is still active.");
    await releaseLock(lock);
    return data;
}

async function validateJournal(journal, sourceRoot, manifestFile, stateRoot, ownership) {
    const manifest = path.relative(sourceRoot, manifestFile).split(path.sep).join("/");
    assertExactKeys(
        journal,
        ["entries", "manifest", "phase", "schema", "sourceRoot", "stateRoot", "transactionId", "version"],
        "recovery journal",
    );
    if (journal.schema !== JOURNAL_SCHEMA
        || journal.sourceRoot !== sourceRoot
        || journal.stateRoot !== stateRoot
        || journal.manifest !== manifest
        || !TRANSACTION_ID_PATTERN.test(journal.transactionId)
        || !/^[0-9a-f]{64}$/.test(journal.version)
        || !JOURNAL_PHASES.has(journal.phase)
        || !Array.isArray(journal.entries)
        || !ownership
        || ownership.sourceRoot !== sourceRoot
        || ownership.stateRoot !== stateRoot
        || ownership.transactionId !== journal.transactionId) {
        throw new Error("Calendar asset recovery journal does not match this transaction.");
    }
    if (!journal.entries.length) throw new Error("Calendar asset recovery journal has no entries.");
    const stageRoot = path.join(stateRoot, "stage");
    const backupRoot = path.join(stateRoot, "backup");
    const paths = new Set();
    let changedEntries = 0;
    let manifestEntries = 0;
    for (const entry of journal.entries) {
        assertExactKeys(entry, ["applied", "backup", "changed", "kind", "original", "path", "staged", "stagedSha256"], "journal entry");
        if (typeof entry.applied !== "boolean" || typeof entry.changed !== "boolean"
            || !["module", "manifest"].includes(entry.kind)
            || typeof entry.path !== "string"
            || typeof entry.staged !== "string"
            || !/^[0-9a-f]{64}$/.test(entry.stagedSha256)) {
            throw new Error("Calendar asset recovery journal has invalid entries.");
        }
        const target = assertJournalPath(entry.path, sourceRoot, "target");
        if (entry.path !== path.relative(sourceRoot, target).split(path.sep).join("/") || paths.has(entry.path)) {
            throw new Error("Calendar asset recovery journal has non-canonical or duplicate paths.");
        }
        paths.add(entry.path);
        if (entry.kind === "manifest") {
            manifestEntries += 1;
            if (entry.path !== manifest) throw new Error("Calendar asset recovery journal has an invalid manifest entry.");
        } else if (!/[.]m?js$/i.test(entry.path)) {
            throw new Error("Calendar asset recovery journal has a non-module entry.");
        }
        const staged = assertJournalPath(entry.staged, stateRoot, "staged");
        if (entry.staged !== path.relative(stateRoot, staged).split(path.sep).join("/") || !isWithin(stageRoot, staged)) {
            throw new Error("Calendar asset recovery journal staged path escapes stage/.");
        }
        await assertConfinedParent(staged, stageRoot, "staged");
        if ((await metadataFor(staged)).sha256 !== entry.stagedSha256) {
            throw new Error(`Calendar asset recovery staged file is corrupt: ${entry.path}`);
        }
        if (entry.changed) changedEntries += 1;
        if (entry.original === null) {
            if (!entry.changed || entry.backup !== null) throw new Error("Calendar asset recovery journal has invalid new-file metadata.");
        } else {
            assertMetadataShape(entry.original, entry.path);
            if (!entry.changed) {
                if (entry.backup !== null || entry.stagedSha256 !== entry.original.sha256) {
                    throw new Error("Calendar asset recovery journal has invalid unchanged-file metadata.");
                }
                if (!(await matchesOriginal(target, entry.original))) {
                    throw new Error(`Calendar asset recovery unchanged target identity is invalid: ${entry.path}`);
                }
            } else {
                if (typeof entry.backup !== "string") {
                    throw new Error("Calendar asset recovery journal has invalid backup metadata.");
                }
                const backup = assertJournalPath(entry.backup, stateRoot, "backup record");
                if (entry.backup !== path.relative(stateRoot, backup).split(path.sep).join("/") || !isWithin(backupRoot, backup)) {
                    throw new Error("Calendar transaction backup record escapes backup/.");
                }
                await assertConfinedParent(backup, backupRoot, "backup");
                const backupMetadata = await exists(backup);
                if (backupMetadata) {
                    if (!backupMetadata.isFile() || !sameMetadata(await metadataFor(backup), entry.original)) {
                        throw new Error(`Calendar transaction backup identity is invalid: ${entry.path}`);
                    }
                } else if (journal.phase !== "rolling-back" && journal.phase !== "rolled-back") {
                    throw new Error(`Calendar transaction backup is missing: ${entry.path}`);
                } else if (!(await matchesOriginal(target, entry.original))) {
                    throw new Error(`Calendar transaction backup is missing before restoration: ${entry.path}`);
                }
            }
        }
        if (journal.phase === "committed" && entry.applied !== entry.changed) {
            throw new Error("Calendar asset recovery journal has an invalid committed state.");
        }
        if ((journal.phase === "prepared" || journal.phase === "rolled-back") && entry.applied) {
            throw new Error("Calendar asset recovery journal has an invalid applied state.");
        }
    }
    if (changedEntries === 0 || manifestEntries !== 1) throw new Error("Calendar asset recovery journal has an invalid file set.");
    await validateStagedGraph(journal, stateRoot);
}

async function validateStagedGraph(journal, stateRoot) {
    const stageRoot = path.join(stateRoot, "stage");
    await assertNoSymlinkPath(stageRoot, stateRoot, "stage");
    const stagedManifest = assertJournalPath(journal.manifest, stageRoot, "staged manifest");
    await assertNoSymlinkPath(stagedManifest, stageRoot, "staged manifest");
    let manifest;
    try {
        manifest = JSON.parse(await readBytesNoFollow(stagedManifest, "staged manifest"));
    } catch (error) {
        throw new Error("Calendar asset recovery staged graph is invalid.", { cause: error });
    }
    if (typeof manifest?.entry !== "string") {
        throw new Error("Calendar asset recovery staged graph is invalid.");
    }
    const staged = await validateCalendarAssets({
        sourceRoot: stageRoot,
        entry: path.join(stageRoot, manifest.entry),
        manifestFile: stagedManifest,
    });
    if (staged.version !== journal.version) {
        throw new Error("Calendar asset recovery staged graph version is invalid.");
    }
    const expected = new Map([
        ...staged.modules.map((module) => [module.path, "module"]),
        [journal.manifest, "manifest"],
    ]);
    if (expected.size !== journal.entries.length) {
        throw new Error("Calendar asset recovery journal does not describe the staged graph.");
    }
    for (const entry of journal.entries) {
        if (expected.get(entry.path) !== entry.kind) {
            throw new Error("Calendar asset recovery journal does not describe the staged graph.");
        }
    }
}

async function validateCommittedTargets(journal, sourceRoot) {
    for (const entry of journal.entries) {
        const target = assertJournalPath(entry.path, sourceRoot, "target");
        await assertConfinedParent(target, sourceRoot, "target");
        await assertNoSymlinkPath(target, sourceRoot, "target");
        if ((await metadataFor(target)).sha256 !== entry.stagedSha256) {
            throw new Error(`Calendar asset committed target does not match its staged file: ${entry.path}`);
        }
    }
}

async function validateRolledBackTargets(journal, sourceRoot) {
    for (const entry of journal.entries) {
        const target = assertJournalPath(entry.path, sourceRoot, "target");
        await assertConfinedParent(target, sourceRoot, "target");
        await assertNoSymlinkPath(target, sourceRoot, "target");
        if (!(await matchesOriginal(target, entry.original))) {
            throw new Error(`Calendar asset rollback target is not the original file: ${entry.path}`);
        }
    }
}

export async function recoverCalendarAssets({ sourceRoot, entry, manifestFile, stateRoot }) {
    const lexicalRoot = path.resolve(sourceRoot);
    const resolvedRoot = await realpath(path.resolve(sourceRoot));
    const lexicalEntry = path.resolve(entry);
    await assertNoSymlinkPath(lexicalEntry, lexicalRoot, "entry");
    const resolvedEntry = await realpath(path.resolve(entry));
    if (!isWithin(resolvedRoot, resolvedEntry)) throw new Error("Calendar entry resolves outside static/.");
    const lexicalManifest = path.resolve(manifestFile);
    await assertNoSymlinkPath(path.dirname(lexicalManifest), lexicalRoot, "manifest");
    await assertNoSymlinkPath(lexicalManifest, lexicalRoot, "manifest");
    const resolvedManifest = path.join(resolvedRoot, path.relative(lexicalRoot, lexicalManifest));
    const locations = await transactionLocations(resolvedRoot, stateRoot);
    const stateMetadata = await exists(locations.state);
    if (stateMetadata?.isSymbolicLink()) throw new Error("Calendar asset transaction state may not be a symlink.");
    const staleLock = await removeStaleLock(locations.lock);
    if (!stateMetadata) return { recovered: false };
    await acquireLock(locations.lock, {
        pid: process.pid,
        sourceRoot: resolvedRoot,
        stateRoot: locations.state,
        transactionId: randomUUID(),
    });
    try {
        const journal = await readJournal(locations);
        if (!journal) {
            await cleanupState(locations);
            return { recovered: true, phase: "staging" };
        }
        await validateJournal(journal, resolvedRoot, resolvedManifest, locations.state, staleLock);
        if (journal.phase !== "committed" && journal.phase !== "rolled-back") {
            await rollbackJournal(locations, journal);
        }
        if (journal.phase === "committed") {
            await validateCommittedTargets(journal, resolvedRoot);
        } else {
            await validateRolledBackTargets(journal, resolvedRoot);
        }
        const result = await validateCalendarAssets({
            sourceRoot: resolvedRoot,
            entry: resolvedEntry,
            manifestFile: resolvedManifest,
        });
        await cleanupState(locations);
        return { recovered: true, phase: journal.phase, version: result.version };
    } finally {
        await releaseLock(locations.lock);
    }
}
