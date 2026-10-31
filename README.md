# WordPress Study Publisher

WordPress REST API를 통해 개인 학습자료를 **임시글(draft)** 로 동기화하기 위한 저장소입니다.

## 운영 정책

- GitHub push 자체가 WordPress에 게시하지 않습니다.
- 작성된 HTML은 posts/<게시글폴더>/post.html, 메타데이터는 post.json으로 관리합니다.
- 최초 업로드는 임시글로 등록하고 사용자가 검토 후 발행합니다.
- 기존 공개글은 기본적으로 자동 수정하지 않습니다.
- WordPress Application Password를 사용하며 일반 로그인 비밀번호는 사용하지 않습니다.
- 인증정보가 담긴 .env 및 실행 상태 파일은 Git에서 제외합니다.
- 본문은 `<div class="chapter19-wide">` 형식을 유지합니다.
- **현재 저장소는 Public**입니다. 개인자료나 인증정보를 커밋하지 마세요.

## 현재 구현 진행상태

1. **완료**: 저장소 구조, .gitignore, .env.example
2. **코드 등록 / 실제 연결 검증 대기**: wp_check.py (읽기 전용 REST API 진단)
3. 예정: wp_sync.py (그림 업로드, 중복 방지, 임시글 등록·갱신)
4. 예정: WordPress에서 HTML·그림·수식·Chapter 19 CSS 실제 표시 확인

## 디렉터리

```text
wordpress_post/
├── .env.example
├── .gitignore
├── README.md
├── wp_check.py
├── tests/
│   └── test_wp_check.py
└── posts/
    └── example/
        ├── post.json
        └── post.html
```

실제 학습자료는 posts/chapter32-1/ 등 새 폴더를 만들고, 그림은 images/ 하위에 보관할 예정입니다.

## 2단계: 읽기 전용 WordPress 연결 진단

Python 3.10 이상에서 외부 라이브러리 없이 실행합니다.

### 1) 공개 API 연결 확인 (인증 필요 없음)

```bash
git clone https://github.com/now0930/wordpress_post.git
cd wordpress_post
WP_URL=https://now0930.pe.kr/wordpress python3 wp_check.py --public
```

### 2) 로컬 인증 설정

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

WordPress 관리자 → 사용자 → 프로필 → 애플리케이션 비밀번호에서 발급한 암호를 사용하세요.

```ini
WP_URL=https://now0930.pe.kr/wordpress
WP_USER=실제_워드프레스_사용자명
WP_APP_PASSWORD=발급받은_애플리케이션_비밀번호
```

실제 설치 주소가 다르면 WP_URL을 수정하세요. **.env와 비밀번호를 GitHub나 채팅에 보내지 마세요.**

### 3) 인증 및 권한 확인

```bash
python3 wp_check.py
python3 -m unittest discover -s tests -v
```

wp_check.py는 GET 요청만 수행합니다. 확인 대상은 REST API 색인, 게시글·미디어 API, 사용자 인증,
사용자 권한(edit_posts, upload_files)입니다. WordPress 게시글과 이미지는 **변경하지 않습니다**.

| 결과 | 우선 점검 |
|---|---|
| HTTP 401 | 사용자명, Application Password, Basic Auth 전달 설정 |
| HTTP 403 | WordPress 사용자 권한 또는 보안 플러그인 |
| HTTP 404 | WP_URL 설치 경로 |
| 연결 실패 | DNS, HTTPS 인증서, 방화벽 |
| JSON 오류 | 웹 서버가 HTML 로그인/차단 페이지를 반환했는지 |

일부 서버는 권한(capabilities) 필드를 숨길 수 있습니다. 이 경우 인증 성공과 게시 권한 확인을 구분합니다.

## 안전 원칙

- 이 저장소는 소스 및 게시 원본의 버전 관리용입니다.
- 자동 발행과 삭제 기능은 현재 구현하지 않았습니다.
- 공개 글이 자동으로 갱신되는 기능은 연결 및 표시 검증 이후 별도 정책으로 도입합니다.
- 인증 정보는 서버 로컬에만 보관하세요.
