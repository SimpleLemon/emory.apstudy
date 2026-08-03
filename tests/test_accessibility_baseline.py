import re
import os
import sqlite3
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def _relative_luminance(hex_color):
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first, second):
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _mix_hex(foreground, background, foreground_fraction):
    foreground_channels = [int(foreground[index:index + 2], 16) for index in (1, 3, 5)]
    background_channels = [int(background[index:index + 2], 16) for index in (1, 3, 5)]
    channels = [
        round(foreground_channel * foreground_fraction + background_channel * (1 - foreground_fraction))
        for foreground_channel, background_channel in zip(foreground_channels, background_channels)
    ]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


class _TemplateParser(HTMLParser):
    VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parent": self.stack[-1] if self.stack else None, "text": ""}
        self.nodes.append(node)
        if tag not in self.VOID_ELEMENTS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"] += data


class AccessibilityBaselineTests(unittest.TestCase):
    def test_full_templates_load_self_hosted_shared_fonts(self):
        full_templates = [
            template for template in TEMPLATES.glob("*.html")
            if "<!DOCTYPE html>" in template.read_text()
        ]
        self.assertTrue(full_templates)
        for template in full_templates:
            source = template.read_text()
            self.assertIn('{% include "_font_assets.html" %}', source, template.name)
            self.assertNotIn("family=Inter", source, template.name)
            self.assertNotIn("family=Space+Grotesk", source, template.name)

        font_css = (ROOT / "static/css/fonts.css").read_text()
        for family in ("Inter", "Space Grotesk", "IBM Plex Mono"):
            self.assertIn(f'font-family: "{family}"', font_css)
        for asset in (
            "inter-400.woff2", "inter-500.woff2", "inter-600.woff2", "inter-700.woff2",
            "space-grotesk-latin.woff2", "ibm-plex-mono-400.woff2", "ibm-plex-mono-500.woff2",
        ):
            self.assertTrue((ROOT / "static/fonts" / asset).is_file(), asset)

    def test_first_party_css_uses_visual_system_contract(self):
        global_css = (ROOT / "static/css/global-foundation.css").read_text()
        for token in (
            "--radius-control: 6px", "--radius-card: 12px", "--radius-panel: 18px",
            "--radius-dialog: 24px", "--radius-pill: 999px", "--radius-avatar: 50%",
            "--text-meta: 12px", "--text-compact: 14px", "--text-body: 16px",
            "--content-reading: 760px", "--content-standard: 1120px", "--content-wide: 1280px",
        ):
            self.assertIn(token, global_css)
        for primitive in (".ui-section", ".ui-card", ".ui-panel", ".ui-status", ".ui-meta"):
            self.assertIn(primitive, global_css)

        excluded = {"tailwind.css", "themes.css", "fonts.css"}
        literal_radii = []
        undersized_text = []
        for stylesheet in (ROOT / "static/css").glob("*.css"):
            if stylesheet.name in excluded:
                continue
            source = stylesheet.read_text()
            literal_radii.extend(
                f"{stylesheet.name}: {match.group(0)}"
                for match in re.finditer(r"border-radius:\s*[^;]*(?:px|rem)", source)
            )
            for match in re.finditer(r"font-size:\s*([0-9.]+)(px|rem)", source):
                value = float(match.group(1)) * (16 if match.group(2) == "rem" else 1)
                if value < 12:
                    undersized_text.append(f"{stylesheet.name}: {match.group(0)}")
        self.assertEqual([], literal_radii)
        self.assertEqual([], undersized_text)

    def test_landing_has_distinct_ctas_and_accessible_preview_contract(self):
        template = (TEMPLATES / "landing.html").read_text()
        theme_init = (ROOT / "static/js/landing-theme-init.js").read_text()
        for label in (
            "Start free", "Open Nest", "Go to dashboard",
            "View dashboard", "Explore calendar", "Organize tasks", "Open workspace",
            "Continue in Nest", "See Nest in action",
        ):
            self.assertIn(label, template)
        self.assertEqual(4, len(re.findall(r'class="product-mockup landing-app-demo [^"]+" aria-hidden="true"', template)))
        self.assertIn('data-landing-rotator', template)
        self.assertIn('data-landing-marquee', template)
        self.assertIn('data-landing-faq', template)
        self.assertIn("js/landing-theme-init.js", template)
        self.assertIn("colorScheme.matches ? 'nest-dark' : 'parchment-light'", theme_init)
        self.assertEqual(10, template.count('class="university-mark '))
        self.assertNotIn('not endorsed or sponsored by these institutions', template)
        self.assertNotIn('class="course-metadata"', template)
        self.assertNotIn('class="workflow-sequence"', template)
        self.assertNotIn('class="capability-grid"', template)

    def test_landing_light_and_dark_text_tokens_meet_contrast(self):
        source = (ROOT / "static/css/landing.css").read_text()
        root_block = re.search(r":root\s*\{([^}]+)\}", source, re.DOTALL).group(1)
        dark_block = re.search(r'html\[data-theme="nest-dark"\]\s*\{([^}]+)\}', source, re.DOTALL).group(1)
        light = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", root_block))
        dark = {**light, **dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", dark_block))}

        for theme_name, tokens in (("light", light), ("dark", dark)):
            for background, foreground in (
                ("--landing-page-bg", "--landing-ink"),
                ("--landing-page-bg", "--landing-muted"),
                ("--landing-surface", "--landing-ink"),
            ):
                ratio = _contrast_ratio(tokens[background], tokens[foreground])
                self.assertGreaterEqual(ratio, 4.5, f"landing {theme_name}: {foreground} on {background} = {ratio:.2f}:1")

        self.assertGreaterEqual(_contrast_ratio(light["--landing-gold-bright"], light["--landing-navy-deep"]), 4.5)

    def test_login_uses_theme_surfaces_and_text_tokens(self):
        source = (ROOT / "static/css/login.css").read_text()
        for declaration in (
            "background: var(--color-background)",
            "color: var(--color-on-surface)",
            "background: var(--login-card-bg)",
            "color: var(--color-on-surface-variant)",
            "background: var(--color-error-container)",
            "color: var(--color-on-error-container)",
        ):
            self.assertIn(declaration, source)

        self.assertNotRegex(source, r"html,\s*body\s*\{[^}]*background:\s*#[0-9a-fA-F]{3,8}")
        self.assertNotRegex(source, r"\.login-card\s*\{[^}]*background:\s*(?:#|rgba?\()")

    def test_semantic_theme_pairs_meet_normal_text_contrast(self):
        source = (ROOT / "static/css/themes.css").read_text()
        root_values = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", source.split('[data-theme="obsidian-dark"]', 1)[0]))
        theme_blocks = []
        for match in re.finditer(r'\[data-theme="([^"]+)"\]\s*\{([^{}]+)\}', source, re.DOTALL):
            theme_blocks.append((match.group(1), dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", match.group(2)))))

        expected_themes = {"obsidian-dark", "parchment-light", "system-match", "nest-light", "nest-dark"}
        self.assertEqual(expected_themes, {name for name, _values in theme_blocks})
        pairs = (
            ("--color-surface", "--color-on-surface"),
            ("--color-surface", "--color-on-surface-variant"),
            ("--color-background", "--color-on-background"),
            ("--color-primary", "--color-on-primary"),
            ("--color-primary-container", "--color-on-primary-container"),
            ("--color-secondary", "--color-on-secondary"),
            ("--color-secondary-container", "--color-on-secondary-container"),
            ("--color-tertiary", "--color-on-tertiary"),
            ("--color-tertiary-container", "--color-on-tertiary-container"),
            ("--color-error", "--color-on-error"),
            ("--color-error-container", "--color-on-error-container"),
        )
        for block_index, (theme_name, overrides) in enumerate(theme_blocks):
            values = {**root_values, **overrides}
            for background, foreground in pairs:
                ratio = _contrast_ratio(values[background], values[foreground])
                self.assertGreaterEqual(ratio, 4.5, f"{theme_name} block {block_index}: {foreground} on {background} = {ratio:.2f}:1")
            for status_token in ("--color-success", "--color-warning", "--color-info"):
                ratio = _contrast_ratio(values[status_token], values["--color-surface"])
                self.assertGreaterEqual(ratio, 4.5, f"{theme_name} block {block_index}: {status_token} on surface = {ratio:.2f}:1")
                tinted_surface = _mix_hex(values[status_token], values["--color-surface"], 0.18)
                tinted_ratio = _contrast_ratio(values[status_token], tinted_surface)
                self.assertGreaterEqual(tinted_ratio, 4.5, f"{theme_name} block {block_index}: {status_token} on 18% tint = {tinted_ratio:.2f}:1")
            shell_muted = _mix_hex(values["--color-on-surface-variant"], values["--color-surface"], 0.8)
            shell_muted_ratio = _contrast_ratio(shell_muted, values["--color-surface"])
            self.assertGreaterEqual(shell_muted_ratio, 4.5, f"{theme_name} block {block_index}: muted shell text = {shell_muted_ratio:.2f}:1")
            outline_ratio = _contrast_ratio(values["--color-outline"], values["--color-surface"])
            self.assertGreaterEqual(outline_ratio, 3.0, f"{theme_name} block {block_index}: form outline = {outline_ratio:.2f}:1")

    def test_primary_templates_have_landmarks_headings_and_named_controls(self):
        names = (
            "login.html", "dashboard.html", "calendar.html", "courses.html", "notes.html",
            "notes_editor.html", "task.html", "chat.html", "files.html", "settings.html",
            "calendar_share.html", "file_share_download.html", "file_share_folder.html",
            "notes_shared_folder.html", "user_profile.html",
        )
        problems = []
        for name in names:
            parser = _TemplateParser()
            parser.feed((TEMPLATES / name).read_text())
            self.assertTrue(any(node["tag"] == "main" for node in parser.nodes), f"{name} needs a main landmark")
            self.assertTrue(any(node["tag"] == "h1" for node in parser.nodes), f"{name} needs an h1")

            labels_by_for = {
                node["attrs"].get("for") for node in parser.nodes
                if node["tag"] == "label" and node["attrs"].get("for")
            }
            for node in parser.nodes:
                attrs = node["attrs"]
                if node["tag"] not in {"input", "select", "textarea"}:
                    continue
                if attrs.get("type", "").lower() == "hidden" or "hidden" in attrs:
                    continue
                ancestor = node["parent"]
                inside_label = False
                while ancestor:
                    if ancestor["tag"] == "label":
                        inside_label = True
                        break
                    ancestor = ancestor["parent"]
                named = attrs.get("aria-label") or attrs.get("aria-labelledby") or inside_label or attrs.get("id") in labels_by_for
                if not named:
                    problems.append(f"{name}: unnamed {node['tag']}#{attrs.get('id', '')}")
        self.assertEqual([], problems)

    def test_shared_accessibility_layer_covers_focus_dialogs_and_motion(self):
        css = (ROOT / "static/css/global-foundation.css").read_text()
        javascript = (ROOT / "static/js/core/global.js").read_text()
        self.assertIn(".apstudy-skip-link", css)
        self.assertRegex(css, r"::selection\s*\{[^}]*--color-on-surface")
        for template in TEMPLATES.glob("*.html"):
            self.assertNotIn("selection:bg-primary", template.read_text(), template.name)
        self.assertRegex(css, r":focus-visible\s*\{[^}]*outline:\s*3px[^}]*!important")
        self.assertIn("border-color: var(--color-outline", css)
        self.assertRegex(css, r"prefers-reduced-motion:\s*reduce[\s\S]*animation-duration:\s*0\.01ms\s*!important")
        self.assertIn('event.key !== "Tab"', javascript)
        self.assertIn("focusableElements(activeDialog)", javascript)
        self.assertIn("record.previousFocus.focus", javascript)
        self.assertIn('sibling.setAttribute("inert", "")', javascript)
        self.assertIn("Skip to main content", javascript)

    def test_dynamic_primary_controls_keep_accessible_names_and_dialog_semantics(self):
        course_modal = (ROOT / "static/js/calendar/integrations/course-modal.js").read_text()
        sources = (ROOT / "static/js/calendar/integrations/sources.js").read_text()
        share = (ROOT / "static/js/calendar/integrations/share.js").read_text()
        courses = (ROOT / "static/js/courses/panel.js").read_text()
        chat = "\n".join(
            (
                (ROOT / "static/js/chat/runtime.js").read_text(),
                (ROOT / "static/js/chat/rooms.js").read_text(),
            )
        )
        self.assertIn('role="dialog" aria-modal="true" aria-labelledby="courses-modal-title"', course_modal)
        self.assertIn('aria-label="Search courses"', course_modal)
        self.assertIn('aria-label="Filter by term"', course_modal)
        self.assertIn('aria-label="${channel.toUpperCase()} color slider"', sources)
        self.assertIn('aria-label="Share link"', share)
        self.assertIn('aria-label="${escapeHtml(day.key)} start time"', courses)
        self.assertIn('drawer.setAttribute("aria-modal", "true")', chat)

    def test_keyboard_paths_cover_menus_mobile_navigation_and_drag_alternatives(self):
        global_js = (ROOT / "static/js/core/global.js").read_text()
        navbar = (ROOT / "static/js/core/navbar.js").read_text()
        sidebar = (ROOT / "static/js/core/sidebar.js").read_text()
        dashboard = (ROOT / "static/js/dashboard/layout-editor.js").read_text()
        notes = (ROOT / "static/js/notes/list/cards.js").read_text()
        tasks = (ROOT / "static/js/tasks/task-app-helpers.js").read_text()
        self.assertIn('["ArrowDown", "ArrowUp", "Home", "End"]', global_js)
        self.assertIn("aria-haspopup=\"menu\"", navbar)
        self.assertIn("avatarBtn?.focus", navbar)
        self.assertIn("sidebar.toggleAttribute('inert'", sidebar)
        self.assertIn("sidebar.setAttribute('aria-modal', 'true')", sidebar)
        self.assertIn("toolbarMoveEarlier", dashboard)
        self.assertIn("toolbarMoveLater", dashboard)
        self.assertIn("event.key !== \"Enter\" && event.key !== \" \"", dashboard)
        self.assertIn('data-action="share"', notes)
        self.assertNotIn('data-action="move-earlier"', notes)
        self.assertIn('label: "Move list earlier"', tasks)
        self.assertIn('label: "Move task earlier"', tasks)
        self.assertIn("Move to ${list.name}", tasks)
        for relative_path in ("static/js/dashboard/layout-editor.js", "static/js/tasks/task-components.js"):
            self.assertIn("prefersReducedMotion", (ROOT / relative_path).read_text())


class RenderedJourneyAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = os.path.join(self.temp_dir.name, "accessibility.sqlite3")
        self.environment = patch.dict(os.environ, {
            "DATABASE_PATH": self.database_path,
            "FLASK_SECRET_KEY": "accessibility-test-key",
            "FLASK_ENV": "testing",
            "APSTUDY_ALLOW_INSECURE_HTTP": "1",
            "SCHEDULER_ENABLED": "0",
        }, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        with sqlite3.connect(self.database_path) as connection:
            connection.executescript((ROOT / "migrations/001_initial_schema.sql").read_text())
            connection.execute(
                """
                INSERT INTO users (
                    id, google_id, email, name, username, onboarding_complete,
                    education_level, school, school_key, major, graduation_year,
                    emory_student, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "accessibility-user", "accessibility-google", "accessibility@example.test",
                    "Accessibility Student", "accessibility-student", 1, "Undergraduate",
                    "Emory University", "emory-university", "Computer Science", "2027", 1,
                    "2026-01-01T00:00:00Z",
                ),
            )
            connection.commit()
        from app import create_app
        from extensions import login_manager
        from models import load_user
        with patch("services.scheduler.init_scheduler"), patch("services.discord_audit.init_discord_audit"):
            self.app = create_app()
        previous_loader = login_manager._user_callback
        login_manager._user_callback = load_user
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.app.config.update(TESTING=True)

    @staticmethod
    def _assert_rendered_document(test_case, response, route):
        test_case.assertIn(response.status_code, {200, 401, 404}, route)
        parser = _TemplateParser()
        parser.feed(response.get_data(as_text=True))
        test_case.assertTrue(any(node["tag"] == "main" for node in parser.nodes), f"{route} needs main")
        test_case.assertTrue(any(node["tag"] == "h1" for node in parser.nodes), f"{route} needs h1")
        ids = [node["attrs"]["id"] for node in parser.nodes if node["attrs"].get("id")]
        test_case.assertEqual(len(ids), len(set(ids)), f"{route} has duplicate IDs")
        id_set = set(ids)
        for node in parser.nodes:
            labelled_by = node["attrs"].get("aria-labelledby", "").split()
            for label_id in labelled_by:
                test_case.assertIn(label_id, id_set, f"{route} references missing aria-labelledby target {label_id}")

    def test_rendered_public_and_authenticated_journeys_have_document_structure(self):
        anonymous = self.app.test_client()
        for route in ("/login", "/calendar/share/not-found", "/files/share/not-found", "/files/folder/not-found", "/notes/not-found"):
            with self.subTest(route=route):
                self._assert_rendered_document(self, anonymous.get(route), route)

        authenticated = self.app.test_client()
        with authenticated.session_transaction() as session:
            session["_user_id"] = "accessibility-user"
            session["_fresh"] = True
        for route in ("/dashboard", "/calendar", "/courses", "/notes", "/tasks", "/chat", "/files", "/settings/"):
            with self.subTest(route=route):
                self._assert_rendered_document(self, authenticated.get(route), route)


if __name__ == "__main__":
    unittest.main()
