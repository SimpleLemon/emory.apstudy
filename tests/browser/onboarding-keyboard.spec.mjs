import { expect, test } from "playwright/test";

test("onboarding theme radios support keyboard-only selection and persistence callbacks", async ({ page, baseURL }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.route("**/onboarding-theme-harness", (route) => route.fulfill({
    contentType: "text/html",
    body: `<!doctype html><html><head>
      <link rel="stylesheet" href="/static/css/global.css">
      <link rel="stylesheet" href="/static/css/onboarding.css">
    </head><body><fieldset id="themes"><legend>Choose a theme</legend>
      <label class="theme-card"><input type="radio" name="theme" value="parchment-light" data-theme-input checked><span>Light</span> <span class="theme-check">Selected</span></label>
      <label class="theme-card"><input type="radio" name="theme" value="nest-dark" data-theme-input><span>Dark</span> <span class="theme-check hidden">Selected</span></label>
      <label class="theme-card"><input type="radio" name="theme" value="obsidian-dark" data-theme-input><span>Obsidian</span> <span class="theme-check hidden">Selected</span></label>
    </fieldset></body></html>`,
  }));
  await page.goto(`${baseURL}/onboarding-theme-harness`, { waitUntil: "networkidle" });
  await page.evaluate(async () => {
    const { createThemeSelector } = await import("/static/js/onboarding/theme-selector.js");
    window.__selectedThemes = [];
    window.__themeSelector = createThemeSelector(document.querySelector("#themes"), {
      initialTheme: "parchment-light",
      onSelect: (theme) => window.__selectedThemes.push(theme),
    });
  });

  const light = page.getByRole("radio", { name: "Light Selected" });
  const dark = page.getByRole("radio", { name: "Dark Selected" });
  const obsidian = page.getByRole("radio", { name: "Obsidian Selected" });
  await light.focus();
  await page.keyboard.press("ArrowRight");
  await expect(dark).toBeFocused();
  await expect(dark).toBeChecked();
  await page.keyboard.press("End");
  await expect(obsidian).toBeFocused();
  await expect(obsidian).toBeChecked();
  await page.keyboard.press("Home");
  await expect(light).toBeFocused();
  await page.keyboard.press("Space");
  await expect(light).toBeChecked();
  expect(await page.evaluate(() => window.__themeSelector.value())).toBe("parchment-light");
  expect(await page.evaluate(() => window.__selectedThemes)).toEqual(["nest-dark", "obsidian-dark", "parchment-light"]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
