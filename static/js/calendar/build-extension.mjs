import { createHash } from "node:crypto";
import os from "node:os";
import { mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "acorn";
import { build } from "vite";

const calendarDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(calendarDirectory, "../../..");
const sourceRoot = path.join(repoRoot, "static/js");
const extensionEntry = path.join(calendarDirectory, "extension-entry.js");
const defaultOutputDirectory = path.join(repoRoot, "static/dist/calendar-extension");
const tempOutputPrefix = path.join(os.tmpdir(), "apstudy-calendar-extension-build-");
const entryFilename = "calendar-extension.v1.js";
const stylesheetFilename = "calendar-extension.v1.css";
const expectedOutputFiles = new Set([entryFilename, stylesheetFilename]);
const allowedPublishedFiles = new Set([...expectedOutputFiles, "manifest.json"]);

const remoteSpecifierPattern = /^(?:[a-z][a-z\d+.-]*:|\/\/)/i;
const javascriptForbiddenPatterns = [
    { label: "javascript URL", pattern: /\bjavascript\s*:/i },
];
const cssImportPattern = /@import\s+(?:url\(\s*)?(["']?)([^"'\s)]+)\1\s*\)?/gi;
const cssUrlPattern = /url\(\s*(["']?)([^"')]+)\1\s*\)/gi;

function validateOutputDirectory(outputDirectory) {
    const resolved = path.resolve(outputDirectory);
    if (resolved !== defaultOutputDirectory && !resolved.startsWith(tempOutputPrefix)) {
        throw new Error("Calendar extension builds may only write the stable artifact directory or an approved temp build directory.");
    }
    return resolved;
}

function isRemoteSpecifier(specifier) {
    return remoteSpecifierPattern.test(String(specifier || "").trim());
}

function sourcePathIsAllowed(sourcePath) {
    const resolved = path.resolve(sourcePath);
    return resolved === sourceRoot || resolved.startsWith(`${sourceRoot}${path.sep}`);
}

function walkJavaScriptAst(root, visit) {
    const pending = [root];
    while (pending.length) {
        const node = pending.pop();
        if (!node || typeof node.type !== "string") continue;
        visit(node);
        for (const value of Object.values(node)) {
            if (Array.isArray(value)) {
                for (const child of value) pending.push(child);
            } else if (value && typeof value === "object") {
                pending.push(value);
            }
        }
    }
}

function directCalleeName(node) {
    let callee = node?.callee;
    while (callee?.type === "ChainExpression") callee = callee.expression;
    return callee?.type === "Identifier" ? callee.name : null;
}

function assertJavaScriptPolicy(source, label) {
    let ast;
    try {
        ast = parse(source, {
            allowHashBang: true,
            ecmaVersion: "latest",
            sourceType: "module",
        });
    } catch (error) {
        throw new Error(`Calendar extension policy rejected ${label}: JavaScript could not be parsed safely.`, {
            cause: error,
        });
    }
    walkJavaScriptAst(ast, (node) => {
        if (node.type !== "CallExpression" && node.type !== "NewExpression") return;
        const calleeName = directCalleeName(node);
        if (calleeName === "eval") {
            throw new Error(`Calendar extension policy rejected ${label}: forbidden eval call.`);
        }
        if (calleeName === "Function") {
            throw new Error(`Calendar extension policy rejected ${label}: forbidden Function constructor call.`);
        }
    });
    for (const { label: patternLabel, pattern } of javascriptForbiddenPatterns) {
        if (pattern.test(source)) {
            throw new Error(`Calendar extension policy rejected ${label}: forbidden ${patternLabel} pattern.`);
        }
    }
}

function assertCssUrlPolicy(value, label) {
    const url = String(value || "").trim();
    if (!url || url.startsWith("#")) return;
    if (isRemoteSpecifier(url) || /^(?:data|javascript|vbscript|file|blob):/i.test(url)) {
        throw new Error(`Calendar extension policy rejected ${label}: unsafe CSS URL.`);
    }
}

function importSpecifiers(source, label) {
    const specifiers = [];
    const staticPatterns = [
        /\bimport\s+(?:[\s\S]*?\sfrom\s*)?["']([^"']+)["']/g,
        /\bexport\s+(?:[\s\S]*?\sfrom\s*)["']([^"']+)["']/g,
    ];
    for (const pattern of staticPatterns) {
        for (const match of source.matchAll(pattern)) specifiers.push(match[1]);
    }

    const dynamicPattern = /\bimport\s*\(([^)]*)\)/g;
    for (const match of source.matchAll(dynamicPattern)) {
        const expression = match[1].trim();
        const literal = /^(["'])([^"']+)\1$/.exec(expression);
        if (!literal) {
            throw new Error(`Calendar extension policy rejected ${label}: dynamic imports must be absent or literal local imports.`);
        }
        specifiers.push(literal[2]);
    }
    return specifiers;
}

async function resolveLocalSource(importer, specifier) {
    if (isRemoteSpecifier(specifier)) {
        throw new Error(`Calendar extension policy rejected ${importer}: remote executable import ${specifier}.`);
    }
    if (!specifier.startsWith(".") && !specifier.startsWith("/")) {
        throw new Error(`Calendar extension policy rejected ${importer}: external executable import ${specifier}.`);
    }
    const candidate = path.resolve(path.dirname(importer), specifier);
    if (!sourcePathIsAllowed(candidate)) {
        throw new Error(`Calendar extension policy rejected ${importer}: executable import escapes the local source graph.`);
    }
    const candidates = path.extname(candidate)
        ? [candidate]
        : [candidate, `${candidate}.js`, `${candidate}.mjs`, `${candidate}.css`, path.join(candidate, "index.js")];
    for (const sourcePath of candidates) {
        try {
            await readFile(sourcePath);
            return sourcePath;
        } catch {
            // Continue through the small, explicit source extension set.
        }
    }
    throw new Error(`Calendar extension policy could not resolve local import ${specifier} from ${importer}.`);
}

async function inspectCssSource(sourcePath, source, queue) {
    for (const match of source.matchAll(cssImportPattern)) {
        const specifier = match[2];
        if (isRemoteSpecifier(specifier)) {
            throw new Error(`Calendar extension policy rejected ${sourcePath}: remote CSS @import ${specifier}.`);
        }
        queue.push(await resolveLocalSource(sourcePath, specifier));
    }
    for (const match of source.matchAll(cssUrlPattern)) assertCssUrlPolicy(match[2], sourcePath);
}

export async function validateCalendarExtensionSourceGraph(entry = extensionEntry) {
    const queue = [path.resolve(entry)];
    const visited = new Set();
    while (queue.length) {
        const sourcePath = queue.shift();
        if (visited.has(sourcePath)) continue;
        visited.add(sourcePath);
        const source = await readFile(sourcePath, "utf8");
        const extension = path.extname(sourcePath).toLowerCase();
        if (extension === ".js" || extension === ".mjs") {
            assertJavaScriptPolicy(source, sourcePath);
            for (const specifier of importSpecifiers(source, sourcePath)) {
                queue.push(await resolveLocalSource(sourcePath, specifier));
            }
        } else if (extension === ".css") {
            await inspectCssSource(sourcePath, source, queue);
        }
    }
    return [...visited].sort();
}

export async function validateCalendarExtensionOutput(outputDirectory) {
    const entries = await readdir(outputDirectory, { withFileTypes: true });
    const filenames = entries.filter((entry) => entry.isFile()).map((entry) => entry.name);
    if (entries.some((entry) => !entry.isFile()) || filenames.some((filename) => !allowedPublishedFiles.has(filename))
        || ![...expectedOutputFiles].every((filename) => filenames.includes(filename))) {
        throw new Error("Calendar extension policy rejected built output: unexpected or missing artifact files.");
    }
    const javascript = await readFile(path.join(outputDirectory, entryFilename), "utf8");
    const stylesheet = await readFile(path.join(outputDirectory, stylesheetFilename), "utf8");
    assertJavaScriptPolicy(javascript, path.join(outputDirectory, entryFilename));
    if (importSpecifiers(javascript, path.join(outputDirectory, entryFilename)).length) {
        throw new Error("Calendar extension policy rejected built output: executable imports remain in the bundle.");
    }
    if (/@import\b/i.test(stylesheet)) {
        throw new Error("Calendar extension policy rejected built output: CSS @import is not allowed.");
    }
    for (const match of stylesheet.matchAll(cssUrlPattern)) assertCssUrlPolicy(match[2], stylesheetFilename);
    return { javascript, stylesheet };
}

async function sha256(outputDirectory, filename) {
    const bytes = await readFile(path.join(outputDirectory, filename));
    return createHash("sha256").update(bytes).digest("hex");
}

async function publishBuild(stagingDirectory, outputDirectory) {
    const backupDirectory = `${outputDirectory}.previous-${process.pid}-${Date.now()}`;
    let previousOutputMoved = false;
    try {
        try {
            await rename(outputDirectory, backupDirectory);
            previousOutputMoved = true;
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
        }
        await rename(stagingDirectory, outputDirectory);
        if (previousOutputMoved) await rm(backupDirectory, { recursive: true, force: true });
    } catch (error) {
        if (previousOutputMoved) {
            try {
                await rename(backupDirectory, outputDirectory);
            } catch {
                // Preserve the original publication error; the backup remains recoverable.
            }
        }
        throw error;
    }
}

export async function buildCalendarExtension(outputDirectory = defaultOutputDirectory, { entry = extensionEntry } = {}) {
    outputDirectory = validateOutputDirectory(outputDirectory);
    const sourceEntry = path.resolve(entry);
    await validateCalendarExtensionSourceGraph(sourceEntry);

    const stagingDirectory = await mkdtemp(path.join(path.dirname(outputDirectory), `.${path.basename(outputDirectory)}.staging-`));
    try {
        await build({
            root: repoRoot,
            configFile: false,
            logLevel: "error",
            build: {
                outDir: stagingDirectory,
                emptyOutDir: false,
                cssCodeSplit: false,
                minify: "esbuild",
                sourcemap: false,
                reportCompressedSize: false,
                rollupOptions: {
                    input: sourceEntry,
                    output: {
                        format: "iife",
                        name: "APStudyCalendarExtensionBundle",
                        entryFileNames: entryFilename,
                        assetFileNames: (assetInfo) => assetInfo.name?.endsWith(".css")
                            ? stylesheetFilename
                            : "calendar-extension-asset[extname]",
                    },
                },
            },
        });
        await validateCalendarExtensionOutput(stagingDirectory);
        const manifest = {
            contract_version: 1,
            entry: entryFilename,
            stylesheet: stylesheetFilename,
            files: [
                { filename: entryFilename, sha256: await sha256(stagingDirectory, entryFilename) },
                { filename: stylesheetFilename, sha256: await sha256(stagingDirectory, stylesheetFilename) },
            ],
        };
        await writeFile(
            path.join(stagingDirectory, "manifest.json"),
            `${JSON.stringify(manifest, null, 2)}\n`,
            "utf8",
        );
        await publishBuild(stagingDirectory, outputDirectory);
        return { manifest, outputDirectory };
    } catch (error) {
        await rm(stagingDirectory, { recursive: true, force: true });
        throw error;
    }
}

const invokedScript = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedScript === path.join(calendarDirectory, "build-extension.mjs")) {
    const result = await buildCalendarExtension();
    console.log(JSON.stringify(result.manifest));
}
