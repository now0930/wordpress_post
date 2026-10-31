"""Offline unit tests for wp_check.py (no requests to WordPress)."""
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wp_check


class ConfigTests(unittest.TestCase):
    def test_simple_env_and_app_password_with_spaces(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".env"
            path.write_text(
                'WP_URL=https://example.org/wordpress\n'
                'WP_USER="editor"\n'
                'WP_APP_PASSWORD="abcd efgh ijkl"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                data = wp_check.load_env(path)
        self.assertEqual(data["WP_USER"], "editor")
        self.assertEqual(data["WP_APP_PASSWORD"], "abcd efgh ijkl")

    def test_environment_values_override_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".env"
            path.write_text("WP_USER=from_file\n", encoding="utf-8")
            with patch.dict(os.environ, {"WP_USER": "from_environment"}):
                data = wp_check.load_env(path)
        self.assertEqual(data["WP_USER"], "from_environment")

    def test_rest_base_rejects_insecure_and_embedded_credentials(self):
        for url in (
            "http://example.org/wordpress",
            "https://user:secret@example.org/wordpress",
            "https://example.org/wordpress?token=abc",
            "https://example.org/wordpress/wp-json",
        ):
            with self.subTest(url=url):
                with self.assertRaises(wp_check.ProbeError):
                    wp_check.rest_base(url)

    def test_rest_base_subdirectory(self):
        self.assertEqual(
            wp_check.rest_base("https://example.org/wordpress/"),
            "https://example.org/wordpress/wp-json",
        )


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.index = {
            "routes": {
                "/wp/v2/posts": {},
                "/wp/v2/media": {},
                "/wp/v2/users/me": {},
            }
        }
        self.config = {
            "WP_URL": "https://example.org/wordpress",
            "WP_USER": "writer",
            "WP_APP_PASSWORD": "secret app password",
        }

    def test_public_only_does_not_send_credentials(self):
        calls = []

        def fake_get(url, authorization, timeout):
            calls.append((url, authorization))
            return self.index

        with patch.object(wp_check, "get_json", side_effect=fake_get):
            wp_check.check(self.config, public_only=True, timeout=2)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][1])

    def test_authenticated_check_is_read_only_and_scoped(self):
        calls = []

        def fake_get(url, authorization, timeout):
            calls.append((url, authorization))
            if url.endswith("/wp-json/"):
                return self.index
            return {
                "id": 1,
                "capabilities": {"edit_posts": True, "upload_files": True},
            }

        with patch.object(wp_check, "get_json", side_effect=fake_get):
            wp_check.check(self.config, public_only=False, timeout=2)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0][1])
        self.assertEqual(
            calls[1][0],
            "https://example.org/wordpress/wp-json/wp/v2/users/me?context=edit",
        )
        self.assertEqual(
            calls[1][1],
            "Basic " + base64.b64encode(
                b"writer:secret app password"
            ).decode("ascii"),
        )

    def test_missing_permission_is_error(self):
        responses = iter([
            self.index,
            {"id": 1, "capabilities": {
                "edit_posts": False, "upload_files": True
            }},
        ])
        with patch.object(wp_check, "get_json", side_effect=lambda *args, **kwargs: next(responses)):
            with self.assertRaises(wp_check.ProbeError):
                wp_check.check(self.config, public_only=False, timeout=2)

    def test_redirect_to_another_host_is_rejected(self):
        from email.message import Message

        handler = wp_check.SameOriginRedirect()
        req = wp_check.urllib.request.Request(
            "https://example.org/wordpress/wp-json",
            headers={"Authorization": "Basic redacted"},
        )
        with self.assertRaises(wp_check.ProbeError):
            handler.redirect_request(
                req, None, 302, "Found", Message(),
                "https://other.example.com/redirect",
            )


if __name__ == "__main__":
    unittest.main()
