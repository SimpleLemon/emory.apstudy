import { createHash } from "node:crypto";
import { readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { parse } from "acorn";

export const MANIFEST_SCHEMA = 2;
export const VERSION_PATTERN = /^[0-9a-f]{64}$/;

function isWithin(root, candidate) {
    const relative = path.relative(root, candidate);
    return relative === "" || (relative && !relative.startsWith("..") && !path.isAbsolute(relative));
}

function splitSpecifier(specifier) {
    const hashIndex = specifier.indexOf("#");
    const withoutFragment = hashIndex === -1 ? specifier : specifier.slice(0, hashIndex);
    const fragment = hashIndex === -1 ? "" : specifier.slice(hashIndex);
    const queryIndex = withoutFragment.indexOf("?");
    return {
        pathname: queryIndex === -1 ? withoutFragment : withoutFragment.slice(0, queryIndex),
        query: queryIndex === -1 ? "" : withoutFragment.slice(queryIndex + 1),
        fragment,
    };
}

function isLocalSpecifier(specifier) {
    const { pathname } = splitSpecifier(specifier);
    return pathname.startsWith(".") || pathname.startsWith("/static/");
}

export function versionedSpecifier(specifier, version) {
    if (!isLocalSpecifier(specifier)) return specifier;
    const { pathname, query, fragment } = splitSpecifier(specifier);
    const params = new URLSearchParams(query);
    params.set("v", version);
    return `${pathname}?${params}${fragment}`;
}

function withoutVersion(specifier) {
    if (!isLocalSpecifier(specifier)) return specifier;
    const { pathname, query, fragment } = splitSpecifier(specifier);
    const params = new URLSearchParams(query);
    params.delete("v");
    const normalizedQuery = params.toString();
    return `${pathname}${normalizedQuery ? `?${normalizedQuery}` : ""}${fragment}`;
}

function walkAst(root, visit) {
    const pending = [root];
    while (pending.length) {
        const node = pending.pop();
        if (!node || typeof node.type !== "string") continue;
        visit(node);
        for (const value of Object.values(node)) {
            if (Array.isArray(value)) pending.push(...value);
            else if (value && typeof value === "object") pending.push(value);
        }
    }
}

function importRecords(source, label) {
    let ast;
    try {
        ast = parse(source, { ecmaVersion: "latest", sourceType: "module" });
    } catch (error) {
        throw new Error(`Calendar module could not be parsed: ${label}`, { cause: error });
    }

    const records = [];
    walkAst(ast, (node) => {
        let literal = null;
        if (
            (node.type === "ImportDeclaration"
                || node.type === "ExportNamedDeclaration"
                || node.type === "ExportAllDeclaration")
            && node.source
        ) {
            literal = node.source;
        } else if (node.type === "ImportExpression") {
            literal = node.source;
            if (literal?.type !== "Literal" || typeof literal.value !== "string") {
                throw new Error(`Calendar module has an unsupported non-literal dynamic import: ${label}`);
            }
        }
        if (!literal || literal.type !== "Literal" || typeof literal.value !== "string") return;
        records.push({
            end: literal.end,
            local: isLocalSpecifier(literal.value),
            specifier: literal.value,
            start: literal.start,
        });
    });
    return records.sort((left, right) => left.start - right.start);
}

function rewriteModuleSource(source, records, transform) {
    let rewritten = source;
    for (const record of [...records].reverse()) {
        if (!record.local) continue;
        const raw = source.slice(record.start, record.end);
        const quote = raw[0];
        rewritten = `${rewritten.slice(0, record.start)}${quote}${transform(record.specifier)}${quote}${rewritten.slice(record.end)}`;
    }
    return rewritten;
}

function canonicalSource(source, records) {
    return rewriteModuleSource(source, records, withoutVersion);
}

function rawSha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

function relativeModulePath(sourcePath, sourceRoot) {
    return path.relative(sourceRoot, sourcePath).split(path.sep).join("/");
}

async function confinedExistingFile(candidate, sourceRoot, label, lexicalRoot = sourceRoot) {
    const lexical = path.resolve(candidate);
    if (!isWithin(lexicalRoot, lexical)) throw new Error(`${label} escapes the static root: ${candidate}`);
    let resolved;
    try {
        resolved = await realpath(lexical);
    } catch (error) {
        if (error.code === "ENOENT") throw new Error(`${label} does not exist: ${candidate}`, { cause: error });
        throw error;
    }
    if (!isWithin(sourceRoot, resolved)) throw new Error(`${label} resolves outside the static root: ${candidate}`);
    const metadata = await stat(resolved);
    if (!metadata.isFile()) throw new Error(`${label} is not a regular file: ${candidate}`);
    return resolved;
}

export async function resolveCalendarPaths({ sourceRoot, entry, manifestFile }) {
    const lexicalRoot = path.resolve(sourceRoot);
    const resolvedRoot = await realpath(lexicalRoot);
    const rootMetadata = await stat(resolvedRoot);
    if (!rootMetadata.isDirectory()) throw new Error(`Calendar static root is not a directory: ${sourceRoot}`);
    const resolvedEntry = await confinedExistingFile(entry, resolvedRoot, "Calendar entry", lexicalRoot);
    const manifestLexical = path.resolve(manifestFile);
    if (!isWithin(lexicalRoot, manifestLexical)) throw new Error(`Calendar manifest escapes the static root: ${manifestFile}`);
    const manifestParent = await realpath(path.dirname(manifestLexical));
    if (!isWithin(resolvedRoot, manifestParent)) throw new Error(`Calendar manifest parent resolves outside the static root: ${manifestFile}`);
    let resolvedManifest = path.join(manifestParent, path.basename(manifestLexical));
    try {
        resolvedManifest = await realpath(manifestLexical);
        if (!isWithin(resolvedRoot, resolvedManifest)) {
            throw new Error(`Calendar manifest resolves outside the static root: ${manifestFile}`);
        }
    } catch (error) {
        if (error.code !== "ENOENT") throw error;
    }
    return { sourceRoot: resolvedRoot, entry: resolvedEntry, manifestFile: resolvedManifest };
}

async function resolveLocalSpecifier(importer, specifier, sourceRoot) {
    const { pathname } = splitSpecifier(specifier);
    const base = pathname.startsWith("/static/")
        ? path.resolve(sourceRoot, pathname.slice("/static/".length))
        : path.resolve(path.dirname(importer), pathname);
    if (!isWithin(sourceRoot, base)) {
        throw new Error(`Calendar local import traverses outside static/: ${specifier} from ${importer}`);
    }
    const candidates = path.extname(base) ? [base] : [base, `${base}.js`, `${base}.mjs`];
    for (const candidate of candidates) {
        try {
            const resolved = await confinedExistingFile(candidate, sourceRoot, "Calendar local import");
            if (!/[.]m?js$/i.test(resolved)) {
                throw new Error(`Calendar local import is not an ES module: ${specifier} from ${importer}`);
            }
            return resolved;
        } catch (error) {
            if (!error.message?.includes("does not exist")) throw error;
        }
    }
    throw new Error(`Calendar local import does not exist: ${specifier} from ${importer}`);
}

export async function walkCalendarGraph(entry, { sourceRoot }) {
    const paths = await resolveCalendarPaths({ sourceRoot, entry, manifestFile: path.join(sourceRoot, "manifest-placeholder.json") });
    const queue = [paths.entry];
    const modules = new Map();
    while (queue.length) {
        const sourcePath = queue.shift();
        if (modules.has(sourcePath)) continue;
        const source = await readFile(sourcePath, "utf8");
        const imports = importRecords(source, sourcePath);
        const localImports = [];
        for (const record of imports) {
            if (!record.local) continue;
            const target = await resolveLocalSpecifier(sourcePath, record.specifier, paths.sourceRoot);
            localImports.push({ ...record, target });
            queue.push(target);
        }
        modules.set(sourcePath, {
            imports,
            localImports,
            path: relativeModulePath(sourcePath, paths.sourceRoot),
            source,
            sourcePath,
            rawHash: rawSha256(Buffer.from(source, "utf8")),
        });
    }
    return [...modules.values()].sort((left, right) => left.path.localeCompare(right.path));
}

export function graphVersion(modules) {
    const hash = createHash("sha256");
    for (const module of modules) {
        hash.update(module.path, "utf8");
        hash.update("\0", "utf8");
        hash.update(canonicalSource(module.source, module.imports), "utf8");
        hash.update("\0", "utf8");
    }
    return hash.digest("hex");
}

function edgeList(modules) {
    const paths = new Map(modules.map((module) => [module.sourcePath, module.path]));
    return modules.flatMap((module) => module.localImports.map((record) => {
        const targetPath = paths.get(record.target);
        if (!targetPath) throw new Error(`Calendar graph edge has an unknown target: ${module.path}`);
        return { from: module.path, specifier: record.specifier, to: targetPath };
    })).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

export function manifestFor(modules, version, entryPath) {
    return {
        schema: MANIFEST_SCHEMA,
        entry: entryPath,
        version,
        modules: modules.map((module) => ({ path: module.path, sha256: module.rawHash })),
        edges: edgeList(modules),
    };
}

function applySourceOverrides(modules, sourceOverrides = new Map()) {
    return modules.map((module) => {
        if (!sourceOverrides.has(module.path)) return module;
        const source = String(sourceOverrides.get(module.path));
        const imports = importRecords(source, module.sourcePath);
        const originalLocal = module.localImports.map((record) => withoutVersion(record.specifier));
        const nextLocal = imports.filter((record) => record.local).map((record) => withoutVersion(record.specifier));
        if (JSON.stringify(originalLocal) !== JSON.stringify(nextLocal)) {
            throw new Error("Calendar source overrides may not change graph topology.");
        }
        let localIndex = 0;
        const localImports = imports.filter((record) => record.local).map((record) => ({
            ...record,
            target: module.localImports[localIndex++].target,
        }));
        return { ...module, imports, localImports, source };
    });
}

export async function buildCalendarPlan({ sourceRoot, entry, manifestFile, sourceOverrides = new Map() }) {
    const paths = await resolveCalendarPaths({ sourceRoot, entry, manifestFile });
    const liveModules = await walkCalendarGraph(paths.entry, { sourceRoot: paths.sourceRoot });
    const workingModules = applySourceOverrides(liveModules, sourceOverrides);
    const version = graphVersion(workingModules);
    const finalModules = workingModules.map((module) => {
        const source = rewriteModuleSource(module.source, module.imports, (specifier) => versionedSpecifier(specifier, version));
        const imports = importRecords(source, module.sourcePath);
        let localIndex = 0;
        const localImports = imports.filter((record) => record.local).map((record) => ({
            ...record,
            target: module.localImports[localIndex++].target,
        }));
        return {
            ...module,
            imports,
            localImports,
            rawHash: rawSha256(Buffer.from(source, "utf8")),
            source,
        };
    });
    const entryPath = relativeModulePath(paths.entry, paths.sourceRoot);
    const manifest = manifestFor(finalModules, version, entryPath);
    const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    return {
        ...paths,
        entryPath,
        finalModules,
        liveModules,
        manifest,
        manifestBytes,
        version,
    };
}

function assertManifestShape(manifest, entryPath) {
    if (
        manifest?.schema !== MANIFEST_SCHEMA
        || manifest.entry !== entryPath
        || !VERSION_PATTERN.test(manifest.version || "")
        || !Array.isArray(manifest.modules)
        || !Array.isArray(manifest.edges)
    ) {
        throw new Error("Calendar asset manifest has an invalid shape.");
    }
}

export async function validateCalendarAssets({ sourceRoot, entry, manifestFile }) {
    const paths = await resolveCalendarPaths({ sourceRoot, entry, manifestFile });
    const modules = await walkCalendarGraph(paths.entry, { sourceRoot: paths.sourceRoot });
    let manifest;
    try {
        manifest = JSON.parse(await readFile(paths.manifestFile, "utf8"));
    } catch (error) {
        throw new Error(`Calendar asset manifest is missing or invalid: ${paths.manifestFile}`, { cause: error });
    }
    const version = graphVersion(modules);
    const entryPath = relativeModulePath(paths.entry, paths.sourceRoot);
    assertManifestShape(manifest, entryPath);
    for (const module of modules) {
        for (const record of module.localImports) {
            if (record.specifier !== versionedSpecifier(record.specifier, version)) {
                throw new Error(`Calendar local import is missing or stale: ${module.path} -> ${record.specifier}`);
            }
        }
    }
    const expected = manifestFor(modules, version, entryPath);
    if (JSON.stringify(manifest) !== JSON.stringify(expected)) {
        throw new Error("Calendar asset manifest or raw module hashes are stale.");
    }
    return { ...paths, entryPath, manifest, modules, version };
}
