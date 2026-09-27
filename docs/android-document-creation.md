# Android 새 문서 생성 경로 추적

2026-09-28. Android 1.5.0.11 (822) 패키지를 실행하지 않고 DEX의 호출,
분기, 직렬화 필드 및 Retrofit 어노테이션을 대조했다. 외부 미러에서 받은
패키지이며 공식 서명과 대조하지 않았다. 아래 이름은 이 빌드의 난독화된
클래스/메서드 이름이다. APK와 역어셈블·디컴파일 결과는 배포하지 않는다.

## 확인한 호출 경로

| 단계 | 코드 위치 | 관측한 처리 |
|---|---|---|
| PDF에서 문서 생성 | `pg.h.g`, `pg.h.o0` | 문서 모델 생성, PDF를 문서 저장소의 `attachment/PDF/<key>`로 복사 |
| 문서 모델 | `jsonmodel.document.a` 생성자, `ef.a` | UUID, 버전 0.0.5, 제목, 생성/수정 시각, 문서 종류, PDF 암호 맵 생성 |
| 페이지 생성 | `pg.h.d0`, `jsonmodel.document.b` | PDF 페이지 크기·인덱스에서 페이지 모델을 만들고 객체/썸네일 저장 처리 |
| 문서 목록 항목 | `gf.c(document)` | 문서 UUID를 참조하는 별도 list-item UUID, 제목·종류·시각 생성 |
| 목록 저장 | `pg.h.g` → `in.c.b.add` → `pg.h.Q0` | 목록에 항목을 추가하고 `documents.list` 저장 및 변경 알림 |
| 신규 업로드 준비 | `jg.f.invokeSuspend` → `jg.a.c` | 업로드 파일 존재와 크기 확인. 5 MiB 이하 multipart, 초과 resumable 경로 |
| 메타데이터 생성 | `jg.a.e`, `jg.n.b`, `jg.n.c` | 파일 이름, 동기화 폴더, MIME, 표준 시각, 앱 전용 속성 구성 |
| HTTP 신규 생성 | `jg.r.b`의 Retrofit 어노테이션 | `POST https://www.googleapis.com/upload/drive/v3/files` |
| HTTP 기존 갱신 | `jg.r.e`의 Retrofit 어노테이션 | `PATCH https://www.googleapis.com/upload/drive/v3/files/{cloudId}` |

문서 생성 처리를 이해하려고 앱을 반드시 실행해야 하는 것은 아니다. 파일 구성과
서버 요청 형식을 정적 분석으로 확인했다. 기기에서 문서가 생성됐다는 검증은 별도다.

## 파일 데이터

문서 종류 enum `cf.j`: PDF `0`, 폴더 `1`, 노트 `4`, 플래너 `5`, 잠긴 PDF `99`.

- `info.key`: 문서 UUID. `version`: `0.0.5`.
- `info.name`, `type`, `createDate`, `modifiedDate`, `currentPage` 등은 직렬화된 필드다.
- **`info.attachments`의 값은 파일명이 아니라 PDF 암호다.**
  `document.a(name, type, password, copyright)`가 첨부 키에 암호를 저장한다.
  `pg.h.o0`는 같은 암호로 PDF를 연다. 암호 없는 PDF에는 빈 문자열을 쓴다.
- `pages.index`는 각 페이지 UUID와 PDF 첨부 키·페이지 인덱스를 연결한다.
- `documents.list`의 항목 `key`와 `document`는 서로 다른 UUID다.
- 문서 이름·파일 이름을 바꾸는 것만으로 이 식별자들이 함께 바뀌지 않는다.

기존 `insert_pdf`/`add_blank_page` 구현이 암호 맵에 파일명을 넣던 오류를 수정했다.
이전 테스트의 Drive 바이트 재검증은 그 필드의 의미까지 검증한 것이 아니었다.

## 신규 Drive 파일 메타데이터

다음은 값의 의미를 재구성한 예시다. 개인 폴더 ID, 문서 ID, 인증 값은 포함하지 않는다.

```json
{
  "name": "<document UUID>.flx",
  "parents": ["<sync root Drive folder ID>"],
  "mimeType": "application/com.flexcil.object",
  "createdTime": "<RFC 3339 UTC>",
  "modifiedTime": "<RFC 3339 UTC>",
  "appProperties": {
    "Flexcil": "MakeFromFlexcil",
    "SyncId": "<document UUID>",
    "Type": "Document",
    "createdTime": "<Unix seconds>",
    "modifiedTime": "<Unix seconds>"
  }
}
```

문서 ZIP과 문서 목록은 별도로 동기화된다. 여러 파일의 갱신은 원자적이지 않으므로
재시도 시 이미 적용된 단계를 확인하고, 외부 변경을 감지해야 한다.

## 수신 경로

`jg.e0.invokeSuspend`는 부모 폴더와 `trashed = false` 조건에 더해
`appProperties has {key = 'Flexcil' and value = 'MakeFromFlexcil'}`로 파일을 찾는다.
조회 후 `SyncId`별로 중복을 제거하므로 동일한 `SyncId`의 복사본은 독립 문서가 아니다.

`yf.v1.h`는 `.flx`의 신규/변경 여부를 판단하고 `SyncId`가 없으면 파일명을 쓴다.
이 대체 처리는 **앞선 속성 검색을 통과한 파일에만** 적용된다.

`yf.v1.a`는 다운로드 기록의 문서 키로 `Documents/<key>`에 압축을 푼다.
내부 `info.key`만 바꾸어 새 문서로 등록하는 근거는 발견하지 못했다.

## 인증과 대체 구현

`MainActivity.K`는 AppAuth의 인증 코드 흐름과 state/nonce, PKCE S256을 사용한다.
`ig.a`에는 Google 인증·토큰 URL, 공개 클라이언트 설정, 앱 콜백 URI,
`drive.file`과 `userinfo.email` 범위가 있다. `kg.e`는 토큰을 갱신하고
`jg.v$a`는 요청에 Bearer 인증을 붙인다. APK에 사용자 토큰이 들어 있는 것은 아니다.

Google의 `appProperties`는 호출 앱별로 제한된다. 다른 OAuth 프로젝트에서 같은
키를 작성해도 Flexcil의 검색 조건을 만족한다는 뜻이 아니다.

`direct.py`는 명시적으로 제공한 로컬 클라이언트 설정으로 PKCE 로그인·토큰 갱신·
Drive 조회·다운로드·중단 후 이어 올리기를 수행한다. `connection-probe`가 실제
동기화 폴더와 `documents.list`의 비공개 속성을 읽지 못하면 신규 등록을 중단한다.
클라이언트 ID만 바꾸면 해결된다고 가정하지 않는다. 기존 앱의 토큰 추출은 하지 않는다.

`creation.py`는 PDF 또는 빈 PDF 배경으로 새 문서와 별도 목록 항목을 만들고,
중첩 가상 폴더를 구성한다. Drive 파일 ID를 먼저 예약해 재시도 시 중복 생성을 피한다.
문서 등록·원본 바이트 확인 후 목록을 갱신하고 재조회한다. 작업 기록은 설치 폴더 밖에
저장한다. 현재 빈 문서는 PDF 종류이며 네이티브 노트 표지·템플릿 기능은 별도다.

## 완료를 판단하는 시험

1. 실제 연결에서 Flexcil의 비공개 속성을 읽을 수 있어야 한다.
2. 새 UUID의 문서와 목록 후보를 만들고 원본 보관·변경 검사를 수행한다.
3. 새 Drive ID로 등록하고 앱의 조회 조건 및 두 파일의 내용을 확인한다.
4. 태블릿에서 기존 테스트 노트와 신규 문서가 각각 표시되어야 한다.
5. 신규 문서에 추가한 필기를 회수하고 식별자·기존 내용 보존을 확인한다.

2026-09-28에 실제 1–5 시험을 통과했다. 새 문서 표시 후 사용자가 추가한 필기 객체
6개를 회수했고 PDF·문서/페이지 UUID를 보존했다. [검증 기록](verification.md)을 참고한다.
그보다 앞선 별도 시험에서는 에이전트가
**공식 Mac 앱의 UI로 새 노트를 만들고 태블릿 수신까지 확인한 기록**이 있다.
두 시험의 실행 경로와 증거를 혼동하지 않는다.

## 일차 자료

- [Google Drive custom properties](https://developers.google.com/workspace/drive/api/guides/properties)
- [Google OAuth native-app flow](https://developers.google.com/identity/protocols/oauth2/native-app)
- [AppAuth Android](https://github.com/openid/AppAuth-Android)
