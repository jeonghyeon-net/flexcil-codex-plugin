# Flexcil for Codex

Codex에서 Flexcil 도서관과 노트를 다루는 플러그인입니다. 컴퓨터에 Flexcil 앱을 설치하지 않고, 연결된 Google Drive와 로컬 파일 엔진을 사용합니다.

**검증된 핵심 흐름:** 기존 노트 편집뿐 아니라 **새 PDF 문서 생성 → 가상 폴더 배치 → Android 태블릿 표시 → 태블릿 필기 회수**까지 실제 기기로 확인했습니다. 새 문서 생성에 Flexcil 데스크톱 앱, 태블릿 디버깅, 태블릿 보조 앱이 필요하지 않습니다. 전체 객체 편집·렌더링 등 남은 항목은 아래에 명시합니다.

## 무엇을 할 수 있나요?

| 영역 | 구현된 작업 | 확인 수준 |
|---|---|---|
| 도서관 조회 | 폴더 트리, 문서 목록, 활성 문서/휴지통 구분, Drive 파일 매핑 | 실제 데이터 읽기 + 자동 테스트 |
| 도서관 편집 | 폴더 생성, 문서·폴더 이름 변경/이동, 휴지통/복원 | 로컬 자동 테스트; 폴더·이동 Drive 쓰기/재조회 확인 |
| 노트 읽기 | PDF 텍스트, 텍스트 객체, 페이지·객체 정보, 필기 이미지 | 실제 기기 필기 42개 회수 및 이미지 확인 |
| 노트 쓰기 | 텍스트 추가/변경, 객체 삭제, PDF/빈 페이지 추가, 페이지 삭제/재배치 | 텍스트 실제 기기 왕복; 나머지는 자동 테스트 |
| 산출물 | PNG 미리보기, 주석 포함 래스터 PDF, 원본 `.flx` 보존 | 자동 테스트 |
| 변경 보호 | 원본 백업, SHA-256 사전 비교, 차이 보고서, 재다운로드 검증, 복구 후보 생성 | 자동 테스트 + 실제 Drive 왕복 |
| 신규 문서 | PDF 또는 빈 PDF 배경, 고유 문서/목록 UUID, 가상 폴더 경로 생성, 앱 전용 등록 | 새 PDF 문서 실제 태블릿 왕복 |
| 직접 연결 | PKCE 로그인, 토큰 갱신, 전체 목록 조회, 중단 후 이어 올리기 | 로그인·앱 전용 속성 조회·신규 업로드 실증 |
| 스킬 연결 | 다른 스킬의 표준 PDF를 기존 노트에 삽입하거나 새 문서로 등록 | 신규 문서 실제 태블릿 왕복 |

“노트를 읽고 설명 추가”, “이 문서를 개발/리뷰로 이동”, “이 PR을 문서로 만들어 개발/리뷰 폴더에 넣어줘” 같은 요청에 사용할 수 있습니다.

### 아직 자동으로 완료할 수 없는 작업

- **연결 조건:** 신규 등록은 Flexcil 전용 속성에 접근하는 직접 연결이 필요합니다. 일반 Codex Google Drive 연결이나 임의의 OAuth 클라이언트만으로는 충족되지 않습니다. `connection-probe`가 실제 접근을 검증합니다. 직접 연결이 없는 경우에만 `import-plan`의 수동 가져오기 경로를 사용합니다.
- **문서 복제·네이티브 템플릿:** 새 PDF 문서 생성과 별개로, 기존 문서의 전체 내부 참조 복제 및 노트 표지·템플릿 편집은 아직 구현하지 않았습니다.
- **완벽한 화면 재현:** PDF 배경과 기본 필기·텍스트를 렌더링합니다. 펜 굵기·압력·서체·줄바꿈은 근사치입니다. 이미지/도형/마스킹/링크 등 미지원 레이어는 원본에 보존하고 렌더 보고서에 생략을 표시합니다. `--strict`는 근사 또는 생략이 있으면 거절합니다.
- **영구 삭제, 공유 권한 변경, 음성 녹음 편집, 앱 설정, 전체 객체 종류 편집:** 자동화하지 않습니다. “모든 Flexcil 기능 지원”으로 표시하지 않습니다.
- **동시 편집:** 원본 비교와 단계별 재조회는 구현했지만 원자적 조건부 쓰기는 검증하지 않았습니다. 마지막 다운로드와 업로드 사이의 경쟁 조건이 남습니다. 여러 파일 갱신은 순서와 복구 기록으로 관리합니다.
- **iOS:** 실제 쓰기 검증이 없습니다. 계정별 테스트 노트 검증을 통과해야 일반 문서를 쓸 수 있습니다.
- **대규모 목록:** 직접 연결은 페이지네이션을 처리합니다. 커넥터 목록 제한에 걸린 결과를 전체 도서관으로 표시하거나, 누락/고아 파일을 추측해 삭제하지 않습니다.

## 설치와 개인 설정

이 저장소는 `.agents/plugins/marketplace.json`과 `plugins/flexcil-codex-plugin`을 포함합니다. 저장소를 로컬로 받아 Codex의 로컬 marketplace로 연결할 수 있습니다.

```sh
git clone git@github.com:jeonghyeon-net/flexcil-codex-plugin.git
cd flexcil-codex-plugin
codex plugin marketplace add .
codex plugin add flexcil-codex-plugin@personal
```

카탈로그 이름은 생성 도구 기본값인 `personal`입니다. 기존에 같은 이름의 다른 marketplace가 있다면 그 항목을 덮어쓰지 마세요. Codex의 marketplace 관리 기능으로 이름 충돌을 먼저 해소해야 합니다.

설치 후 새 채팅에서 **“Flexcil 연결 설정해줘”**라고 요청합니다. `flexcil-setup`이 Python 실행 환경, 동기화 폴더, 직접 Google 로그인, 계정별 테스트를 안내합니다. 공개 OAuth 설정은 로컬 설정 파일로 제공하거나, 사용자가 선택한 Flexcil APK/XAPK에서 읽어 옵니다. APK는 실행하거나 설치하지 않습니다. Mac에서는 로컬 OAuth 콜백 수신기를 구성하며 첫 빌드에 Swift 명령행 도구가 필요합니다. 이 수신기는 태블릿에 설치하는 앱이 아닙니다.

인증 화면의 앱 이름은 제공된 OAuth 설정의 이름으로 표시됩니다. 새 연결을 실제로 보관하고 사용하는 주체는 이 플러그인임을 안내한 후 사용자가 직접 동의합니다. 토큰이나 콜백 URL을 채팅으로 받지 않습니다. Windows/Linux의 자동 콜백 수신은 아직 구현하지 않았습니다.

공개 저장소에는 사용자 이메일, OAuth 토큰, 실제 폴더/문서 ID, 필기, 테스트 원본을 저장하지 않습니다. 개인 설정과 백업은 다음 위치에 저장합니다.

- macOS: `~/Library/Application Support/flexcil-codex`
- Linux: `$XDG_STATE_HOME/flexcil-codex` 또는 `~/.local/state/flexcil-codex`
- Windows: `%LOCALAPPDATA%/flexcil-codex`
- 별도 위치: `FLEXCIL_STATE_DIR`

`FLEXCIL_FONT`로 미리보기에 사용할 Unicode TTF/TTC 파일을 지정할 수 있습니다. 원본 Flexcil 파일의 서체 설정은 변경하지 않습니다.

## 다른 스킬과의 연결

```mermaid
flowchart LR
  A[사용자 요청 / PR / 자료] --> B[Codex와 문서 작성 스킬]
  B --> C[표준 PDF]
  C --> D[기존 노트의 페이지 삽입]
  C --> E[새 Flexcil 문서 구성]
  E --> F[직접 연결의 앱 전용 속성 검증]
  F --> G[새 Drive ID로 문서 등록]
  G --> H[Flexcil 폴더 생성 · 목록 갱신]
  D --> I[백업 · 변경 검사 · Drive 쓰기]
  H --> I
  I --> J[재다운로드 검증 · 결과 안내]
```

문서 작성 스킬은 PDF의 레이아웃과 내용을 결정합니다. Flexcil 엔진은 파일·페이지·객체·도서관 구조를 관리합니다. 필기를 해석하는 일은 렌더 이미지를 읽는 Codex가 담당합니다. 별도 원격 서버나 MCP 프로세스는 필요하지 않습니다. 직접 OAuth와 파일 전송은 플러그인의 명시적 연결 명령이 담당합니다.

## 로컬 개발

Python 3.10 이상이 필요합니다. 아래의 가상환경은 저장소 밖 또는 무시되는 작업 디렉터리에 만드세요.

```sh
python3 plugins/flexcil-codex-plugin/scripts/bootstrap.py --directory work/runtime
work/runtime/bin/python plugins/flexcil-codex-plugin/scripts/flexcil.py doctor
PYTHONPATH=plugins/flexcil-codex-plugin/src work/runtime/bin/python -m unittest discover -s tests -v
```

Windows에서는 `work/runtime/Scripts/python.exe`를 사용합니다. Codex가 제공하는 Python에 필요한 라이브러리가 있으면 `doctor` 확인 후 그대로 사용할 수 있습니다.

CLI 명세와 예제는 [commands.md](plugins/flexcil-codex-plugin/skills/flexcil/references/commands.md), 실제 업로드 순서는 [drive.md](plugins/flexcil-codex-plugin/skills/flexcil/references/drive.md)를 참고하세요.

## 문서

- [구현 구조와 책임](docs/architecture.md)
- [포맷과 동기화 조사](docs/format-and-sync.md)
- [검증 기록](docs/verification.md)
- [Android 생성·인증 경로와 대체 구현](docs/android-document-creation.md)
- [개인정보와 복구](docs/privacy-and-recovery.md)

Flexcil과 Google의 비공식 통합입니다. 원본 앱 바이너리, 역어셈블 코드, 개인 노트는 이 저장소에 포함하지 않습니다.
