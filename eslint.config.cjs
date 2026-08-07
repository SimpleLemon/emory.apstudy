module.exports = [
    {
        ignores: [
            ".next/**",
            ".venv/**",
            "node_modules/**",
            "Fall_2026/**",
            "Spring_2026/**",
            "data/**",
            ".desloppify/**",
            ".agents/**",
            "docs/**",
            "static/css/**",
            "static/js/notes/dist/**",
            "static/js/tasks/dist/**",
        ],
    },
    {
        files: ["**/*.js", "**/*.cjs", "**/*.mjs"],
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "module",
        },
        rules: {
            "no-unused-vars": "warn",
            "no-undef": "warn",
            "no-implicit-globals": "warn",
        },
    },
    // Measured 2026-08-07: 3,283 existing warnings (3,160 no-undef and
    // 123 no-unused-vars). Keep that backlog visible while enforcing the
    // clean shared browser-shell subset below.
    {
        files: ["eslint.config.cjs", "static/js/core/**/*.js"],
        rules: {
            "no-unused-vars": "error",
        },
    },
    {
        files: [
            "static/js/core/breadcrumb.js",
            "static/js/core/command-palette.js",
            "static/js/core/console-discord.js",
            "static/js/core/cookie-consent.js",
            "static/js/core/global-chrome.js",
            "static/js/core/global.js",
            "static/js/core/navbar.js",
            "static/js/core/notifications.js",
            "static/js/core/sidebar.js",
        ],
        // These nine files contain the 16 pre-existing core no-unused-vars
        // warnings and remain explicitly warning-baselined.
        rules: {
            "no-unused-vars": "warn",
        },
    },
];
