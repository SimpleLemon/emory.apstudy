/* global process, URL */

const DEFAULT_BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000";

async function stubUnavailableAppwriteApis(page) {
    const endpoint = process.env.APPWRITE_ENDPOINT;
    if (!endpoint) {
        for (const appwriteHost of ["https://nyc.cloud.appwrite.io/**", "https://cloud.appwrite.io/**"]) {
            await page.route(appwriteHost, (route) => route.fulfill({ status: 503, body: "Appwrite unavailable in browser harness" }));
        }
        return;
    }
    try {
        const response = await page.request.get(endpoint.replace(/\/$/, "") + "/health", { timeout: 2_000 });
        if (response.ok()) return;
    } catch {
        // The seam remains authenticated through the real Flask session.
    }
    await page.route("**/v1/**", (route) => route.fulfill({ status: 503, body: "Appwrite unavailable in browser harness" }));
}

export async function authenticate(page, { tier = "free", theme = "system-match", route = "/dashboard", colorScheme = "light" } = {}) {
    await page.emulateMedia({ colorScheme });
    await stubUnavailableAppwriteApis(page);
    const authUrl = new URL("/__test__/auth", DEFAULT_BASE_URL);
    authUrl.searchParams.set("tier", tier);
    authUrl.searchParams.set("theme", theme);
    authUrl.searchParams.set("next", route);
    await page.goto(authUrl.toString(), { waitUntil: "domcontentloaded" });
    return page;
}

export const AUTHENTICATED_TIERS = ["free", "grade_a", "grade_aa", "developer"];
export const AUTHENTICATED_THEMES = ["obsidian-dark", "parchment-light", "system-match", "nest-light", "nest-dark"];
