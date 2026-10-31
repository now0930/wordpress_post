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


## 3단계: HTML·그림 임시글 동기화

`wp_sync.py`는 기본적으로 **오프라인 미리보기**만 수행합니다. `--apply`가 없으면 WordPress에 요청하지 않습니다.

### 게시글별 입력 파일

```text
posts/chapter32-1/
├── post.json        # 제목 · slug · 요약
├── post.html        # Chapter 19 형식 학습 본문
└── images/          # 본문에서 상대경로로 참조하는 이미지
    └── graph.png
```

`post.json` 예시:

```json
{
  "title": "Chapter 32.1 위험장소와 방폭 보호대책",
  "slug": "chapter32-1-protective-measures",
  "excerpt": "위험장소, 폭발한계, 점화에너지, 본질안전 방폭"
}
```

`post.html`의 그림은 `<img src="images/graph.png">`처럼 폴더 안의 **상대경로**를 사용할 수 있습니다.
원본 HTML에 들어 있는 PNG/JPEG/GIF/WebP의 Base64 `data:image/...;base64,...`도 자동으로 미디어 라이브러리에 업로드하고 URL로 교체합니다. 동일한 이미지 파일 내용은 해시로 중복 업로드를 방지합니다.
원격 HTTPS 그림은 링크를 유지하며 자동으로 다운로드하지 않습니다.

### 미리보기와 실행

먼저 2단계의 실제 인증 진단을 완료합니다.

```bash
cd ~/wordpress_post
git pull --ff-only

# 실제 WordPress 인증·게시/미디어 권한 확인 (GET만 수행)
python3 wp_check.py

# WordPress에 연결하지 않는 미리보기
python3 wp_sync.py example

# 새로운 예제 게시글을 WordPress 임시글로 실제 등록
python3 wp_sync.py example --apply

# 모든 게시글 폴더를 미리보기 (실제 게시 안 함)
python3 wp_sync.py --all

# 모든 폴더의 변경사항을 임시글에 반영
python3 wp_sync.py --all --apply

# 모든 오프라인 테스트 실행
python3 -m unittest discover -s tests -v
```

예제 폴더의 실제 업로드는 **`--apply`를 실행한 경우에만** 일어납니다.

### 중복 방지 및 수정 정책

- 신규 글 생성 시 `status=draft`를 지정합니다.
- 등록한 WordPress 글 ID와 이미지 URL 캐시는 해당 폴더의 `.wp-state.json`에 저장합니다. 이 파일은 Git에 올리지 않습니다.
- HTML이나 메타데이터가 바뀌면 **기존 임시글만** 수정합니다.
- 기존 WordPress 게시글의 slug와 충돌하면 기본적으로 멈춥니다.
- 다른 PC에서 등록한 **기존 임시글**을 연결하려면 신중히 확인 후 `python3 wp_sync.py example --apply --adopt-draft`를 실행합니다.
- **발행됨(publish), 예약됨(future), 비공개(private) 등 draft가 아닌 글은 수정하지 않습니다.** 자동 공개발행·삭제 기능도 없습니다.
- `--force`는 같은 임시글의 내용 재전송에만 사용하며, 공개글 수정 제한을 해제하지 않습니다.

### 현재 검증 범위

- **확인 완료**: 공개 REST API 호출, 최초 8개 진단 코드 오프라인 테스트 (사용자 서버 실행 결과).
- **GitHub 등록 완료 / 실행 검증 대기**: `wp_sync.py` 및 동기화 오프라인 테스트.
- **아직 미검증**: 사용자 WordPress Application Password 실제 인증, 미디어 업로드, 임시글 등록, CSS/그림 최종 렌더링.

주의: WordPress가 `<style>`을 제한하는 경우 페이지의 CSS가 제거될 수 있습니다. **본문 구조는 유지하고 Chapter 19 CSS는 WordPress의 추가 CSS 영역에 한 번 등록**하는 방식이 더 안정적입니다.


## 날짜_제목 ZIP 업로드 방식 (기본 작업 흐름)

학습자료를 만들면 **게시글마다 ZIP 하나**로 전달하고 사용자가 서버에서 압축을 해제해 업로드합니다.

- 폴더명: `posts/YYYYMMDD_제목/` (예: `posts/20261031_Chapter32-1_공정안전과_방폭/`)
- `post.json`: 게시글 제목·고유 slug·요약
- `post.html`: 기존 Chapter 19 CSS 본문. 그림 링크는 `images/파일명.jpg` 상대경로로 사전 설정
- `images/`: 본문에 사용되는 그림 전부. Base64 내장 이미지는 ZIP 제작 시 분리
- 별도로 제공할 수 있는 `chapter19.css`는 CSS 문제 확인용으로, `wp_sync.py`는 HTML 안의 스타일을 게시합니다.

ZIP의 최상위에는 `posts/` 폴더가 포함됩니다. **프로젝트 루트**에서 압축을 풉니다.

```bash
cd ~/wordpress_post
unzip /다운로드경로/파일명.zip -d .
python3 wp_sync.py '20261031_Chapter32-1_공정안전과_방폭'
python3 wp_sync.py '20261031_Chapter32-1_공정안전과_방폭' --apply
```

`--apply` 이전에는 WordPress에 저장하지 않습니다. 실행 시 상대경로 그림은 자동으로 WordPress 미디어 라이브러리에 업로드되고 링크가 교체됩니다. 최초 업로드는 **임시글**이며 수동으로 검토 후 공개합니다.

> 공개 GitHub 저장소에서는 교재 이미지·개인 필기 등 공개 권한이 확인되지 않은 자료를 무심코 commit하지 마세요. ZIP 파일은 별도로 내려받아 서버에 배치할 수 있습니다.
