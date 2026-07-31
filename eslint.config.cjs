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
];
