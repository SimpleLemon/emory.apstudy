import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
BASE_STYLES = {
    "fonts.css",
    "global.css",
    "index.css",
    "layout.css",
    "tailwind.css",
    "themes.css",
}
BASE_TEMPLATE = "base.html"
BASE_EXTENDS_PATTERN = re.compile(r"{%\s*extends\s+['\"]base\.html['\"]\s*%}")
BLOCK_PATTERN = re.compile(
    r"{%\s*block\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*%}(?P<body>.*?){%\s*endblock\s*%}",
    re.DOTALL,
)


def _is_concrete_template(template):
    source = template.read_text()
    return template.name != BASE_TEMPLATE and (
        "<!DOCTYPE html>" in source or BASE_EXTENDS_PATTERN.search(source)
    )


def _full_templates():
    return [
        template
        for template in sorted(TEMPLATES.glob("*.html"))
        if _is_concrete_template(template)
    ]


def _template_source(template):
    source = template.read_text()
    if not BASE_EXTENDS_PATTERN.search(source):
        return source

    inherited_source = (TEMPLATES / BASE_TEMPLATE).read_text()
    for child_block in BLOCK_PATTERN.finditer(source):
        block_pattern = re.compile(
            rf"{{%\s*block\s+{re.escape(child_block.group('name'))}\s*%}}.*?{{%\s*endblock\s*%}}",
            re.DOTALL,
        )
        inherited_source, replacements = block_pattern.subn(
            child_block.group("body"), inherited_source, count=1
        )
        if replacements != 1:
            raise AssertionError(
                f"{template.name} overrides a block missing from {BASE_TEMPLATE}: "
                f"{child_block.group('name')}"
            )
    return inherited_source


class TemplateAssetOrderTests(unittest.TestCase):
    def test_pages_do_not_load_the_unused_browser_appwrite_sdk(self):
        for template in _full_templates():
            with self.subTest(template=template.name):
                source = _template_source(template)
                self.assertNotIn("appwrite@25.0.0", source)
                self.assertNotIn("js/core/appwrite.js", source)
                self.assertNotIn("_appwrite_meta.html", source)

    def test_theme_and_styles_follow_the_shared_cascade(self):
        for template in _full_templates():
            with self.subTest(template=template.name):
                source = _template_source(template)
                head = source.split("</head>", 1)[0]
                theme_init = head.index("js/core/theme-init.js")
                themes = head.index("css/themes.css")
                global_styles = head.index("css/global.css")
                tailwind = head.find("css/tailwind.css")

                self.assertLess(theme_init, themes)
                self.assertLess(themes, global_styles)
                if tailwind >= 0:
                    self.assertLess(global_styles, tailwind)
                feature_anchor = tailwind if tailwind >= 0 else global_styles

                feature_styles = re.findall(
                    r"filename=['\"](?:css|js/notes/dist)/([^'\"]+\.css)",
                    head,
                )
                for stylesheet in feature_styles:
                    if stylesheet in BASE_STYLES:
                        continue
                    self.assertLess(
                        feature_anchor,
                        head.index(stylesheet),
                        f"{stylesheet} must load after shared styles",
                    )

    def test_remote_font_connections_are_warmed_before_use(self):
        for template in _full_templates():
            with self.subTest(template=template.name):
                head = _template_source(template).split("</head>", 1)[0]
                if "https://fonts.googleapis.com/css" not in head:
                    continue
                preconnect = 'rel="preconnect" href="https://fonts.googleapis.com"'
                self.assertIn(preconnect, head)
                self.assertLess(head.index(preconnect), head.index("https://fonts.googleapis.com/css"))

    def test_head_scripts_do_not_block_rendering_dependencies(self):
        allowed_synchronous = {"theme-init.js", "landing-theme-init.js", "sidebar-init.js"}
        for template in _full_templates():
            with self.subTest(template=template.name):
                head = _template_source(template).split("</head>", 1)[0]
                for script in re.findall(r"<script\b[^>]*\bsrc=[^>]+>", head):
                    if any(asset in script for asset in allowed_synchronous):
                        continue
                    self.assertTrue(
                        " defer" in script or 'type="module"' in script,
                        f"render-blocking script: {script}",
                    )

                if "appwrite@25.0.0" in head:
                    sdk = head.index("appwrite@25.0.0")
                    appwrite = head.index("js/core/appwrite.js")
                    self.assertLess(head.index("js/core/theme-init.js"), sdk)
                    self.assertLess(sdk, appwrite)

    def test_shared_shell_bootstraps_before_global_runtime(self):
        for template in _full_templates():
            source = _template_source(template)
            if '<global class="thenav"' not in source:
                continue
            with self.subTest(template=template.name):
                head = source.split("</head>", 1)[0]
                self.assertLess(
                    head.index("js/core/global-chrome.js"),
                    head.index("js/core/global.js"),
                )


if __name__ == "__main__":
    unittest.main()
