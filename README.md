# Flexcil for Codex

![코드 리뷰와 PDF 문서가 잉크 선을 따라 태블릿의 필기로 이어지는 Flexcil for Codex 커버](docs/assets/flexcil-cover.png)

Codex에서 문서를 만들고, Flexcil에 정리하고, 태블릿에 쓴 필기를 다시 읽습니다.

Google Drive로 동기화하는 Flexcil 도서관을 연결하는 비공식 Codex 플러그인입니다. 다른 스킬이 만든 PDF를 새 문서로 등록하고 폴더에 배치하며, 기존 노트와 페이지를 편집합니다. 개인 연결 정보와 백업은 각 사용자의 컴퓨터에 보관합니다.

**[설치](#설치) · [사용 안내](docs/usage.md) · [지원 범위](docs/support.md) · [개발과 기여](CONTRIBUTING.md) · [릴리스](https://github.com/jeonghyeon-net/flexcil-codex-plugin/releases)**

```mermaid
flowchart LR
    A[PR · 자료 · 아이디어] --> B[Codex + 문서 작성 스킬]
    B -->|표준 PDF| C[Flexcil 문서 · 폴더]
    C -->|Google Drive 동기화| D[태블릿에서 읽고 필기]
    D -->|필기 회수| B
```

## 이렇게 요청하세요

> 이 PR을 리뷰 문서로 만들어서 개발/리뷰 폴더에 넣고 알려줘.

> 회의 노트에 적은 내용을 읽고, 마지막에 요약 페이지를 추가해줘.

> 프로젝트 폴더를 보여주고, 이 문서를 보관/완료로 옮겨줘.

문서 작성 스킬은 내용과 레이아웃을, Flexcil 플러그인은 문서 등록·폴더·페이지·동기화를 담당합니다. 전달 형식은 **PDF + 제목 + Flexcil 폴더 경로**입니다.

## 지원 환경

| 항목 | 조건 |
| --- | --- |
| Codex | 로컬 플러그인과 스킬을 사용할 수 있는 Codex 환경 |
| Flexcil | Google Drive 동기화를 설정한 도서관. Android 실제 왕복 검증 완료 |
| 컴퓨터 | macOS에서 직접 로그인·신규 문서 등록 검증. Windows/Linux 자동 OAuth 콜백은 미구현 |
| 실행 환경 | Python 3.10 이상. 첫 설정에서 의존성을 별도 환경에 설치 |
| 첫 연결 | 사용자별 OAuth 설정과 Google 동의. Mac 콜백 수신기 빌드에는 Swift 명령행 도구 필요 |

컴퓨터의 Flexcil 앱, 태블릿 디버깅 연결, 태블릿 보조 앱은 필요하지 않습니다. iOS 쓰기는 아직 검증하지 않았습니다.

## 설치

[릴리스](https://github.com/jeonghyeon-net/flexcil-codex-plugin/releases)에서 같은 버전의 ZIP과 `.sha256`을 받습니다. macOS에서는 내려받은 디렉터리에서 체크섬을 확인하고 압축을 풉니다. 다음은 `v0.1.0`의 예입니다.

```sh
shasum -a 256 -c flexcil-codex-plugin-v0.1.0.zip.sha256
unzip flexcil-codex-plugin-v0.1.0.zip
cd flexcil-codex-plugin
codex plugin marketplace add .
codex plugin add flexcil-codex-plugin@personal
```

카탈로그 이름은 `personal`입니다. 같은 이름의 다른 카탈로그가 이미 있다면 [설치 충돌 안내](docs/usage.md)를 먼저 확인하세요. 소스로 설치하는 방법과 Windows/Linux 체크섬 확인도 같은 문서에 있습니다.

설치 후 **새 Codex 채팅**에서 다음과 같이 요청합니다.

> Flexcil 연결 설정해줘.

설정 스킬이 동기화 폴더 선택, 실행 환경 준비, Google 로그인, 테스트 문서 왕복을 안내합니다. OAuth 설정은 사용자가 제공한 로컬 설정이나 Flexcil APK/XAPK에서 읽습니다. APK는 실행하지 않습니다. 사용자가 Google 동의를 마치고 테스트 문서에 한 번 필기하면 이후에는 일반 문서를 바로 만들 수 있습니다.

## 현재 확인된 범위

**새 PDF 문서 생성 → 폴더 배치 → Android 표시 → 새 필기 회수**를 실제 태블릿으로 확인했습니다. 기존 노트의 텍스트 추가도 왕복 검증했습니다. 도서관의 이름 변경·이동·휴지통·복원은 실제 Drive 쓰기와 재다운로드를 확인했습니다.

전체 Flexcil 기능 구현은 진행 중입니다. 문서 전체 복제, 네이티브 표지·템플릿, 전체 객체 편집과 정밀 렌더링, 영구 삭제는 남아 있습니다. [지원 범위와 완성 기준](docs/support.md)에서 기능별 구현·검증 상태를 확인할 수 있습니다.

## 문서

| 문서 | 읽는 목적 |
| --- | --- |
| [사용 안내](docs/usage.md) | 설치, 연결, 요청 예시, 업데이트, 문제 해결 |
| [지원 범위와 완성 기준](docs/support.md) | 기능별 지원·검증 수준과 남은 작업 |
| [개발과 기여 안내](CONTRIBUTING.md) | 실행 환경, 검사, 패키징, 릴리스 절차 |
| [에이전트 작업 지침](AGENTS.md) | 코드 변경 시 지킬 규칙과 문서 선택 |
| [아키텍처](docs/architecture.md) | 코드 책임, 식별자, 작업 상태와 복구 |
| [설계 결정 기록](docs/decisions/README.md) | PDF 전달, 직접 연결, 변경 보호를 선택한 이유 |
| [검증 기록](docs/verification.md) | 실제 기기 시험과 자동 검사에서 확인한 사실 |
| [개인정보와 복구](docs/privacy-and-recovery.md) | 개인 설정·토큰·백업 위치와 중단된 작업 처리 |

문제 제보와 기능 제안은 [GitHub Issues](https://github.com/jeonghyeon-net/flexcil-codex-plugin/issues)에 남겨 주세요. 개인 노트나 인증 정보를 첨부하지 마세요.

[MIT License](LICENSE). Flexcil 및 Google의 공식 제품이나 제휴 플러그인이 아닙니다.
