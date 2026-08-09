# ESLint baseline

This is the starting baseline for the standalone JavaScript static-analysis setup. It records
existing findings so Theme H can wire the check without mixing in production-code cleanup.

## Configuration

- Generated on 2026-07-31 with ESLint 10.8.0.
- Run with `npm run lint`.
- Linted files match `**/*.js`, `**/*.cjs`, and `**/*.mjs`.
- Existing parser settings are preserved: `ecmaVersion: "latest"` and `sourceType: "module"`.
- The configured rules are `no-unused-vars`, `no-undef`, and `no-implicit-globals`, all at
  warning severity for this initial baseline.
- Local-only trees, stylesheets, and committed generated bundles are ignored by the ESLint
  configuration.

## Findings at baseline

| Measure | Count |
|---|---:|
| Files checked | 245 |
| Files with findings | 197 |
| Total findings | 3,192 |
| `no-undef` | 3,066 |
| `no-unused-vars` | 126 |
| `no-implicit-globals` | 0 |
| Errors | 0 |
| Warnings | 3,192 |

The lint command intentionally reports this backlog as warnings and exits successfully. No
findings are disabled or suppressed by this baseline, and no production-code changes are part of
Theme H. Follow-up cleanup commits can reduce the counts incrementally; update this document only
when the baseline is deliberately remeasured.

The zero `no-implicit-globals` count follows the existing shared `sourceType: "module"` setting.
This theme does not reclassify classic scripts or change their parsing model.
