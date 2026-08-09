import { expect, test } from "playwright/test";

const layout = {
    version: 4,
    daily_quote_visible: true,
    tiles: ["tile-a", "tile-b", "tile-c"].map((instanceId) => ({
        instance_id: instanceId,
        type: "calendar",
        size: "standard",
        density: "comfortable",
        item_limit: 5,
        view: "week",
        upcoming_days: 7,
    })),
};

test("tile settings preview live, open opposite the tile, and keep move boundaries current", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/static/js/dashboard/utils.js`);
    await page.setContent(`
        <button id="edit">Edit</button>
        <button id="add" hidden>Add</button>
        <button id="cancel" hidden>Cancel</button>
        <section id="toolbar" hidden>
            <strong id="label"></strong>
            <button id="earlier">Earlier</button>
            <button id="later">Later</button>
            <button id="customize">Customize</button>
            <button id="remove">Remove</button>
        </section>
        <div id="quote"></div>
        <div id="tiles" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px"></div>
        <dialog id="drawer"><div id="drawer-body"></div></dialog>
        <dialog id="discard"><button id="keep">Keep</button><button id="discard-confirm">Discard</button></dialog>
        <div id="announcer"></div>
    `);
    await page.addScriptTag({ url: `${baseURL}/static/js/core/ui-primitives.js` });
    await page.addScriptTag({ url: `${baseURL}/static/js/dashboard/utils.js` });
    await page.addScriptTag({ url: `${baseURL}/static/js/dashboard/layout-editor.js` });

    await page.evaluate((initialLayout) => {
        const elements = {
            tiles: document.querySelector("#tiles"),
            quoteSlot: document.querySelector("#quote"),
            editToggle: document.querySelector("#edit"),
            addTile: document.querySelector("#add"),
            cancel: document.querySelector("#cancel"),
            toolbar: document.querySelector("#toolbar"),
            toolbarLabel: document.querySelector("#label"),
            toolbarMoveEarlier: document.querySelector("#earlier"),
            toolbarMoveLater: document.querySelector("#later"),
            toolbarCustomize: document.querySelector("#customize"),
            toolbarRemove: document.querySelector("#remove"),
            drawer: document.querySelector("#drawer"),
            drawerBody: document.querySelector("#drawer-body"),
            discardDialog: document.querySelector("#discard"),
            discardKeep: document.querySelector("#keep"),
            discardConfirm: document.querySelector("#discard-confirm"),
            announcer: document.querySelector("#announcer"),
        };
        const summary = { available_tiles: ["calendar"], tiles: { tasks: { lists: [] } } };
        let editor;
        const render = (nextLayout) => {
            elements.tiles.innerHTML = nextLayout.tiles.map((tile) => `
                <article class="dashboard-tile" data-tile-id="${tile.instance_id}" data-tile-size="${tile.size}" aria-label="${tile.title || "Calendar: Week"} dashboard tile" style="min-height:120px">
                    <div class="dashboard-tile-inner"><h2>${tile.title || "Calendar: Week"}</h2></div>
                </article>
            `).join("");
            editor?.bindTiles();
        };
        editor = window.APStudyDashboardLayoutEditor.create({
            elements,
            getSummary: () => summary,
            render,
            persist: async (nextLayout) => nextLayout,
            showToast: () => {},
        });
        editor.load(initialLayout);
        render(editor.currentLayout());
    }, layout);

    const firstTile = page.locator('[data-tile-id="tile-a"]');
    await expect(firstTile).not.toHaveAttribute("role");
    await expect(firstTile).not.toHaveAttribute("tabindex");
    await expect(firstTile).not.toHaveAttribute("aria-pressed");

    await page.locator("#edit").click();
    await expect(firstTile).toHaveAttribute("role", "button");
    await expect(firstTile).toHaveAttribute("tabindex", "0");
    await expect(firstTile).toHaveAttribute("aria-pressed", "false");
    await page.locator("#cancel").click();
    await expect(page.locator("#edit")).toBeFocused();

    await page.locator("#edit").click();
    await page.locator('[data-tile-id="tile-b"]').click();
    await page.locator("#customize").click();
    await expect(page.locator("#drawer")).toHaveAttribute("data-side", "left");

    await page.getByRole("textbox", { name: "Custom title Optional" }).fill("Live calendar");
    await expect(page.locator('[data-tile-id="tile-b"] h2')).toHaveText("Live calendar");
    await page.getByRole("radio", { name: "Wide" }).check();
    await expect(page.locator('[data-tile-id="tile-b"]')).toHaveAttribute("data-tile-size", "wide");
    await page.locator("#drawer").getByRole("button", { name: "Done" }).click();
    await expect(page.locator("#customize")).toBeFocused();

    await expect(page.locator("#earlier")).toBeEnabled();
    await expect(page.locator("#later")).toBeEnabled();
    await page.locator("#earlier").click();
    await expect(page.locator("#earlier")).toBeDisabled();
    await expect(page.locator("#later")).toBeEnabled();
    await page.locator("#later").click();
    await expect(page.locator("#earlier")).toBeEnabled();
    await expect(page.locator("#later")).toBeEnabled();
    await page.locator("#later").click();
    await expect(page.locator("#earlier")).toBeEnabled();
    await expect(page.locator("#later")).toBeDisabled();

    await page.locator('[data-tile-id="tile-a"]').click();
    await page.locator("#customize").click();
    await expect(page.locator("#drawer")).toHaveAttribute("data-side", "right");

    await page.locator("#drawer").getByRole("button", { name: "Done" }).click();
    await page.locator("#cancel").click();
    await expect(page.locator("#discard")).toBeVisible();
    await page.locator("#keep").click();
    await expect(page.locator("#discard")).toBeHidden();
    await expect(page.locator("#edit")).not.toBeFocused();

    await page.locator("#cancel").click();
    await page.locator("#discard-confirm").click();
    await expect(page.locator("#edit")).toBeFocused();
    await expect(firstTile).not.toHaveAttribute("role");
    await expect(firstTile).not.toHaveAttribute("tabindex");
    await expect(firstTile).not.toHaveAttribute("aria-pressed");
    await expect(page.locator('[data-tile-id="tile-b"] h2')).toHaveText("Calendar: Week");
});
