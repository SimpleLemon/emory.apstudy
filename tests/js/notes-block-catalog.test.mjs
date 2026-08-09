import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function importBrowserModule(relativePath) {
    const source = await readFile(path.join(repoRoot, relativePath), "utf8");
    const url = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
    return import(url);
}

test("block catalog filters turn-into and atom blocks", async () => {
    const { filterBlockCatalog, catalogItemByKey } = await importBrowserModule("static/js/notes/editor/block-catalog.js");

    const all = filterBlockCatalog("");
    const turnInto = filterBlockCatalog("", { includeAtoms: false, turnIntoOnly: true });

    assert.ok(all.some((item) => item.key === "divider"));
    assert.ok(all.some((item) => item.key === "bookmark"));
    assert.ok(turnInto.every((item) => item.turnInto));
    assert.ok(!turnInto.some((item) => item.atom));
    assert.ok(!turnInto.some((item) => item.key === "table"));
    assert.equal(filterBlockCatalog("checklist").length, 1);
    assert.equal(catalogItemByKey("callout")?.type, "callout");
});

test("block catalog exposes inline images and builds URL block payloads", async () => {
    const { blockPayloadForCatalogItem, catalogItemByKey } = await importBrowserModule("static/js/notes/editor/block-catalog.js");

    const image = catalogItemByKey("image");
    assert.equal(image.type, "inlineImage");
    assert.equal(image.inline, true);

    const bookmark = blockPayloadForCatalogItem(catalogItemByKey("bookmark"), {
        url: "https://example.com",
        title: "Example",
        description: "Desc",
        image_url: "https://example.com/og.png",
        site_name: "Example",
        content_type: "text/html",
    });
    assert.equal(bookmark.type, "bookmark");
    assert.equal(bookmark.props.title, "Example");
    assert.equal(bookmark.props.imageUrl, "https://example.com/og.png");

    const table = blockPayloadForCatalogItem(catalogItemByKey("table"));
    assert.equal(table.type, "table");
    assert.equal(table.content.type, "tableContent");
    assert.deepEqual(table.content.rows.map((row) => row.cells), [
        ["", "", ""],
        ["", "", ""],
        ["", "", ""],
    ]);
});

test("notes image block renderer keeps image props and lazy-loads previews", async () => {
    const source = await readFile(path.join(repoRoot, "static/js/notes/toolbar.js"), "utf8");

    assert.match(source, /imageBlockConfig/);
    assert.match(source, /const \{ url, caption, name, showPreview, previewWidth, textAlignment \} = props\.block\.props/);
    assert.match(source, /loading: 'lazy'/);
    assert.match(source, /decoding: 'async'/);
    assert.match(source, /fetchPriority: 'low'/);
    assert.match(source, /rootMargin: '900px 0px'/);
    assert.match(source, /parse: imageParse/);
    assert.match(source, /inlineImageSpec/);
});

test("notes editor handles image clipboard uploads and selected-image alignment", async () => {
    const editor = [
        await readFile(path.join(repoRoot, "static/js/notes/editor.js"), "utf8"),
        await readFile(path.join(repoRoot, "static/js/notes/editor/paste.js"), "utf8"),
    ].join("\n");
    const images = await readFile(path.join(repoRoot, "static/js/notes/editor/images.js"), "utf8");
    const runtime = await readFile(path.join(repoRoot, "static/js/notes/editor/image-runtime.js"), "utf8");

    assert.match(editor, /clipboardImageFiles\(clipboardData\)/);
    assert.match(runtime, /insertInlineContent/);
    assert.match(editor, /layout: 'break', alignment/);
    assert.match(runtime, /notes-image-upload-progress/);
    assert.match(images, /type: 'inlineImage'/);
    assert.match(images, /min: 48, max: 900/);
    assert.match(images, /notes-image-retry/);
});

test("heading collapse hides blocks until same-or-higher heading", async () => {
    const { hiddenBlocksForCollapsedHeadings } = await importBrowserModule("static/js/notes/editor/heading-collapse.js");

    const blocks = [
        { id: "h1", type: "heading", props: { level: 1, isCollapsed: true } },
        { id: "p1", type: "paragraph" },
        { id: "h2", type: "heading", props: { level: 2, isCollapsed: true } },
        { id: "p2", type: "paragraph" },
        { id: "h1b", type: "heading", props: { level: 1, isCollapsed: false } },
        { id: "p3", type: "paragraph" },
    ];

    const { hidden, counts } = hiddenBlocksForCollapsedHeadings(blocks);

    assert.equal(hidden.get("p1"), "h1");
    assert.equal(hidden.get("p2"), "h2");
    assert.equal(hidden.has("p3"), false);
    assert.equal(counts.get("h1"), 1);
    assert.equal(counts.get("h2"), 1);
});
