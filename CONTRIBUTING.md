# 개발과 기여 안내

코드 책임은 [아키텍처](docs/architecture.md), 사용자 동작은 [사용 안내](docs/usage.md), 작업 규칙은 [AGENTS.md](AGENTS.md)를 기준으로 합니다.

## 개발 환경

Python 3.10 이상과 Git을 사용합니다. 저장소 루트에서 다음 명령을 실행합니다.

```sh
python3 plugins/flexcil-codex-plugin/scripts/bootstrap.py --directory work/runtime
work/runtime/bin/python tools/check.py
work/runtime/bin/python plugins/flexcil-codex-plugin/scripts/flexcil.py --help
```

Windows에서는 `work/runtime/Scripts/python.exe`를 사용합니다. `bootstrap.py`는 새 가상환경에 고정한 의존성을 설치하며 기존 환경을 덮어쓰지 않습니다. Codex가 제공한 Python에 필요한 라이브러리가 있으면 `doctor` 확인 후 사용할 수 있습니다.

`tools/check.py`는 개인 상태 대신 임시 디렉터리를 사용해 엔진 테스트, CLI 실행, 문서의 상대 파일 링크, Git 추적 파일의 패키징을 검사합니다. 링크 검사는 코드 블록 밖의 인라인 상대 링크를 대상으로 하며 외부 URL이나 제목 앵커의 유효성을 검사하지 않습니다. Git 추적 파일이 배포 대상이므로 새 공개 파일은 검토 후 `git add`해야 패키지에 포함됩니다.

## 저장소 구성

```text
.agents/plugins/marketplace.json    Codex 로컬 카탈로그
plugins/flexcil-codex-plugin/
  .codex-plugin/plugin.json         설치 메타데이터
  .app.json                        Google Drive 커넥터 선언
  skills/                          Flexcil 작업·연결 설정 지침
  scripts/                         실행 환경·CLI·Mac OAuth 수신기
  src/flexcil/                     포맷·편집·전송·복구 엔진
tests/                             합성 자료·실패 주입 테스트
tools/                             검사와 공개 소스 패키징
docs/                              사용법·설계·지원 범위·검증 기록
```

## 변경 제안

[GitHub Issues](https://github.com/jeonghyeon-net/flexcil-codex-plugin/issues)에서 버그와 기능을 제안할 수 있습니다. 버그에는 플러그인 버전, 컴퓨터 OS, 실제 확인한 Flexcil 버전, 재현 순서, 기대 결과와 실제 결과, 민감한 값을 제거한 오류 코드를 포함합니다. 버전을 모르면 추측하지 말고 미확인으로 표시합니다.

원본 노트·PDF·필기·동기화 목록·토큰·인증 URL·작업 기록을 공개 이슈에 올리지 않습니다. 재현 파일이 필요하면 합성 자료를 만듭니다. 변경 제안에는 해결하는 사용자 작업과 필요한 데이터 보존 조건을 설명합니다.

코드 변경은 기존 책임 안에서 수행합니다. 파일 포맷 관측과 가정을 구별하고 새로운 필드의 의미를 추측해 원본을 덮어쓰지 않습니다. 명령·스킬·사용 안내가 같은 기능을 설명해야 합니다. 원본 보존과 실패 복구를 검증할 수 있도록 작은 단위로 변경합니다.

## 검사

| 변경 | 필요한 확인 |
| --- | --- |
| 문서·스킬 안내 | 상대 링크·예제 명령·실제 지원 범위 일치 |
| 포맷·페이지·객체 | 입력 불변, 알 수 없는 항목 보존, 실패 시 무변경, 잘못된 식별자·참조 거절 |
| 도서관 변경 | 중복 제목, 폴더 순환, 문서·목록 제목 일치, 휴지통 순서 |
| 인증·전송 | state·만료·주소 검증, 중단 후 재시도, 외부 변경 보존, 재다운로드 |
| 신규 등록 | 별도 문서·목록·Drive ID, 초기 프로필 제한, 사용자 시험에서 기존 PDF와 새 필기 회수 |
| 배포 | 공개 파일 목록, 개인 자료 제외, 버전 일치, 압축 해제 후 CLI·Codex 설치 |

통합 검사는 `python tools/check.py`로 실행합니다. 특정 테스트만 실행하려면 `PYTHONPATH=plugins/flexcil-codex-plugin/src python -m unittest discover -s tests -p test_direct.py -v`처럼 범위를 지정할 수 있습니다. PowerShell에서는 먼저 `$env:PYTHONPATH = 'plugins/flexcil-codex-plugin/src'`를 설정합니다.

플러그인 메타데이터·스킬을 변경했다면 Codex의 `plugin-creator` 검증 도구와 `skill-creator` 검증 도구도 사용합니다. 설치된 개발본 갱신은 `plugin-creator`의 marketplace 이름 확인 → cachebuster 갱신 → `codex plugin add` 절차를 따릅니다. 개인 설정을 플러그인에 복사하거나 marketplace 파일을 임의로 고치지 않습니다.

검사·패키징·체크섬 생성·릴리스 업로드는 로컬에서 수행합니다. GitHub Actions와 원격 CI·릴리스 자동화를 추가하거나 활성화하지 않습니다. 로컬 검사와 실제 기기 확인을 구분해 날짜·대상 커밋·환경·결과·미검증 범위를 [검증 기록](docs/verification.md)에 남깁니다.

## 버전과 배포

엔진 버전은 `pyproject.toml`과 `src/flexcil/__init__.py`, 플러그인 기본 버전은 `plugin.json`에서 함께 관리합니다. Codex 캐시 구분용 `+codex.<값>`은 같은 기본 버전의 설치본을 구별합니다. Git 태그와 GitHub 릴리스 제목은 모두 `v<기본 버전>`만 사용합니다. 제품명이나 설명을 제목 앞뒤에 붙이지 않습니다.

릴리스마다 [CHANGELOG](CHANGELOG.md)에 사용자에게 달라지는 점과 알려진 한계를 기록합니다. 커밋된 변경을 원격에 반영한 뒤 깨끗한 체크아웃에서 검사하고 버전이 있는 ZIP을 만듭니다.

```sh
python tools/check.py
python tools/package.py dist/flexcil-codex-plugin-v0.1.0.zip
cd dist
shasum -a 256 -c flexcil-codex-plugin-v0.1.0.zip.sha256
cd ..
```

체크섬 검사는 ZIP이 있는 디렉터리에서 실행합니다. ZIP은 Git이 추적하는 공개 소스만 포함하며 `.sha256`을 함께 생성합니다. 원본 APK·개인 설정·노트·작업 기록·실행 환경은 포함하지 않습니다. 체크섬은 파일 무결성 검사이며 서명 검증이 아닙니다.

태그와 릴리스는 자동으로 생성하지 않습니다. 게시 권한과 인증된 GitHub CLI가 있는 유지관리자가 검증한 커밋에 주석 태그를 만들고, 검토한 릴리스 설명 파일과 패키지를 게시합니다.

```sh
git tag -a v0.1.0 -m 'v0.1.0'
git push origin v0.1.0
gh release create v0.1.0 --verify-tag \
  --title 'v0.1.0' \
  --notes-file work/release-notes.md \
  dist/flexcil-codex-plugin-v0.1.0.zip \
  dist/flexcil-codex-plugin-v0.1.0.zip.sha256
```

동일 버전이 이미 게시됐다면 파일을 몰래 교체하지 않고 수정 버전을 발행합니다. 게시 후 릴리스에서 ZIP과 체크섬을 다시 내려받아 비교하고, 압축 해제한 소스의 검사와 Codex 설치 상태를 확인합니다. 로컬 빌드·원격 게시·설치는 각각의 결과로 보고합니다.
