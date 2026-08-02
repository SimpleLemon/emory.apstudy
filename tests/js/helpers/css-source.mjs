import { readFile } from "node:fs/promises";
import path from "node:path";

const CSS_IMPORT_PATTERN = /@import\s+(?:url\(\s*)?["']([^"']+)["']\s*\)?\s*;/g;

async function expandCssSource(repoRoot, relativePath, seen) {
    const normalizedPath = path.normalize(relativePath);
    if (seen.has(normalizedPath)) return "";
    seen.add(normalizedPath);

    const source = await readFile(path.join(repoRoot, normalizedPath), "utf8");
    const importedSources = [];
    for (const match of source.matchAll(CSS_IMPORT_PATTERN)) {
        const importPath = match[1];
        if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(importPath)) continue;
        const importedPath = path.normalize(path.join(path.dirname(normalizedPath), importPath));
        importedSources.push(await expandCssSource(repoRoot, importedPath, seen));
    }

    return [source, ...importedSources].filter(Boolean).join("\n");
}

export function readCssSource(repoRoot, relativePath) {
    return expandCssSource(repoRoot, relativePath, new Set());
}
