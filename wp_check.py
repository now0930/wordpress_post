#!/usr/bin/env python3
"""Read-only WordPress REST API connectivity and permission check.

No WordPress posts, media or settings are written by this script.
Requires only the Python standard library.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PLACEHOLDERS = {
    "your_wordpress_username",
    "your_application_password",
}


class ProbeError(Exception):
    """An expected diagnostic failure without credential disclosure."""


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward credentials to a different scheme, host or port."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urlsplit(req.full_url)
        new = urlsplit(newurl)
        if (old.scheme, old.hostname, old.port) != (
            new.scheme, new.hostname, new.port
        ):
            raise ProbeError(
                "서버가 다른 주소로 리디렉션합니다. "
                "WP_URL과 HTTPS 주소를 직접 확인하세요."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_env(path: Path) -> dict[str, str]:
    """Load simple KEY=VALUE assignments; process environment has priority."""
    result: dict[str, str] = {}
    if path.is_file():
        for line_number, raw in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        ):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not key.strip():
                raise ProbeError(
                    f".env {line_number}행 형식 오류 (KEY=VALUE 필요)"
                )
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            result[key] = value

    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in os.environ:
            result[key] = os.environ[key]
    return result


def rest_base(site_url: str) -> str:
    """Accept an HTTPS WordPress installation URL, not an API endpoint."""
    parts = urlsplit(site_url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ProbeError("WP_URL은 사용자정보·쿼리 없는 HTTPS 설치 주소여야 합니다.")

    path = parts.path.rstrip("/")
    if path.endswith("/wp-json") or "/wp-json/" in path:
        raise ProbeError("WP_URL에는 /wp-json을 포함하지 마세요.")

    return site_url.rstrip("/") + "/wp-json"


def get_json(url: str, authorization: str | None, timeout: float) -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "wordpress-study-publisher-readonly/0.1",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, method="GET", headers=headers)
    opener = urllib.request.build_opener(SameOriginRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Do not echo request headers, credentials, or untrusted response bodies.
        error_codes = {
            401: "인증 실패: 사용자명과 Application Password를 확인하세요.",
            403: "접근 거부: 계정 권한 또는 보안 플러그인을 확인하세요.",
            404: "REST API를 찾지 못했습니다. WP_URL 경로를 확인하세요.",
        }
        detail = error_codes.get(exc.code, "서버의 HTTP 응답을 확인하세요.")
        raise ProbeError(f"HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ProbeError(
            "연결 실패: DNS, HTTPS 인증서, 방화벽 또는 WP_URL을 확인하세요."
        ) from None
    except TimeoutError:
        raise ProbeError("연결 시간 초과: 서버 및 네트워크 상태를 확인하세요.") from None

    if len(content) > MAX_RESPONSE_BYTES:
        raise ProbeError("REST API 응답 크기가 제한을 초과했습니다.")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ProbeError("서버가 JSON을 반환하지 않았습니다.") from None
    if not isinstance(parsed, dict):
        raise ProbeError("서버 JSON 형식이 예상과 다릅니다.")
    return parsed


def check(config: dict[str, str], public_only: bool, timeout: float) -> None:
    url = config.get("WP_URL", "").strip()
    if not url:
        raise ProbeError("WP_URL이 없습니다. .env.example을 .env로 복사하세요.")
    base = rest_base(url)

    print("[CHECK] 공개 REST API 읽기")
    index = get_json(base + "/", authorization=None, timeout=timeout)
    routes = index.get("routes")
    if not isinstance(routes, dict):
        raise ProbeError("WordPress REST API 색인을 확인하지 못했습니다.")
    print("[PASS] REST API 색인 응답")

    required_routes = ("/wp/v2/posts", "/wp/v2/media", "/wp/v2/users/me")
    missing = [route for route in required_routes if route not in routes]
    if missing:
        raise ProbeError("필수 API 라우트가 없습니다: " + ", ".join(missing))
    print("[PASS] 게시글·미디어·사용자 조회 API 확인")

    if public_only:
        print("[SKIP] 인증 테스트 생략 (--public)")
        print("[DONE] 읽기 전용 진단 완료. 변경사항 없음.")
        return

    username = config.get("WP_USER", "").strip()
    password = config.get("WP_APP_PASSWORD", "").strip()
    if not username or not password or username in PLACEHOLDERS or password in PLACEHOLDERS:
        raise ProbeError(
            ".env의 WP_USER와 WP_APP_PASSWORD를 실제 값으로 설정하세요. "
            "비밀번호를 채팅이나 GitHub에 붙여넣지 마세요."
        )
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    authorization = "Basic " + token

    print("[CHECK] Application Password 인증 (GET /users/me)")
    me = get_json(
        base + "/wp/v2/users/me?context=edit",
        authorization=authorization,
        timeout=timeout,
    )
    if not isinstance(me.get("id"), int):
        raise ProbeError("인증 응답에 사용자 ID가 없습니다.")

    print("[PASS] Application Password 인증 성공")
    capabilities = me.get("capabilities")
    if isinstance(capabilities, dict):
        if not capabilities.get("edit_posts"):
            raise ProbeError(
                "계정의 edit_posts 권한이 없습니다. 게시글 편집 권한을 확인하세요."
            )
        if not capabilities.get("upload_files"):
            raise ProbeError(
                "계정의 upload_files 권한이 없습니다. 미디어 업로드 권한을 확인하세요."
            )
        print("[PASS] edit_posts · upload_files 권한 확인")
    else:
        print(
            "[WARN] 사용자 capabilities가 응답에 없어 "
            "게시·업로드 권한은 확인되지 않았습니다."
        )

    print("[DONE] 읽기 전용 진단 완료. 게시글이나 이미지를 변경하지 않았습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WordPress REST API 읽기 전용 연결 진단"
    )
    parser.add_argument(
        "--public", action="store_true",
        help="인증정보 없이 공개 REST API 상태만 확인",
    )
    parser.add_argument(
        "--env", type=Path, default=ROOT / ".env",
        help="로컬 인증 설정 파일 (기본: 스크립트 폴더의 .env)",
    )
    parser.add_argument(
        "--timeout", type=float, default=12.0,
        help="요청당 제한 시간(초)",
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout은 1~120초 사이여야 합니다.")
    try:
        check(load_env(args.env), public_only=args.public, timeout=args.timeout)
        return 0
    except ProbeError as exc:
        print("[FAIL] " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
