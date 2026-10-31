#!/usr/bin/env python3
"""Folder-based WordPress draft synchronization (Python standard library only).

Default is an offline dry run. --apply is required for any WordPress mutation.
Only drafts can be updated. Existing drafts without local state require
--adopt-draft; published posts are never overwritten.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wp_check import SameOriginRedirect, load_env, rest_base

ROOT = Path(__file__).resolve().parent
POSTS_DIR = ROOT / "posts"
IMAGE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 100
IMAGE_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
SRC_ATTR = re.compile(r"(?P<prefix>\bsrc\s*=\s*)(?P<q>['\"])(?P<url>.*?)(?P=q)", re.IGNORECASE | re.DOTALL)
BODY = re.compile(r"<body\b[^>]*>(.*?)</body\s*>", re.IGNORECASE | re.DOTALL)
HEAD = re.compile(r"<head\b[^>]*>(.*?)</head\s*>", re.IGNORECASE | re.DOTALL)
STYLES = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class SyncError(Exception):
    """Safe diagnostic exception; never contains authentication headers."""


def read_source(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    body = BODY.search(raw)
    if body is None:
        return raw
    head = HEAD.search(raw)
    styles = STYLES.findall(head.group(1)) if head else []
    return "\n".join(styles + [body.group(1)])


def metadata(folder: Path) -> dict:
    path = folder / "post.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SyncError("post.json은 JSON 객체여야 합니다.")
    title = data.get("title")
    slug = data.get("slug")
    excerpt = data.get("excerpt", "")
    if not isinstance(title, str) or not title.strip():
        raise SyncError("post.json에 제목(title)이 필요합니다.")
    if not isinstance(slug, str) or SLUG.fullmatch(slug) is None:
        raise SyncError("slug는 소문자·숫자·하이픈만 사용하세요.")
    if not isinstance(excerpt, str):
        raise SyncError("excerpt는 문자열이어야 합니다.")
    return {"title": title.strip(), "slug": slug, "excerpt": excerpt}


def image_info(url: str, folder: Path) -> dict | None:
    """Return a content-addressed asset; external HTTPS URLs remain unchanged."""
    url = html.unescape(url)
    if url.startswith("https://"):
        return None
    if url.startswith("http://"):
        raise SyncError("외부 이미지는 HTTPS URL만 지원합니다.")
    if url.startswith("data:"):
        match = re.fullmatch(
            r"data:(image/(?:jpeg|png|gif|webp));base64,(.*)",
            url, re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise SyncError("지원하지 않는 data: 이미지입니다. PNG/JPEG/GIF/WebP만 지원")
        mime = match.group(1).lower()
        encoded = re.sub(r"\s+", "", match.group(2))
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise SyncError("Base64 이미지 데이터가 올바르지 않습니다.") from None
    else:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme or parts.netloc or url.startswith(("/", "\\")):
            raise SyncError("이미지 주소는 HTTPS 또는 게시글 폴더 내 상대경로여야 합니다.")
        relative = urllib.parse.unquote(parts.path)
        asset_path = (folder / relative).resolve()
        if not asset_path.is_relative_to(folder.resolve()) or not asset_path.is_file():
            raise SyncError("이미지 경로가 게시글 폴더 밖이거나 파일이 없습니다.")
        mime = mimetypes.guess_type(asset_path.name)[0]
        if mime not in IMAGE_EXT:
            raise SyncError("지원하지 않는 로컬 이미지 형식입니다: " + asset_path.suffix)
        if asset_path.stat().st_size > MAX_IMAGE_BYTES:
            raise SyncError("이미지 한 개가 20MB를 초과합니다.")
        data = asset_path.read_bytes()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise SyncError("이미지가 비어 있거나 20MB를 초과합니다.")
    digest = hashlib.sha256(data).hexdigest()
    return {"hash": digest, "bytes": data, "mime": mime, "ext": IMAGE_EXT[mime]}


def prepare(folder: Path) -> tuple[dict, str, dict, str]:
    meta = metadata(folder)
    content = read_source(folder / "post.html")
    assets: dict[str, dict | None] = {}
    for tag in IMAGE_TAG.finditer(content):
        match = SRC_ATTR.search(tag.group(0))
        if match:
            src = match.group("url")
            if src not in assets:
                if len(assets) >= MAX_IMAGES:
                    raise SyncError("이미지는 최대 100개까지 지원합니다.")
                assets[src] = image_info(src, folder)
    # Source and image bytes determine whether a draft needs updating.
    digest = hashlib.sha256()
    digest.update(content.encode("utf-8"))
    digest.update(json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for src in sorted(assets):
        asset = assets[src]
        if asset is not None:
            digest.update(asset["hash"].encode("ascii"))
    return meta, content, assets, digest.hexdigest()


def load_state(folder: Path) -> dict:
    path = folder / ".wp-state.json"
    if not path.exists():
        return {"post_id": None, "fingerprint": "", "media": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("media"), dict):
        raise SyncError("상태 파일 .wp-state.json이 손상되었습니다.")
    post_id = data.get("post_id")
    if post_id is not None and (type(post_id) is not int or post_id <= 0):
        raise SyncError("상태 파일의 post_id가 잘못되었습니다.")
    return data


def save_state(folder: Path, state: dict) -> None:
    """Atomically persist uploaded media immediately, including before post creation."""
    destination = folder / ".wp-state.json"
    fd, temp = tempfile.mkstemp(prefix=".wp-state-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class WordPress:
    def __init__(self, config: dict[str, str]):
        for name in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
            if not config.get(name) or config[name].startswith("your_"):
                raise SyncError(".env 인증 설정이 누락되었습니다: " + name)
        self.base = rest_base(config["WP_URL"].strip()) + "/wp/v2/"
        secret = (config["WP_USER"] + ":" + config["WP_APP_PASSWORD"]).encode("utf-8")
        self.authorization = "Basic " + base64.b64encode(secret).decode("ascii")
        self.opener = urllib.request.build_opener(SameOriginRedirect())

    def request(self, method: str, route: str, payload=None, raw=None, headers=None):
        url = self.base + route
        req_headers = {
            "Authorization": self.authorization,
            "Accept": "application/json",
            "User-Agent": "wordpress-study-publisher/0.1",
        }
        if headers:
            req_headers.update(headers)
        if payload is not None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req_headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            url, data=raw, headers=req_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                output = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise SyncError(
                f"WordPress HTTP {exc.code}. 권한, 본문 제한 또는 서버 설정을 확인하세요."
            ) from None
        except urllib.error.URLError:
            raise SyncError("WordPress 연결 실패. DNS, TLS 및 서버 주소를 확인하세요.") from None
        if len(output) > 8 * 1024 * 1024:
            raise SyncError("WordPress 응답이 지나치게 큽니다.")
        try:
            return json.loads(output.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SyncError("WordPress가 JSON 응답을 반환하지 않았습니다.") from None

    def by_slug(self, slug: str) -> list[dict]:
        query = urllib.parse.urlencode({
            "slug": slug, "context": "edit", "status": "any", "per_page": "100"
        })
        result = self.request("GET", "posts?" + query)
        if not isinstance(result, list):
            raise SyncError("원격 게시글 목록 형식이 잘못되었습니다.")
        return [post for post in result if post.get("slug") == slug]

    def by_id(self, post_id: int) -> dict:
        result = self.request("GET", f"posts/{post_id}?context=edit")
        if not isinstance(result, dict):
            raise SyncError("원격 게시글 조회 실패")
        return result

    def upload(self, name: str, asset: dict) -> str:
        response = self.request(
            "POST", "media",
            raw=asset["bytes"],
            headers={
                "Content-Disposition": 'attachment; filename="' + name + '"',
                "Content-Type": asset["mime"],
            },
        )
        url = response.get("source_url") if isinstance(response, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise SyncError("WordPress 미디어 업로드 결과의 HTTPS URL이 없습니다.")
        return url


def synchronize(
    folder: Path, wp: WordPress | None, apply: bool,
    adopt_draft: bool = False, force: bool = False,
) -> str:
    if not folder.is_dir():
        raise SyncError("게시글 폴더가 없습니다: " + folder.name)
    meta, content, assets, fingerprint = prepare(folder)
    state = load_state(folder)
    new_assets = {a["hash"] for a in assets.values() if a is not None}
    changed = state.get("fingerprint") != fingerprint
    print(
        f"[CHECK] {folder.name}: slug={meta['slug']}, "
        f"이미지 {len(new_assets)}개, 로컬변경={'예' if changed else '아니오'}"
    )
    if not apply:
        print("[DRY-RUN] 서버에 연결하거나 변경하지 않았습니다.")
        return "dry-run"
    if wp is None:
        raise SyncError("실제 적용에는 WordPress 연결이 필요합니다.")

    # Never publish or overwrite a public post, including explicitly adopted posts.
    post_id = state.get("post_id")
    if post_id:
        remote = wp.by_id(post_id)
        if remote.get("slug") != meta["slug"]:
            raise SyncError("상태 파일과 원격 게시글 slug가 다릅니다. 중단합니다.")
        if remote.get("status") != "draft":
            raise SyncError("임시글이 아닌 원격 게시글은 수정하지 않습니다.")
    else:
        matches = wp.by_slug(meta["slug"])
        if matches:
            if len(matches) != 1 or not adopt_draft:
                raise SyncError(
                    "동일한 slug의 게시글이 이미 존재합니다. "
                    "기존 임시글을 연결하려면 --adopt-draft를 명시하세요."
                )
            remote = matches[0]
            if remote.get("status") != "draft":
                raise SyncError("기존 게시글이 임시글이 아니므로 연결할 수 없습니다.")
            post_id = remote.get("id")
            if type(post_id) is not int or post_id <= 0:
                raise SyncError("원격 게시글 ID가 올바르지 않습니다.")
            print(f"[ADOPT] 기존 임시글 연결: ID={post_id}")

    if post_id and not changed and not force:
        print("[SKIP] 로컬 내용 변경이 없어 업로드하지 않습니다.")
        return "skip"

    def replace_tag(found: re.Match) -> str:
        tag = found.group(0)
        attr = SRC_ATTR.search(tag)
        if not attr:
            return tag
        src = attr.group("url")
        asset = assets[src]
        if asset is None:
            return tag
        digest = asset["hash"]
        url = state["media"].get(digest)
        if not url:
            filename = f"{meta['slug']}-{digest[:16]}{asset['ext']}"
            url = wp.upload(filename, asset)
            state["media"][digest] = url
            save_state(folder, state)
            print(f"[MEDIA] 업로드 완료: {filename}")
        escaped_url = html.escape(url, quote=True)
        start, end = attr.span("url")
        return tag[:start] + escaped_url + tag[end:]

    final_content = IMAGE_TAG.sub(replace_tag, content)
    payload = {
        "title": meta["title"],
        "slug": meta["slug"],
        "excerpt": meta["excerpt"],
        "content": final_content,
    }
    if post_id:
        saved = wp.request("POST", f"posts/{post_id}", payload=payload)
        action = "UPDATE"
    else:
        payload["status"] = "draft"
        saved = wp.request("POST", "posts", payload=payload)
        action = "CREATE"
    if not isinstance(saved, dict) or type(saved.get("id")) is not int:
        raise SyncError("WordPress가 게시글 저장 결과를 반환하지 않았습니다.")
    if saved.get("status") != "draft" or saved.get("slug") != meta["slug"]:
        raise SyncError("저장 후 상태·slug가 예상과 달라 확인이 필요합니다.")
    if post_id and saved["id"] != post_id:
        raise SyncError("수정된 게시글 ID가 예상과 다릅니다.")

    state["post_id"] = saved["id"]
    state["fingerprint"] = fingerprint
    save_state(folder, state)
    print(f"[{action}] 임시글 ID={saved['id']} 저장 완료")
    print("[DONE] WordPress 관리화면에서 HTML·이미지·CSS를 확인하세요.")
    return action.lower()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="게시글 폴더의 HTML/그림을 WordPress 임시글에 동기화"
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("folder", nargs="?", help="posts/ 아래 게시글 폴더명")
    selector.add_argument("--all", action="store_true", help="모든 폴더 동기화")
    parser.add_argument("--apply", action="store_true", help="실제 이미지/임시글 업로드")
    parser.add_argument("--adopt-draft", action="store_true", help="기존 동일 slug의 임시글 연결 허용")
    parser.add_argument("--force", action="store_true", help="변경되지 않은 임시글도 다시 쓰기")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    if args.all:
        folders = sorted(
            f for f in POSTS_DIR.iterdir()
            if f.is_dir() and (f / "post.json").exists()
        ) if POSTS_DIR.is_dir() else []
    else:
        if Path(args.folder).name != args.folder or args.folder.startswith("."):
            parser.error("게시글 폴더명만 입력하세요.")
        folders = [POSTS_DIR / args.folder]
    if not folders:
        print("[DONE] 게시글 폴더가 없습니다.")
        return 0

    failures = 0
    try:
        wp = WordPress(load_env(args.env)) if args.apply else None
    except (SyncError, ValueError) as exc:
        print("[FAIL] " + str(exc), file=sys.stderr)
        return 1
    for folder in folders:
        try:
            synchronize(folder, wp, apply=args.apply,
                        adopt_draft=args.adopt_draft, force=args.force)
        except (SyncError, ValueError, OSError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"[FAIL] {folder.name}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
