import { expect, test } from "playwright/test";
import {
    authenticate,
    AUTHENTICATED_THEMES,
    AUTHENTICATED_TIERS,
} from "./authenticated.fixture.mjs";

test.describe.configure({ mode: "serial" });

const routes = ["/dashboard", "/chat", "/notes"];

for (const tier of AUTHENTICATED_TIERS) {
    for (const theme of AUTHENTICATED_THEMES) {
        for (const route of routes) {
            const colorSchemes = theme === "system-match" ? ["light", "dark"] : ["light"];
            for (const colorScheme of colorSchemes) {
                test(`${tier} ${theme} ${colorScheme} ${route} stays authenticated and error-free`, async ({ page }) => {
                    const pageErrors = [];
                    const consoleErrors = [];
                    page.on("pageerror", (error) => pageErrors.push(error.message));
                    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });

                    const target = route === "/notes" ? `/notes/auth-test-note-${tier}-${theme}` : route;
                    await authenticate(page, { tier, theme, route: target, colorScheme });

                    expect(new URL(page.url()).pathname).toBe(target);
                    expect(new URL(page.url()).pathname).not.toMatch(/login|onboarding/);
                    if (route === "/notes") {
                        expect(await page.evaluate(() => window.APSTUDY_NOTE_CONTEXT?.authenticated)).toBe(true);
                        expect(await page.evaluate(() => window.APSTUDY_NOTE_CONTEXT?.pageState)).toBe("ready");
                    } else {
                        await expect(page.locator("global[data-user-tier]").first()).toHaveAttribute("data-user-tier", tier);
                    }
                    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
                    const effectiveDark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
                    expect(effectiveDark).toBe(theme === "obsidian-dark" || theme === "nest-dark" || (theme === "system-match" && colorScheme === "dark"));

                    if (route === "/notes") {
                        await expect(page.locator("#note-title-input")).toBeVisible();
                    }

                    expect(pageErrors).toEqual([]);
                    expect(consoleErrors).toEqual([]);
                });
            }
        }
    }
}
