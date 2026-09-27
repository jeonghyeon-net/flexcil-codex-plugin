# 포맷과 동기화 조사

작성일: 2026-09-28. 공식 파일 포맷 명세가 아니라 관측 결과다. 앱 버전이 달라지면 검증을 다시 수행한다.

## 파일 구조

`.flx`는 문서 한 개의 ZIP이다. `info`, `pages.index`, `attachment/PDF/<UUID>`, `objects/<page UUID>.<layer>`와 부가 메타데이터를 포함한다. 부가 항목과 알 수 없는 레이어를 보존한다. 0.0.4와 0.0.5 샘플의 읽기/로컬 편집을 검사했다. 다른 버전은 읽을 수 있어도 쓰기를 거절한다.

실제 내보내기 자료에는 서로 다른 `.itemInfo` 항목이 같은 ZIP에 두 번 들어 있는 경우가 있었다. 이 항목에 한해 두 바이트열을 보존하며 마지막 항목을 읽는다. 그 밖의 중복 파일명, 경로 탈출, 심볼릭 링크, 암호화 파일, 선언 크기 불일치는 거절한다.

`documents.list`와 `.trash.list`는 다음 구조다.

1. 압축 전 바이트 길이: 8-byte little-endian unsigned integer.
2. raw DEFLATE stream.
3. UTF-8 JSON.

가상 폴더는 `type: 1`, `children` 배열이다. 문서는 `document`로 문서 UUID를 참조한다. 휴지통은 `{"parentInfo":[],"item":{...}}` 레코드 배열이다. 앱의 루트 휴지통 샘플을 관측했다. 플러그인 복원 대상은 명시한 폴더 또는 루트이며, 원래 부모 자동 복구를 주장하지 않는다.

필기 `points`는 Base64 안의 uint32 count + count개의 float32 삼중값이다. 관측한 회전 0/배율 1 자료에서 시작점과 x/y 모두 페이지 너비로 정규화됐다. 세 번째 값의 의미는 확정하지 않았으며 압력으로 단정하지 않는다.

텍스트는 `.texts`의 type 30 객체와 `.objects`의 참조를 함께 쓴다. 본문은 `text`와 `columns[].p.span[]`에 존재한다. 테스트한 텍스트 프레임은 x/너비와 y/높이 정규화를 사용하고, 글자 크기는 페이지 너비 기준이다.

## Android 조사와 실제 시험

실행·설치하지 않은 Android 패키지 1.5.0.11 (822)의 정적 분석에서 다음을 관측했다.

- 동기화 파일 열거는 `appProperties.Flexcil = MakeFromFlexcil`을 필터로 사용한다.
- 업로드는 일반 `modifiedTime`과 앱 전용 시간 속성을 함께 설정한다.
- 다운로드 변경 판단 경로는 일반 Drive 수정 시간을 밀리초로 해석하여 로컬 문서 시간과 비교한다. 관측 경로에 2초 허용 오차가 있다.
- PDF는 앱의 일반 가져오기 인텐트로 받지만, 확인된 인텐트/딥링크가 원격 클라이언트에게 무인 신규 등록 API를 제공하지는 않는다.

패키지는 외부 배포 미러에서 받아 읽기만 했다. 공식 서명과 대조하지 않았으므로 정적 관찰의 출처 한계를 가진다. 개인 기기의 실제 앱 버전은 사용자가 '최신일 것'이라고 답했고 정확한 버전 번호는 관측하지 못했다. 둘을 같은 버전이라고 기록하지 않는다.

실제 사용자 소유 Android 테스트 노트에서는 커넥터로 기존 `.flx` 바이트만 갱신한 뒤 문구가 표시되었고, 새 필기 42개가 포함된 파일을 다시 회수했다. 텍스트 객체는 그대로 보존됐다. 이는 기존 파일 갱신의 증거이며 신규 파일 등록의 증거가 아니다.

그 이후 직접 OAuth 연결과 신규 생성 경로를 구현해 별도 시험을 수행했다. 새 문서·목록 UUID와 새 Drive ID로 PDF 문서를 등록하고 가상 폴더에 배치했다. 사용자가 태블릿 표시를 확인하고 필기를 추가했으며 새 필기 객체 6개를 회수했다. [생성 경로 분석](android-document-creation.md)과 [검증 기록](verification.md)에 절차를 구분해 기록했다.

이전 Mac 실험의 앱 전용 수정시간 동작을 Android에 일반화하지 않았다. macOS Flexcil UI/로컬 데이터에 의존하는 경로는 구현에 사용하지 않는다.

## 공식 자료

- [Flexcil 클라우드 동기화](https://support.flexcil.com/hc/en-us/articles/22708916439321-How-to-use-Cloud-Sync-feature): 공개 도움말은 Google Drive 동기화와 동기화 시점을 설명한다.
- [Flexcil 백업·복원](https://support.flexcil.com/hc/en-us/articles/6857130951321-How-to-backup-and-restore-your-data): `.flex`는 앱의 백업 파일이며 Android 복원에는 앱 조작이 필요하다.
- [Google Drive custom properties](https://developers.google.com/workspace/drive/api/guides/properties): `appProperties`는 해당 앱에 한정되는 속성이다.
- [Drive files.update](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update): 기존 파일 내용 갱신.
- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins): 스킬·연결·로컬 marketplace 구성.

역어셈블 결과, APK, 실제 노트, 개인 계정 ID, 비공개 Gist URL은 저장소에 포함하지 않는다.
