# 협업 규칙

## 한눈에 보기

1. 이슈를 만들고 담당자를 지정한다.
2. `main`에서 브랜치를 딴다. 예: `feat/12-link-traversal`
3. 커밋은 `type(scope): 요약` 형식으로 남긴다.
4. PR을 올리고, 템플릿을 채우고, 리뷰어 1명 이상을 지정한다.
5. CI가 통과하고 승인을 받으면 작성자가 **Squash and merge**로 합친다.
6. 합친 브랜치는 지운다.

## 브랜치

`main` 하나만 오래 살아 있고, 나머지는 작업 단위로 만들었다가 지운다.

```
<type>/<이슈 번호>-<짧은 설명>
```

- type은 아래 커밋 type과 같다.
- 설명은 영어 소문자와 하이픈으로 쓴다.
- 이슈가 없는 작은 작업은 번호를 생략한다.

| 예 | 뜻 |
|---|---|
| `feat/12-link-traversal` | 12번 이슈, 링크 탐색 기능 |
| `fix/27-status-ready-rule` | 27번 이슈, READY 판정 버그 수정 |
| `docs/naming-conventions` | 표기 규칙 문서 수정 |

`main`에는 직접 push하지 않는다. 브랜치는 하루 이틀 안에 합칠 수 있는 크기로 자른다.

## 커밋

[Conventional Commits](https://www.conventionalcommits.org/ko/v1.0.0/) 형식을 따른다.

```
<type>(<scope>): <요약>

<본문: 왜 바꿨는지. 필요할 때만>
```

| type | 쓰는 때 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서만 바꿈 |
| `refactor` | 동작은 그대로 두고 구조만 바꿈 |
| `test` | 테스트 추가, 수정 |
| `chore` | 빌드 설정, 의존성 등 잡일 |
| `ci` | CI 설정 |
| `spec` | 온톨로지 스펙(`ontology/*.yaml`) 변경 |

| scope | 범위 | 담당 |
|---|---|---|
| `data` | 적재, 합성 데이터 | A |
| `db` | 스키마, 뷰, 권한 | A |
| `ontology` | 객체, 링크 정의 | B |
| `engine` | 조회 엔진, 파생 속성 | B |
| `action` | 액션 정의, 액션 엔진, 감사 로그 | C |
| `policy` | 행 필터, 컬럼 가림 | B, C |
| `api` | FastAPI 라우터 | 공통 |
| `clock` | 가상 시계 | A |
| `infra` | Docker, OpenMetadata | A |

규칙:

- 요약은 한국어로 쓰고, 50자 안쪽으로, 마침표 없이 끝낸다.
- 한 커밋에는 한 가지 변경만 담는다.
- 스펙이나 규칙을 바꾸는 커밋은 본문에 이유를 적는다.

예:

```
feat(engine): 링크 두 단계 탐색 지원
fix(action): 재가동 거부 사유 순서 수정
spec(ontology): resolvedBy를 첫 교체 기록 규칙으로 변경
docs: 표기 규칙에 API 키 규칙 추가
```

## PR

- **PR 하나에 목적 하나.** 바뀐 줄이 400줄을 넘으면 나눌 수 있는지 먼저 본다.
- **제목은 커밋 형식을 따른다.** Squash merge를 하면 PR 제목이 그대로 `main`의 커밋 메시지가 된다. CI가 제목 형식을 검사한다.
- 본문은 [PR 템플릿](.github/pull_request_template.md)을 채운다.
- 작업 중이면 Draft PR로 올린다. 일찍 올려야 방향을 빨리 맞출 수 있다.

### 리뷰

- 승인 1명 이상이 필요하다.
- 아래 파일을 바꾸면 담당자의 승인을 받는다.

| 파일 | 승인할 사람 |
|---|---|
| `ontology/objects.yaml`, `ontology/links.yaml` | B |
| `ontology/actions.yaml` | C |
| `db/`, `data/` | A |
| `docs/api.md` | 공통 규칙은 B와 C, 조회 API는 B, 액션 API는 C |
| `docs/conventions.md`, `docs/decisions.md` | 세 명 모두 |

- 리뷰어는 하루 안에 응답한다. 바로 못 보면 언제 볼지 댓글로 남긴다.
- 코멘트 앞에 무게를 붙인다. `[필수]`는 고쳐야 합칠 수 있고, `[제안]`은 작성자가 판단하고, `[질문]`은 답만 하면 된다.
- 대화는 작성자가 반영한 뒤 리뷰어가 해결 처리한다.

### 합치기

- CI가 모두 통과해야 합칠 수 있다.
- **Squash and merge만** 쓴다.
- 작성자가 직접 합치고, 합친 뒤 브랜치를 지운다.
- `main`이 앞서 나가 충돌이 나면 작성자가 `main`을 rebase해서 푼다.

## 결정과 스펙을 바꿀 때

- 팀 결정을 바꾸는 PR은 [docs/decisions.md](docs/decisions.md)에 새 번호로 기록을 추가한다.
- 객체, 링크, 속성을 바꾸면 [다이어그램](docs/diagrams/ontology.drawio)도 같은 PR에서 고치고, PNG를 다시 내보낸다.
- 이름과 코드 값은 [docs/conventions.md](docs/conventions.md)를 따른다.

## GitHub 저장소 설정

저장소를 만든 사람이 한 번만 설정한다.

- **Settings > General > Pull Requests:** Squash merging만 켜고, "Default to pull request title"을 고른다. "Automatically delete head branches"를 켠다.
- **Settings > Branches > `main` 보호 규칙:**
  - Require a pull request before merging, 승인 1명
  - Require status checks to pass: `lint`, `test`, `conventions`, `pr-title`
  - Require conversation resolution before merging
  - Do not allow bypassing the above settings
- **Labels:** `part:A`, `part:B`, `part:C`, `spec`, `bug`, `blocked`
