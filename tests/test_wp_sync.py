"""Offline tests for WordPress draft sync: no network or real publishing."""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import wp_sync


class FakeWordPress:
    def __init__(self, initial=None):
        self.posts: dict[int, dict] = {}
        if initial:
            self.posts[initial["id"]] = initial
        self.requests = []
        self.uploads = []
        self.tags = {1: {"id": 1, "name": "기존 태그"}}

    def by_slug(self, slug):
        return [dict(v) for v in self.posts.values() if v["slug"] == slug]

    def by_id(self, post_id):
        return dict(self.posts[post_id])

    def upload(self, name, asset):
        self.uploads.append((name, asset))
        return "https://example.org/wp-content/uploads/" + name

    def keyword_tag_ids(self, keywords):
        # Exercise the real tag-lookup implementation with this fake REST API.
        return wp_sync.WordPress.keyword_tag_ids(self, keywords)

    def request(self, method, route, payload=None):
        self.requests.append((method, route, payload))
        if method == "GET" and route.startswith("tags?"):
            query = urllib.parse.parse_qs(route.partition("?")[2])
            search = query.get("search", [""])[0].casefold()
            return [
                dict(tag) for tag in self.tags.values()
                if search in tag["name"].casefold()
            ]
        if method == "POST" and route == "tags":
            new_id = max(self.tags, default=0) + 1
            tag = {"id": new_id, "name": payload["name"]}
            self.tags[new_id] = tag
            return dict(tag)
        assert method == "POST"
        if route == "posts":
            post_id = max(self.posts, default=100) + 1
        else:
            post_id = int(route.removeprefix("posts/"))
        saved = {
            "id": post_id,
            "slug": payload["slug"],
            "status": payload.get("status", "draft"),
            "content": {"raw": payload["content"]},
        }
        self.posts[post_id] = saved
        return saved


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "chapter32-1"
        self.folder.mkdir()
        (self.folder / "post.json").write_text(
            json.dumps({
                "title": "Chapter 32.1",
                "slug": "chapter32-1",
                "excerpt": "위험장소 정리",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_html(self, body):
        (self.folder / "post.html").write_text(body, encoding="utf-8")

    def test_dry_run_requires_no_client_and_writes_no_state(self):
        self.write_html('<div class="chapter19-wide">Hello</div>')
        result = wp_sync.synchronize(self.folder, None, apply=False)
        self.assertEqual(result, "dry-run")
        self.assertFalse((self.folder / ".wp-state.json").exists())

    def test_create_draft_and_skip_second_run(self):
        self.write_html('<div class="chapter19-wide">Hello</div>')
        fake = FakeWordPress()
        self.assertEqual(wp_sync.synchronize(self.folder, fake, True), "create")
        self.assertEqual(fake.requests[0][2]["status"], "draft")
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(wp_sync.synchronize(self.folder, fake, True), "skip")
        self.assertEqual(len(fake.requests), 1)

    def test_modified_source_updates_same_draft(self):
        self.write_html("<p>first</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        first_id = next(iter(fake.posts))
        self.write_html("<p>second</p>")
        self.assertEqual(wp_sync.synchronize(self.folder, fake, True), "update")
        self.assertEqual(len(fake.posts), 1)
        self.assertEqual(next(iter(fake.posts)), first_id)
        self.assertIn("second", fake.requests[-1][2]["content"])

    def test_base64_and_local_image_uploaded_once_by_hash(self):
        data = b"\x89PNG\r\n\x1a\n" + b"test-bytes"
        (self.folder / "images").mkdir()
        (self.folder / "images" / "scan.png").write_bytes(data)
        inline = "data:image/png;base64," + base64.b64encode(data).decode()
        self.write_html(
            f'<div><img src="images/scan.png"><img src="{inline}"></div>'
        )
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(len(fake.uploads), 1)
        saved = fake.requests[0][2]["content"]
        self.assertNotIn("base64,", saved)
        self.assertNotIn('src="images/scan.png"', saved)
        self.assertEqual(saved.count("https://example.org/wp-content/uploads/"), 2)

    def test_no_update_to_published_post(self):
        self.write_html("<p>test</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        post_id = next(iter(fake.posts))
        fake.posts[post_id]["status"] = "publish"
        self.write_html("<p>modified</p>")
        with self.assertRaisesRegex(wp_sync.SyncError, "임시글이 아닌"):
            wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(len(fake.requests), 1)

    def test_slug_collision_requires_explicit_draft_adoption(self):
        self.write_html("<p>test</p>")
        fake = FakeWordPress({
            "id": 55, "slug": "chapter32-1", "status": "draft",
        })
        with self.assertRaisesRegex(wp_sync.SyncError, "동일한 slug"):
            wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(len(fake.requests), 0)
        self.assertEqual(
            wp_sync.synchronize(self.folder, fake, True, adopt_draft=True),
            "update"
        )
        self.assertEqual(fake.requests[-1][1], "posts/55")

    def test_adoption_rejects_published_post(self):
        self.write_html("<p>test</p>")
        fake = FakeWordPress({
            "id": 55, "slug": "chapter32-1", "status": "publish",
        })
        with self.assertRaises(wp_sync.SyncError):
            wp_sync.synchronize(self.folder, fake, True, adopt_draft=True)

    def test_trashed_post_has_specific_error_and_is_untouched(self):
        self.write_html("<p>first</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        old_id = next(iter(fake.posts))
        fake.posts[old_id]["status"] = "trash"
        fake.posts[old_id]["slug"] = "chapter32-1__trashed"
        before_requests = len(fake.requests)
        before_state = wp_sync.load_state(self.folder)
        with self.assertRaisesRegex(wp_sync.SyncError, "휴지통"):
            wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(len(fake.requests), before_requests)
        self.assertEqual(wp_sync.load_state(self.folder), before_state)

    def test_recreate_trashed_creates_new_draft_and_reuses_images(self):
        data = bytes.fromhex("89504e470d0a1a0a") + b"test-image"
        (self.folder / "images").mkdir()
        (self.folder / "images" / "diagram.png").write_bytes(data)
        self.write_html('<img src="images/diagram.png">')
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        old_id = next(iter(fake.posts))
        self.assertEqual(len(fake.uploads), 1)

        fake.posts[old_id]["status"] = "trash"
        fake.posts[old_id]["slug"] = "chapter32-1__trashed"
        self.assertEqual(
            wp_sync.synchronize(
                self.folder, fake, True, recreate_trashed=True
            ),
            "create",
        )
        state = wp_sync.load_state(self.folder)
        self.assertNotEqual(state["post_id"], old_id)
        self.assertEqual(fake.posts[old_id]["status"], "trash")
        self.assertEqual(fake.posts[old_id]["slug"], "chapter32-1__trashed")
        self.assertEqual(fake.posts[state["post_id"]]["status"], "draft")
        self.assertEqual(fake.posts[state["post_id"]]["slug"], "chapter32-1")
        self.assertEqual(len(fake.uploads), 1)
        self.assertEqual(
            wp_sync.synchronize(self.folder, fake, True), "skip"
        )

    def test_recreate_rejects_active_draft(self):
        self.write_html("<p>test</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        with self.assertRaisesRegex(wp_sync.SyncError, "trash"):
            wp_sync.synchronize(self.folder, fake, True, recreate_trashed=True)
        self.assertEqual(len(fake.requests), 1)

    def test_recreate_rejects_other_post_with_same_slug(self):
        self.write_html("<p>test</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        old_id = next(iter(fake.posts))
        fake.posts[old_id]["status"] = "trash"
        fake.posts[old_id]["slug"] = "chapter32-1__trashed"
        fake.posts[77] = {"id": 77, "slug": "chapter32-1", "status": "draft"}
        with self.assertRaisesRegex(wp_sync.SyncError, "같은 slug"):
            wp_sync.synchronize(self.folder, fake, True, recreate_trashed=True)
        self.assertEqual(len(fake.requests), 1)

    def test_recreate_without_tracked_post_is_rejected(self):
        self.write_html("<p>test</p>")
        fake = FakeWordPress()
        with self.assertRaisesRegex(wp_sync.SyncError, "추적 중인"):
            wp_sync.synchronize(self.folder, fake, True, recreate_trashed=True)
        self.assertEqual(len(fake.requests), 0)

    def test_keywords_are_uploaded_as_wordpress_tag_ids(self):
        self.write_html("<p>first</p>")
        data = json.loads((self.folder / "post.json").read_text(encoding="utf-8"))
        data["keywords"] = ["기존 태그", "본질안전방폭", "MESG·MICR"]
        (self.folder / "post.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        fake = FakeWordPress()
        result = wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(result, "create")
        self.assertEqual(fake.requests[-1][2]["tags"], [1, 2, 3])
        self.assertEqual(len(fake.tags), 3)
        self.assertEqual(
            wp_sync.synchronize(self.folder, fake, True), "skip"
        )
        self.assertEqual(len(fake.tags), 3)

    def test_updating_keywords_changes_existing_draft_no_image_upload(self):
        self.write_html("<p>first</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        post_id = next(iter(fake.posts))
        data = json.loads((self.folder / "post.json").read_text(encoding="utf-8"))
        data["excerpt"] = "새 요약문"
        data["keywords"] = ["위험장소 분류", "폭발한계"]
        (self.folder / "post.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        self.assertEqual(
            wp_sync.synchronize(self.folder, fake, True), "update"
        )
        self.assertEqual(next(iter(fake.posts)), post_id)
        self.assertEqual(fake.requests[-1][2]["excerpt"], "새 요약문")
        self.assertEqual(fake.requests[-1][2]["tags"], [2, 3])
        self.assertEqual(fake.uploads, [])

    def test_omitted_keywords_do_not_reset_existing_tags(self):
        self.write_html("<p>first</p>")
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        self.assertNotIn("tags", fake.requests[-1][2])

    def test_empty_keywords_explicitly_clear_tags(self):
        self.write_html("<p>first</p>")
        data = json.loads((self.folder / "post.json").read_text(encoding="utf-8"))
        data["keywords"] = []
        (self.folder / "post.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(fake.requests[-1][2]["tags"], [])

    def test_duplicate_keywords_are_rejected(self):
        self.write_html("<p>first</p>")
        data = json.loads((self.folder / "post.json").read_text(encoding="utf-8"))
        data["keywords"] = ["방폭", " 방폭 "]
        (self.folder / "post.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(wp_sync.SyncError, "중복"):
            wp_sync.prepare(self.folder)

    def test_keyword_string_instead_of_list_is_rejected(self):
        self.write_html("<p>first</p>")
        data = json.loads((self.folder / "post.json").read_text(encoding="utf-8"))
        data["keywords"] = "방폭, 본질안전"
        (self.folder / "post.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(wp_sync.SyncError, "문자열 배열"):
            wp_sync.prepare(self.folder)

    def test_parent_path_escape_rejected(self):
        self.write_html('<img src="../../private.png">')
        with self.assertRaises(wp_sync.SyncError):
            wp_sync.prepare(self.folder)

    def test_external_https_image_unchanged(self):
        self.write_html('<img src="https://example.com/pic.png">')
        fake = FakeWordPress()
        wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(fake.uploads, [])
        self.assertIn(
            'src="https://example.com/pic.png"',
            fake.requests[0][2]["content"]
        )

    def test_data_src_attribute_is_not_mistaken_for_src(self):
        self.write_html(
            '<img data-src="placeholder.png" '
            'src="https://example.org/real.png">'
        )
        _, _, assets, _ = wp_sync.prepare(self.folder)
        self.assertEqual(
            list(assets),
            ["https://example.org/real.png"],
        )

    def test_http_remote_image_rejected(self):
        self.write_html('<img src="http://example.com/pic.png">')
        with self.assertRaises(wp_sync.SyncError):
            wp_sync.prepare(self.folder)

    def test_full_document_keeps_head_css_and_body(self):
        self.write_html(
            '<!doctype html><html><head><style>.chapter19-wide{width:90%}</style>'
            '</head><body><div class="chapter19-wide"><p>본문</p></div></body></html>'
        )
        _, result, _, _ = wp_sync.prepare(self.folder)
        self.assertIn("<style>", result)
        self.assertIn("본문", result)
        self.assertNotIn("<!doctype", result.lower())

    def test_media_cache_survives_post_failure(self):
        self.write_html(
            '<img src="data:image/png;base64,' +
            base64.b64encode(b"\x89PNG\r\n\x1a\nsample").decode() + '">'
        )
        fake = FakeWordPress()
        original_request = fake.request

        def fail_once(method, route, payload=None):
            raise wp_sync.SyncError("simulated server rejection")

        fake.request = fail_once
        with self.assertRaises(wp_sync.SyncError):
            wp_sync.synchronize(self.folder, fake, True)
        self.assertEqual(len(fake.uploads), 1)
        self.assertEqual(len(wp_sync.load_state(self.folder)["media"]), 1)
        fake.request = original_request
        self.assertEqual(wp_sync.synchronize(self.folder, fake, True), "create")
        self.assertEqual(len(fake.uploads), 1)


if __name__ == "__main__":
    unittest.main()
