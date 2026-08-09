import { defineConfig } from "vite";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
    base: "./",
    build: {
        outDir: path.resolve(rootDir, "static/js/vendor/dist"),
        emptyOutDir: true,
        target: "es2020",
        sourcemap: false,
        minify: "esbuild",
        rollupOptions: {
            input: path.resolve(rootDir, "static/js/vendor/sortable-global.js"),
            output: {
                format: "iife",
                name: "APStudySortable",
                entryFileNames: "sortable-global.js",
                inlineDynamicImports: true,
            },
        },
    },
});
