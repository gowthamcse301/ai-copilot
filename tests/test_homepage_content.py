import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from starlette.requests import Request

import ai_assistant
import app as app_module
import db
from content_safety import sanitize_homepage_css, sanitize_homepage_html


class HomepageContentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.get_conn_patch = patch.object(db, "get_conn", side_effect=self.open_test_connection)
        self.get_conn_patch.start()
        db.init_db()

    def tearDown(self):
        self.get_conn_patch.stop()
        self.temp_dir.cleanup()

    @contextmanager
    def open_test_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def test_sanitizer_removes_active_content_and_keeps_safe_markup(self):
        html = (
            '<h2 onclick="alert(1)">Welcome</h2>'
            '<script>alert("bad")</script>'
            '<p><a href="javascript:alert(1)">Read more</a></p>'
            '<iframe src="https://example.com">embedded content</iframe>'
            '<h3 class="happy-toonzz-title">News</h3>'
        )

        sanitized = sanitize_homepage_html(html)

        self.assertIn("<h2>Welcome</h2>", sanitized)
        self.assertIn('<p><a rel="noopener noreferrer">Read more</a></p>', sanitized)
        self.assertNotIn("onclick", sanitized)
        self.assertNotIn("javascript:", sanitized)
        self.assertNotIn("alert", sanitized)
        self.assertNotIn("iframe", sanitized)
        self.assertIn('<h3 class="happy-toonzz-title">News</h3>', sanitized)

    def test_sanitizer_repairs_escaped_class_attribute_quotes(self):
        sanitized = sanitize_homepage_html(
            r'<div class=\&quot;happy-toonzz-section\&quot;>Update</div>'
        )

        self.assertEqual(
            sanitized,
            '<div class="happy-toonzz-section">Update</div>',
        )

    def test_sanitizer_rejects_empty_or_oversized_content(self):
        with self.assertRaises(ValueError):
            sanitize_homepage_html("<script>alert(1)</script>")
        with self.assertRaises(ValueError):
            sanitize_homepage_html("x" * 8001)

    def test_css_sanitizer_allows_styles_and_blocks_external_resources(self):
        sanitized = sanitize_homepage_css(
            ".happy-toonzz-title { color: #684dc0; } "
            "@media (max-width: 700px) { .happy-toonzz-title { font-size: 1.5rem; } }"
        )
        self.assertIn(".happy-toonzz-title { color: #684dc0; }", sanitized)
        self.assertIn("@media (max-width: 700px)", sanitized)
        self.assertEqual(sanitize_homepage_css(""), "")

        unsafe_stylesheets = (
            "@import url(https://example.com/site.css);",
            ".card { background-image: url(https://example.com/image.png); }",
            ".card { behavior: url(example); }",
            '.card { content: "</style>"; }',
        )
        for css in unsafe_stylesheets:
            with self.subTest(css=css), self.assertRaises(ValueError):
                sanitize_homepage_css(css)

    def test_ai_tool_saves_sanitized_draft_without_publishing(self):
        result, media = ai_assistant._execute_tool(
            "draft_homepage_content",
            {
                "html": '<h2>New update</h2><script>alert(1)</script>',
                "css": ".happy-toonzz-title { color: #684dc0; }",
                "summary": "Added a homepage update",
            },
            "",
        )

        content = db.get_homepage_content()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "draft_only")
        self.assertEqual(media, [])
        self.assertEqual(content["draft_html"], "<h2>New update</h2>")
        self.assertEqual(content["draft_css"], ".happy-toonzz-title { color: #684dc0; }")
        self.assertEqual(content["published_html"], "")

    def test_homepage_edit_detection_skips_questions_and_status_reports(self):
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "Add the existing image on the section."
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "Update this", "Earlier we discussed the homepage section."
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "change this", "Current page: Homepage content editor"
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "Make the heading bigger", "Current page: Homepage content editor"
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "Style it with a brighter color", "Current page: Homepage content editor"
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "சேஞ்ச் பேக்கிரவுண்ட் கிரீன்", "homepage content draft"
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "ரிமூவ் தி செக்ஷன்", "homepage content draft"
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "கடைசி இரண்டு செக்ஷன்",
                previous_user_prompt="ரிமூவ் டு செக்ஷன்ஸ்",
            )
        )
        self.assertTrue(
            ai_assistant._is_homepage_content_edit_request(
                "அப்டேட் பண்ண கன்டன்ட் சேட்ல குடு"
            )
        )
        self.assertFalse(ai_assistant._is_homepage_content_edit_request("How do I use AI?"))
        self.assertFalse(
            ai_assistant._is_homepage_content_edit_request(
                "Why is it not updating?", "homepage section"
            )
        )
        self.assertFalse(
            ai_assistant._is_homepage_content_edit_request(
                "AI said it changed, but it is not updated in the draft textbox."
            )
        )
        self.assertFalse(
            ai_assistant._is_homepage_content_edit_request(
                "How many sections are on the homepage?",
                previous_user_prompt="Remove the last section.",
            )
        )

    def test_homepage_edit_forces_tool_call_and_keeps_publish_separate(self):
        db.publish_homepage_content("<p>Currently live</p>")
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="draft_homepage_content",
                    args={
                        "html": (
                            '<h2>New update</h2>'
                            '<img src="/images/look-chef.jpg" alt="Chef look">'
                        ),
                        "css": ".happy-toonzz-title { color: #684dc0; }",
                        "summary": "Added the chef image to the homepage section",
                    },
                )
            ]
        )

        with (
            patch.object(ai_assistant, "_make_client", return_value=client),
            patch.object(db, "get_gemini_api_key", return_value="test-api-key"),
            patch.object(db, "add_ai_message"),
            patch.object(db, "list_ai_messages", return_value=[]),
        ):
            result = ai_assistant.run_assistant_turn(
                user_id=1,
                prompt="Add the existing chef image to the homepage section.",
            )

        content = db.get_homepage_content()
        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(result["tools"], ["draft_homepage_content"])
        self.assertIn("as a draft", result["reply"])
        self.assertEqual(content["published_html"], "<p>Currently live</p>")
        self.assertEqual(content["published_css"], "")
        self.assertIn('<img src="/images/look-chef.jpg"', content["draft_html"])
        self.assertEqual(content["draft_css"], ".happy-toonzz-title { color: #684dc0; }")
        self.assertEqual(
            config.tool_config.function_calling_config.allowed_function_names,
            ["draft_homepage_content"],
        )

    def test_tamil_transliterated_background_edit_forces_draft_tool(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="draft_homepage_content",
                    args={
                        "html": '<div class="happy-toonzz-section">Update</div>',
                        "css": ".happy-toonzz-section { background-color: green; }",
                        "summary": "Changed the section background to green",
                    },
                )
            ]
        )

        with (
            patch.object(ai_assistant, "_make_client", return_value=client),
            patch.object(db, "get_gemini_api_key", return_value="test-api-key"),
            patch.object(db, "add_ai_message"),
            patch.object(db, "list_ai_messages", return_value=[]),
        ):
            result = ai_assistant.run_assistant_turn(
                user_id=1,
                prompt="சேஞ்ச் பேக்கிரவுண்ட் கிரீன்",
                page_context="content",
            )

        self.assertEqual(result["tools"], ["draft_homepage_content"])
        self.assertIn("background-color: green", db.get_homepage_content()["draft_css"])

    def test_homepage_edit_failure_does_not_claim_a_draft_was_saved(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(function_calls=[])

        with (
            patch.object(ai_assistant, "_make_client", return_value=client),
            patch.object(db, "get_gemini_api_key", return_value="test-api-key"),
            patch.object(db, "add_ai_message"),
            patch.object(db, "list_ai_messages", return_value=[]),
        ):
            result = ai_assistant.run_assistant_turn(
                user_id=1,
                prompt="Update the homepage section with a new announcement.",
            )

        self.assertIn("No changes were made", result["reply"])
        self.assertNotIn("saved the updated homepage section", result["reply"])
        self.assertEqual(db.get_homepage_content()["draft_html"], "")

    def test_empty_model_response_is_reported_as_failure(self):
        client = Mock()
        client.interactions.create.return_value = SimpleNamespace(output_text="", steps=[])

        with (
            patch.object(ai_assistant, "_make_client", return_value=client),
            patch.object(db, "get_gemini_api_key", return_value="test-api-key"),
            patch.object(db, "add_ai_message"),
            patch.object(db, "list_ai_messages", return_value=[]),
        ):
            result = ai_assistant.run_assistant_turn(user_id=1, prompt="Give me an update.")

        self.assertFalse(result["ok"])
        self.assertIn("empty response", result["reply"].lower())
        self.assertNotIn("all done", result["reply"].lower())

    def test_token_limit_error_is_reported_as_failure(self):
        client = Mock()
        client.interactions.create.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED: input token limit exceeded"
        )

        with (
            patch.object(ai_assistant, "_make_client", return_value=client),
            patch.object(db, "get_gemini_api_key", return_value="test-api-key"),
            patch.object(db, "add_ai_message"),
            patch.object(db, "list_ai_messages", return_value=[]),
        ):
            result = ai_assistant.run_assistant_turn(user_id=1, prompt="Give me an update.")

        self.assertFalse(result["ok"])
        self.assertIn("token limit", result["reply"].lower())
        self.assertNotIn("all done", result["reply"].lower())

    def test_restore_copies_previous_version_to_draft_only(self):
        first_draft = sanitize_homepage_html("<p>First version</p>")
        second_draft = sanitize_homepage_html("<p>Second version</p>")
        first_css = sanitize_homepage_css(".first { color: red; }")
        second_css = sanitize_homepage_css(".second { color: blue; }")
        db.save_homepage_content_draft(first_draft, source="admin", summary="First", content_css=first_css)
        db.publish_homepage_content(first_draft, first_css)
        published_version = next(
            version for version in db.list_homepage_content_versions() if version["action"] == "published"
        )
        db.save_homepage_content_draft(second_draft, source="ai", summary="Second", content_css=second_css)

        self.assertTrue(db.restore_homepage_content_version(published_version["id"]))
        content = db.get_homepage_content()
        self.assertEqual(content["draft_html"], first_draft)
        self.assertEqual(content["draft_css"], first_css)
        self.assertEqual(content["published_html"], first_draft)
        self.assertEqual(content["published_css"], first_css)

    def test_database_migrates_existing_homepage_tables(self):
        with db.get_conn() as connection:
            connection.execute("DROP TABLE homepage_content_versions")
            connection.execute("DROP TABLE homepage_content")
            connection.execute(
                """
                CREATE TABLE homepage_content (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    draft_html TEXT NOT NULL DEFAULT '',
                    published_html TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE homepage_content_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_html TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('draft', 'published')),
                    source TEXT NOT NULL CHECK (source IN ('ai', 'admin', 'restore')),
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO homepage_content (id, draft_html, published_html, updated_at) VALUES (1, '', '', 'now')"
            )

        db.init_db()

        content = db.get_homepage_content()
        self.assertEqual(content["draft_css"], "")
        self.assertEqual(content["published_css"], "")

    def test_ai_task_lifecycle_is_persisted_and_user_scoped(self):
        with db.get_conn() as connection:
            user_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]

        task_id = db.create_ai_task(user_id, "Change this")
        self.assertIsNotNone(task_id)
        self.assertEqual(db.get_active_ai_task(user_id)["status"], "queued")
        self.assertIsNone(db.create_ai_task(user_id, "Another task"))
        self.assertIsNone(db.get_ai_task(user_id + 1, task_id))

        db.update_ai_task(task_id, "running")
        self.assertEqual(db.get_active_ai_task(user_id)["status"], "running")
        db.update_ai_task(task_id, "completed", result={"reply": "Draft saved"})

        saved_task = db.get_ai_task(user_id, task_id)
        self.assertEqual(saved_task["status"], "completed")
        self.assertEqual(saved_task["result"], {"reply": "Draft saved"})
        self.assertIsNone(db.get_active_ai_task(user_id))

    def test_ai_tasks_recover_as_failed_after_server_restart(self):
        with db.get_conn() as connection:
            user_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        task_id = db.create_ai_task(user_id, "Continue this task")
        db.update_ai_task(task_id, "running")

        db.init_db()

        task = db.get_ai_task(user_id, task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("server restarted", task["error"].lower())

    def test_background_ai_task_saves_result_and_page_context(self):
        with db.get_conn() as connection:
            user_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        task_id = db.create_ai_task(user_id, "Change this")

        with patch.object(
            ai_assistant,
            "run_assistant_turn",
            return_value={"ok": True, "reply": "Draft saved", "media": []},
        ) as run_turn:
            app_module._run_ai_chat_task(
                task_id,
                user_id,
                "Change this",
                None,
                "",
                "content",
            )

        run_turn.assert_called_once_with(
            user_id,
            "Change this",
            image_data=None,
            image_mime_type="",
            page_context="content",
        )
        task = db.get_ai_task(user_id, task_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["reply"], "Draft saved")

    def test_background_ai_task_marks_returned_failure_as_failed(self):
        with db.get_conn() as connection:
            user_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        task_id = db.create_ai_task(user_id, "Give me an update.")
        error = "The AI task failed because the token limit was reached."

        with patch.object(
            ai_assistant,
            "run_assistant_turn",
            return_value={"ok": False, "reply": error, "error": error},
        ):
            app_module._run_ai_chat_task(
                task_id,
                user_id,
                "Give me an update.",
                None,
                "",
                "assistant",
            )

        task = db.get_ai_task(user_id, task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"], error)

    def test_chat_endpoint_queues_task_and_runs_it_in_background(self):
        with db.get_conn() as connection:
            user_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        session_token = db.create_session(user_id)
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/admin/ai/chat",
                "raw_path": b"/admin/ai/chat",
                "query_string": b"",
                "headers": [
                    (
                        b"cookie",
                        f"{db.SESSION_COOKIE_NAME}={session_token}".encode("ascii"),
                    )
                ],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            }
        )
        with (
            patch.object(app_module._AI_TASK_EXECUTOR, "submit") as submit_task,
            patch.object(
                ai_assistant,
                "run_assistant_turn",
                return_value={"ok": True, "reply": "Draft saved", "media": []},
            ),
        ):
            response = app_module.admin_ai_chat(
                request=request,
                prompt="Change this",
                image_file=None,
                page_context="content",
            )
            queued = json.loads(response.body)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(db.get_active_ai_task(user_id)["id"], queued["task_id"])
            callback, *task_args = submit_task.call_args.args
            callback(*task_args)

        task = db.get_ai_task(user_id, queued["task_id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["reply"], "Draft saved")

    def test_public_homepage_assistant_is_only_rendered_for_admins(self):
        def make_request(session_token=None):
            headers = []
            if session_token:
                headers.append(
                    (
                        b"cookie",
                        f"{db.SESSION_COOKIE_NAME}={session_token}".encode("ascii"),
                    )
                )
            return Request(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/",
                    "raw_path": b"/",
                    "query_string": b"",
                    "headers": headers,
                    "client": ("testclient", 50000),
                    "server": ("testserver", 80),
                }
            )

        with db.get_conn() as connection:
            user_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        session_token = db.create_session(user_id)

        public_response = asyncio.run(app_module.home(make_request()))
        admin_response = asyncio.run(app_module.home(make_request(session_token)))

        self.assertNotIn(b"assistantWidgetLauncher", public_response.body)
        self.assertIn(b"assistantWidgetLauncher", admin_response.body)


if __name__ == "__main__":
    unittest.main()
