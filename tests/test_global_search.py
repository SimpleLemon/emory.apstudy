import unittest
import json
from unittest.mock import patch

from flask import Flask
from flask_login import UserMixin

from extensions import login_manager
from blueprints.search_api import search_api_bp
from services import global_search


class SearchUser(UserMixin):
    def __init__(self, user_id="search-user", **profile):
        self.id = user_id
        self.school = profile.get("school")
        self.school_key = profile.get("school_key")
        self.emory_student = profile.get("emory_student", False)


class GlobalSearchAggregationTests(unittest.TestCase):
    def test_non_emory_search_never_loads_courses(self):
        user = SearchUser(school="Arizona State University")
        loaders = {
            name: patch.object(global_search, f"_search_{name}", return_value=[])
            for name in ("files", "notes", "events", "messages", "courses")
        }
        with loaders["files"] as files, loaders["notes"] as notes, \
                loaders["events"] as events, loaders["messages"] as messages, \
                loaders["courses"] as courses:
            payload = global_search.search_global(user, "linear algebra")

        for loader in (files, notes, events, messages):
            loader.assert_called_once_with("search-user", "linear algebra")
        courses.assert_not_called()
        self.assertFalse(payload["courses_enabled"])
        self.assertEqual(payload["groups"]["courses"], [])

    def test_emory_search_loads_saved_courses(self):
        user = SearchUser(school_key="emory-university")
        course_result = {"id": "course-1", "title": "MATH 221"}
        with patch.object(global_search, "_search_files", return_value=[]), \
                patch.object(global_search, "_search_notes", return_value=[]), \
                patch.object(global_search, "_search_events", return_value=[]), \
                patch.object(global_search, "_search_messages", return_value=[]), \
                patch.object(global_search, "_search_courses", return_value=[course_result]) as courses:
            payload = global_search.search_global(user, "math")

        courses.assert_called_once_with("search-user", "math")
        self.assertTrue(payload["courses_enabled"])
        self.assertEqual(payload["groups"]["courses"], [course_result])

    def test_category_failure_returns_other_authorized_results(self):
        user = SearchUser()
        file_result = {"id": "file-1", "title": "Syllabus.pdf"}
        with patch.object(global_search, "_search_files", return_value=[file_result]), \
                patch.object(global_search, "_search_notes", side_effect=RuntimeError("offline")), \
                patch.object(global_search, "_search_events", return_value=[]), \
                patch.object(global_search, "_search_messages", return_value=[]):
            payload = global_search.search_global(user, "syllabus")

        self.assertEqual(payload["groups"]["files"], [file_result])
        self.assertEqual(payload["unavailable_categories"], ["notes"])
        self.assertEqual(payload["total"], 1)

    def test_message_results_require_an_existing_conversation(self):
        threads = [
            {
                "$id": "thread-empty",
                "participant_a": "search-user",
                "participant_b": "person-empty",
                "last_message_at": None,
            },
            {
                "$id": "thread-active",
                "participant_a": "search-user",
                "participant_b": "person-active",
                "last_message_at": "2026-07-20T12:00:00Z",
            },
        ]

        def rows_for_collection(collection, _queries):
            return threads if collection == global_search.COLLECTIONS["chat_dm_threads"] else []

        with patch.object(global_search, "list_rows_all", side_effect=rows_for_collection), \
                patch.object(global_search, "get_row_safe", return_value={
                    "$id": "person-active",
                    "name": "Alex Morgan",
                    "username": "alex",
                }) as get_user:
            results = global_search._search_messages("search-user", "alex")

        self.assertEqual([result["id"] for result in results], ["thread-active"])
        get_user.assert_called_once()

    def test_title_prefix_ranks_above_context_only_match(self):
        results = [
            {"title": "Biology notes", "timestamp": None, "_score": global_search._match_score("bio", "Biology notes")},
            {"title": "Week one", "timestamp": None, "_score": global_search._match_score("bio", "Week one", "biology")},
        ]
        ranked = global_search._ranked(results)
        self.assertEqual(ranked[0]["title"], "Biology notes")

    def test_file_results_are_user_scoped_and_build_reveal_links(self):
        file_row = {
            "$id": "file-1",
            "user_id": "search-user",
            "folder_id": "folder-1",
            "original_filename": "Biology syllabus.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 2048,
            "expires_at": "2026-08-20T00:00:00Z",
        }
        folder_row = {"$id": "folder-1", "user_id": "search-user", "name": "Fall classes"}

        def rows_for_collection(collection, _queries):
            if collection == global_search.COLLECTIONS["shared_files"]:
                return [file_row]
            if collection == global_search.COLLECTIONS["file_folders"]:
                return [folder_row]
            return []

        with patch.object(global_search, "list_rows_all", side_effect=rows_for_collection) as rows:
            results = global_search._search_files("search-user", "biology")

        self.assertEqual(results[0]["href"], "/files?file=file-1&folder=folder-1")
        for call in rows.call_args_list:
            serialized_queries = [json.loads(query) for query in call.args[1]]
            self.assertIn(
                {"method": "equal", "attribute": "user_id", "values": ["search-user"]},
                serialized_queries,
            )

    def test_calendar_results_build_event_and_date_deep_links(self):
        event = {
            "event_ref": "feed:canvas:event-1",
            "title": "Biology midterm",
            "start": "2026-10-02T16:00:00Z",
            "end": "2026-10-02T17:00:00Z",
            "course": "BIOL 141",
            "calendar_id": "feed:canvas",
        }
        with patch.object(global_search, "first_row", return_value=None), \
                patch.object(global_search, "_load_serialized_calendar_events", return_value=([event], [], [])):
            results = global_search._search_events("search-user", "midterm")

        self.assertEqual(
            results[0]["href"],
            "/calendar?event=feed%3Acanvas%3Aevent-1&date=2026-10-02",
        )


class GlobalSearchRouteTests(unittest.TestCase):
    def setUp(self):
        previous_loader = login_manager._user_callback
        previous_unauthorized = login_manager.unauthorized_callback
        previous_login_view = login_manager.login_view
        self.addCleanup(setattr, login_manager, "_user_callback", previous_loader)
        self.addCleanup(setattr, login_manager, "unauthorized_callback", previous_unauthorized)
        self.addCleanup(setattr, login_manager, "login_view", previous_login_view)
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.config["SERVER_NAME"] = "example.test"
        login_manager.unauthorized_callback = None
        login_manager.login_view = None
        login_manager.init_app(self.app)
        self.app.register_blueprint(search_api_bp)

        @login_manager.user_loader
        def load_user(user_id):
            return SearchUser(user_id) if user_id == "search-user" else None

    def _login(self, client):
        with client.session_transaction() as session:
            session["_user_id"] = "search-user"
            session["_fresh"] = True

    def test_search_requires_authentication(self):
        response = self.app.test_client().get("/api/search?q=notes")
        self.assertEqual(response.status_code, 401)

    def test_short_query_does_not_run_aggregation(self):
        with self.app.test_client() as client:
            self._login(client)
            with patch("blueprints.search_api.search_global") as search:
                response = client.get("/api/search?q=a")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["total"], 0)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        search.assert_not_called()

    def test_normalized_query_is_forwarded(self):
        payload = {
            "query": "linear algebra",
            "total": 0,
            "courses_enabled": False,
            "unavailable_categories": [],
            "groups": {name: [] for name in global_search.GROUP_ORDER},
        }
        with self.app.test_client() as client:
            self._login(client)
            with patch("blueprints.search_api.search_global", return_value=payload) as search:
                response = client.get("/api/search?q=%20linear%20%20algebra%20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        search.assert_called_once()
        self.assertEqual(search.call_args.args[1], "linear algebra")

    def test_query_length_is_bounded(self):
        with self.app.test_client() as client:
            self._login(client)
            response = client.get(f"/api/search?q={'a' * 121}")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
