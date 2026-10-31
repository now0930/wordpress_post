# WordPress Study Publisher

개인 학습자료를 WordPress REST API의 **임시글(draft)** 로 동기화하기 위한 저장소입니다.

## 운영 정책

- 이 저장소는 **소스 및 학습자료 버전 관리용**입니다. Git push 자체가 WordPress에 게시하지 않습니다.
- 게시 원본은 `posts/<게시글폴더>/post.html` 및 `post.json`입니다.
- WordPress 게시 시 먼저 임시글로 등록하고 내용을 확인한 다음 수동 발행합니다.
- 게시된 기존 글을 자동으로 덮어쓰지 않는 것을 기본 정책으로 합니다.
- 사용자 비밀번호와 WordPress Application Password를 GitHub에 올리지 않습니다.
- 기존 학습자료의 Chapter 19 스타일: `<div class="chapter19-wide">`.

## 디렉터리

```text
wordpress_post/
├── .env.example                # 인증 설정 예시만 추적
├── .gitignore                  # 비밀정보 및 실행 상태 제외
├── README.md
└── posts/
    └── example/
        ├── post.json           # 제목, slug, 요약
        └── post.html           # Chapter 19 형식 본문
```

실제 게시글은 `posts/chapter32-1/`처럼 새 폴더를 만들어 관리합니다.
그림은 해당 게시글 아래 `images/`에 보관할 계획입니다.

## 로컬 인증정보 준비

```bash
cp .env.example .env
chmod 600 .env
```

`.env`에 WordPress의 **Application Password**를 입력합니다.
`WP_URL`은 실제 설치 경로/REST API 주소에 맞는지 확인해야 합니다.
`.env`는 `.gitignore`로 추적 대상에서 제외합니다.

> 이 단계에서는 아직 동기화 실행 코드와 WordPress 연결 검증이 완료되지 않았습니다.
> `posts/example`은 임시글 생성 검증을 위한 예제일 뿐 실제로 업로드하지 않았습니다.

## 구현 순서

1. **완료:** 저장소 접근권한과 기본 구조, 비밀정보 제외 설정
2. 예정: WordPress REST API 연결 확인과 읽기 전용 진단
3. 예정: 미디어 업로드 + 중복 생성 방지 + 임시글 등록/수정
4. 예정: WordPress에서 그림/수식/CSS 표시 검증 및 운영 자동화
