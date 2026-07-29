import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const iconFile = path.join(repoRoot, "static/fonts/material-symbols-outlined-v361-icons.txt");

async function sourceFiles(directory) {
    const files = [];
    for (const entry of await readdir(directory, { withFileTypes: true })) {
        if (entry.name === "dist" || entry.name === "node_modules") continue;
        const file = path.join(directory, entry.name);
        if (entry.isDirectory()) files.push(...await sourceFiles(file));
        else if (/\.(?:html|js|mjs)$/.test(entry.name)) files.push(file);
    }
    return files;
}

function collectIconNames(source) {
    const names = new Set();
    const add = (match) => {
        if (match?.[1]) names.add(match[1]);
    };

    for (const match of source.matchAll(/material-symbols-(?:outlined|rounded|sharp)[^>]*>\s*([a-z0-9_]+)\s*</g)) add(match);
    for (const match of source.matchAll(/data-icon=["']([a-z0-9_]+)["']/g)) add(match);
    for (const match of source.matchAll(/\bicon:\s*["']([a-z0-9_]+)["']/g)) add(match);

    for (const line of source.split("\n")) {
        if (!/MaterialIcon/.test(line)) continue;
        const value = line.match(/\bname:\s*(.*)$/)?.[1] || "";
        const literals = [...value.matchAll(/["']([a-z0-9_]+)["']/g)];
        if (/^["']/.test(value.trim())) add(literals[0]);
        else literals.filter((match) => match[1].includes("_")).forEach(add);
    }
    return names;
}

test("every Material Symbols ligature used by the app exists in the shipped subset", async () => {
    const known = new Set((await readFile(iconFile, "utf8")).split(/\s+/).filter(Boolean));
    const files = [
        ...(await sourceFiles(path.join(repoRoot, "templates"))),
        ...(await sourceFiles(path.join(repoRoot, "static/js"))),
    ];
    const unknown = new Map();
    for (const file of files) {
        const names = collectIconNames(await readFile(file, "utf8"));
        for (const name of names) {
            if (!known.has(name)) {
                const locations = unknown.get(name) || [];
                locations.push(path.relative(repoRoot, file));
                unknown.set(name, locations);
            }
        }
    }

    assert.deepEqual([...unknown.keys()].sort(), [], `Unsupported Material Symbols: ${JSON.stringify([...unknown])}`);
});
