/**
 * Deterministically stamp the calendar ES-module graph for browser cache busting.
 *
 * This writer is a build/development operation. Do not run it against a live
 * production checkout; deploy its reviewed generated files through the normal
 * release workflow. Regenerate with `npm run build:calendar-assets`.
 */
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
    assertNoCalendarAssetTransaction,
    recoverCalendarAssets as recoverTransaction,
    writeCalendarAssets as writeTransaction,
} from "./calendar-assets-transaction.mjs";
import { validateCalendarAssets as validateGraph } from "./calendar-assets-graph.mjs";

export {
    buildCalendarPlan,
    graphVersion,
    MANIFEST_SCHEMA,
    manifestFor,
    resolveCalendarPaths,
    VERSION_PATTERN,
    versionedSpecifier,
    walkCalendarGraph,
} from "./calendar-assets-graph.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(scriptDirectory, "..");
export const staticRoot = path.join(repoRoot, "static");
export const calendarEntry = path.join(staticRoot, "js/calendar/entry.js");
export const manifestPath = path.join(staticRoot, "js/calendar/manifest.json");
export const transactionRoot = path.join(repoRoot, ".calendar-assets-transaction");

function withDefaults(options = {}) {
    return {
        sourceRoot: options.sourceRoot || staticRoot,
        entry: options.entry || calendarEntry,
        manifestFile: options.manifestFile || manifestPath,
        stateRoot: options.stateRoot || (options.sourceRoot
            ? path.join(options.sourceRoot, ".calendar-assets-transaction")
            : transactionRoot),
        ...options,
    };
}

export async function validateCalendarAssets(options = {}) {
    const resolved = withDefaults(options);
    await assertNoCalendarAssetTransaction(resolved);
    const result = await validateGraph(resolved);
    await assertNoCalendarAssetTransaction(resolved);
    return result;
}

export async function writeCalendarAssets(options = {}) {
    return writeTransaction(withDefaults(options));
}

export async function recoverCalendarAssets(options = {}) {
    return recoverTransaction(withDefaults(options));
}

async function main() {
    const [mode] = process.argv.slice(2);
    if (process.argv.length !== 3 || !["--check", "--write", "--recover"].includes(mode)) {
        throw new Error("Usage: node scripts/calendar-assets.mjs --check|--write|--recover");
    }
    if (mode === "--recover") {
        const result = await recoverCalendarAssets();
        console.log(result.recovered
            ? "Calendar asset transaction recovered and validated."
            : "No calendar asset transaction requires recovery.");
        return;
    }
    const result = mode === "--write" ? await writeCalendarAssets() : await validateCalendarAssets();
    console.log(`Calendar assets ${mode === "--write" ? "written and validated" : "validated"}: ${result.version}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    main().catch((error) => {
        console.error(error.stack || error.message);
        process.exitCode = 1;
    });
}
