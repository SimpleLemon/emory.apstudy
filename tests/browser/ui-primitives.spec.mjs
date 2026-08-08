import { expect, test } from "playwright/test";

test("shared form, loader, skeleton, toast, and dialog primitives initialize independently", async ({ page, baseURL }) => {
    await page.route("**/ui-primitives-harness", (route) => route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><head><link rel="stylesheet" href="/static/css/global.css"><link rel="stylesheet" href="/static/css/core/feedback-overlays.css"></head><body><button id="trigger">Delete</button><input id="field"><script src="/static/js/core/ui-primitives.js"></script></body></html>`,
    }));
    await page.goto(`${baseURL}/ui-primitives-harness`, { waitUntil: "networkidle" });

    expect(await page.evaluate(() => Object.keys(window.APStudyUIPrimitives).sort())).toEqual([
        "confirm", "escapeHtml", "form", "loader", "skeleton", "toast", "undo",
    ]);
    expect(await page.evaluate(() => [
        window.APStudyUIPrimitives.escapeHtml(`&<>"'`),
        window.APStudyUIPrimitives.escapeHtml(null),
        window.APStudyUIPrimitives.escapeHtml(undefined),
        window.APStudyUIPrimitives.escapeHtml(0),
        window.APStudyUIPrimitives.escapeHtml(false),
    ])).toEqual(["&amp;&lt;&gt;&quot;&#39;", "", "", "0", "false"]);
    expect(await page.evaluate(() => window.APStudyLoader.html("<Loading>"))).toContain("&lt;Loading&gt;");
    expect(await page.evaluate(() => window.APStudySkeleton.cards({ count: 2 }))).toContain('role="status"');

    await page.evaluate(() => window.APStudyFormField.markInvalid(document.querySelector("#field")));
    await expect(page.locator("#field")).toHaveAttribute("aria-invalid", "true");
    await page.locator("#field").fill("fixed");
    await page.evaluate(() => window.APStudyFormField.bindAutoClear(document.querySelector("#field")));
    await page.locator("#field").fill("valid");
    await expect(page.locator("#field")).not.toHaveAttribute("aria-invalid", "true");

    await page.evaluate(() => window.APStudyToast.success("Saved", { duration: 0 }));
    await expect(page.getByRole("status")).toContainText("Saved");
    await page.getByRole("button", { name: "Dismiss notification" }).click();

    await page.locator("#trigger").focus();
    await page.evaluate(() => {
        window.__confirmation = window.APStudyConfirm.request({ title: "Delete note?", acceptLabel: "Delete" });
    });
    await expect(page.getByRole("dialog", { name: "Delete note?" })).toBeVisible();
    await expect(page.locator("[data-confirm-cancel]")).toBeFocused();
    await page.keyboard.press("Escape");
    expect(await page.evaluate(() => window.__confirmation)).toBe(false);
    await expect(page.locator("#trigger")).toBeFocused();
});

test("toast variants normalize content and expose a consistent accessible structure", async ({ page, baseURL }) => {
    await page.route("**/ui-primitives-harness", (route) => route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><head><link rel="stylesheet" href="/static/css/global.css"><link rel="stylesheet" href="/static/css/core/feedback-overlays.css"></head><body><script src="/static/js/core/ui-primitives.js"></script></body></html>`,
    }));
    await page.goto(`${baseURL}/ui-primitives-harness`, { waitUntil: "networkidle" });

    expect(await page.evaluate(() => window.APStudyToast.show({ title: "  ", message: null }))).toBeNull();
    await expect(page.locator(".apstudy-toast")).toHaveCount(0);

    await page.evaluate(() => window.APStudyToast.success("  Profile saved.  ", { duration: 0 }));
    const compact = page.locator(".apstudy-toast", { hasText: "Profile saved." });
    await expect(compact).toHaveClass(/is-compact/);
    await expect(compact.locator(".apstudy-toast__title")).toHaveText("Profile saved.");
    await expect(compact.locator(".apstudy-toast__message")).toHaveCount(0);
    await expect(compact.locator(".apstudy-toast__icon svg")).toHaveCount(1);
    expect(await compact.evaluate((toast) => Array.from(toast.children, (child) => child.className))).toEqual([
        "apstudy-toast__icon",
        "apstudy-toast__copy",
        "apstudy-toast__close",
    ]);
    await compact.getByRole("button", { name: "Dismiss notification" }).click();

    await page.evaluate(() => window.APStudyToast.show({ title: "Title only", type: "info", duration: 0 }));
    const titleOnly = page.locator(".apstudy-toast", { hasText: "Title only" });
    await expect(titleOnly).toHaveClass(/is-compact/);
    await expect(titleOnly.locator(".apstudy-toast__message")).toHaveCount(0);
    await titleOnly.getByRole("button", { name: "Dismiss notification" }).click();

    await page.evaluate(() => window.APStudyToast.show({
        title: "Couldn’t save profile",
        message: "Check your connection and try again.",
        type: "error",
        action: { label: "Retry", onClick: () => { window.__toastRetried = true; } },
    }));
    const rich = page.getByRole("alert");
    await expect(rich).toHaveAttribute("aria-atomic", "true");
    await expect(rich).toHaveClass(/has-detail/);
    await expect(rich).toHaveClass(/has-action/);
    await expect(rich.locator(".apstudy-toast__title")).toHaveText("Couldn’t save profile");
    await expect(rich.locator(".apstudy-toast__message")).toHaveText("Check your connection and try again.");
    await expect(rich).toHaveCSS("--toast-duration", "10000ms");
    await rich.getByRole("button", { name: "Retry" }).click();
    expect(await page.evaluate(() => window.__toastRetried)).toBe(true);
});

test("toast timing pauses with interaction and reduced motion suppresses progress animation", async ({ page, baseURL }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route("**/ui-primitives-harness", (route) => route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><head><link rel="stylesheet" href="/static/css/global.css"><link rel="stylesheet" href="/static/css/core/feedback-overlays.css"></head><body><script src="/static/js/core/ui-primitives.js"></script></body></html>`,
    }));
    await page.goto(`${baseURL}/ui-primitives-harness`, { waitUntil: "networkidle" });

    await page.evaluate(() => window.APStudyToast.info("Paused toast.", { duration: 240 }));
    const toast = page.locator(".apstudy-toast", { hasText: "Paused toast." });
    await expect(toast.locator(".apstudy-toast__progress")).toHaveCSS("display", "none");
    await toast.hover();
    await page.waitForTimeout(300);
    await expect(toast).not.toHaveClass(/is-leaving/);
    await page.mouse.move(0, 0);
    await expect(toast).toHaveClass(/is-leaving/, { timeout: 500 });
});

test("toast layout remains bounded and usable on a narrow viewport", async ({ page, baseURL }) => {
    await page.setViewportSize({ width: 390, height: 640 });
    await page.route("**/ui-primitives-harness", (route) => route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><head><link rel="stylesheet" href="/static/css/global.css"><link rel="stylesheet" href="/static/css/core/feedback-overlays.css"></head><body><script src="/static/js/core/ui-primitives.js"></script></body></html>`,
    }));
    await page.goto(`${baseURL}/ui-primitives-harness`, { waitUntil: "networkidle" });
    await page.evaluate(() => {
        for (let index = 0; index < 8; index += 1) {
            window.APStudyToast.show({
                title: `Update ${index + 1}`,
                message: "This longer supporting message verifies wrapping and viewport containment.",
                type: index % 2 ? "warning" : "info",
                duration: 0,
                action: { label: "Open", onClick: () => {} },
            });
        }
    });

    const host = page.locator(".apstudy-toast-host");
    const box = await host.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    expect(box.y + box.height).toBeLessThanOrEqual(640);
    await expect(host).toHaveCSS("overflow-y", "auto");
    await expect(host.locator(".apstudy-toast__action").first()).toHaveCSS("min-height", "44px");
    expect(await host.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
    await host.locator(".apstudy-toast").first().hover();
    await page.mouse.wheel(0, 320);
    await expect.poll(() => host.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});
