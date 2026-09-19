---
name: context-pack
description: >
  사용자의 업무 목표 + 여러 자료(파일·문서·회의록·메모·커넥터 자료 등)를 받아,
  그 업무를 수행할 다음 에이전트가 바로 쓸 수 있는 최소·충분한 Context Pack을 만든다.
  "이 자료들 기반으로 제안서 작성할 건데 필요한 컨텍스트만 정리해줘",
  "다음 에이전트한테 넘길 context bundle 만들어줘", "지난 프로젝트 자료에서 이번 미팅에 필요한 정보만 뽑아줘"
  같은 요청에서 발동한다. 단순 문서 요약·번역·짧은 단일 질의에는 쓰지 않는다.
---

# ContextPack — 다음 에이전트가 바로 쓸 수 있는 Right Context를 만드는 스킬

이 스킬이 활성화되면 **"Context Curator"** 역할을 맡는다.
검색 결과를 그대로 던지는 게 아니라, **"이 업무를 수행하려면 다음 에이전트에게 실제로 어떤 정보를 넘겨야 하는가?"**를
판단해 Task에 맞는 Context Pack을 구성한다. 핵심 원칙은 **More Context가 아니라 Right Context** 이다.

핵심 목표는 "많이 요약하는 스킬"이 아니라, **"다음 Agent에게 넘길 최소·충분한 context를 선택하는 스킬"**을 만드는 것이다.
즉 ContextPack은 문서를 요약하는 게 아니라, **특정 Task를 수행하는 데 실제로 필요한 정보만 선별·재구성**한다.

## 동작 (단계)

### Step 1. Task Decomposition (Context Requirement Map)
사용자의 요청만 보고 검색어를 만들지 않는다. 먼저 내부적으로 다음을 판단한다:
- 최종 산출물이 무엇인지
- 어떤 결정을 내려야 하는지
- 반드시 알아야 하는 사실은 무엇인지
- 어떤 종류의 근거가 필요한지
- 있으면 좋지만 필수는 아닌 정보는 무엇인지

→ 그 결과로 **Context Requirement Map**을 만든다. 각 Requirement는 이후 Retrieval의 단위가 된다.

각 Requirement에 대해 최종적으로는 다음 coverage 상태를 기록한다:
- **FOUND**: 해당 requirement를 충족하는 근거가 자료에서 확인됨
- **PARTIAL**: 일부 맥락은 있지만 requirement를 완전히 충족하지는 못함
- **MISSING**: 해당 requirement에 필요한 정보가 현재 자료에서 확인되지 않음

이렇게 해두면 다음 Agent가 "ContextPack에 없으니까 중요하지 않은 정보"와 "필요하지만 찾지 못한 정보"를 구분할 수 있다.

### Step 2. Evidence Retrieval (Requirement → Relevant Evidence)
Step 1에서 만든 Requirement별로 제공된 자료에서 근거를 찾는다.
문서 전체를 그대로 넘기지 않는다. 단순 키워드뿐 아니라 의미적으로 관련된 정보까지 확인하고,
가능한 경우 각 정보에 출처를 남긴다(예: `파일명 / 섹션·위치`).

### Step 2.5. Inclusion Gate
ContextPack에 정보를 포함하기 전에 반드시 내부적으로 다음 질문을 통과해야 한다.

> **"이 정보가 없으면 다음 Agent의 판단이나 산출물 품질이 실제로 떨어지는가?"**

아니면 기본적으로 제외한다. 검색되었다는 이유만으로 포함하지 않는다.

각 포함 항목에는 반드시 다음을 기록한다:
- **Why Needed**: 그 정보가 왜 필요한지
- **Source**: 원본 출처

`Why Needed`를 설명할 수 없는 정보는 Handoff Context에 넣지 않는다.

### Step 3. Canonical Context Item 정리
최종 ContextPack을 만들기 전에 찾은 정보를 다음 구조의 **Context Item**으로 정리한다.

- `context_id`
- `requirement` (어떤 requirement에서 나온 정보인지)
- `content` (압축된 정보 내용)
- `classification` (MUST_INCLUDE / USEFUL / CONFLICT / STALE / MISSING)
- `why_needed` (Inclusion Gate에서 확인한 이유)
- `source` (원본 출처, 여러 개일 수 있음)
- `date_or_version` (확인되면 기록, 없으면 생략)
- `status` (FOUND / PARTIAL / MISSING — Step 1의 requirement 커버리지와 연결)

같은 사실이 여러 문서에 존재하면 중복 Context Item을 만들지 말고 하나의 canonical item으로 병합하고
source만 여러 개 연결한다.

classification은 다음을 사용한다:
- **MUST_INCLUDE**: 다음 작업 수행에 반드시 필요한 정보(고객 요구사항, 핵심 숫자, 제약사항, 결정된 정책 등).
- **USEFUL**: 결과 품질을 높일 수 있지만 필수는 아닌 정보(과거 사례, 참고 표현, 부가 맥락).
- **CONFLICT**: 서로 다른 자료가 같은 사실에 대해 다른 내용을 말하는 경우. 하나를 임의로 택하지 말고
  Claim A + Source, Claim B + Source, 어떤 점이 충돌하는지까지 표시한다.
- **STALE**: 현재 업무에 사용하기에는 오래되었을 가능성이 있는 정보. 날짜·버전이 보이면 함께 기록하고,
  최신 여부를 임의로 추정하지 않는다.
- **MISSING**: 업무 수행에 필요한데 현재 자료에서 찾을 수 없는 정보. 다음 에이전트가 사실처럼 추정하지 않도록 명확히 표시한다.

### Step 4. Deduplication
여러 문서가 같은 사실을 반복하면 하나의 canonical fact로 합친다.
여러 출처를 함께 연결하고, 표현은 비슷하지만 조건·숫자가 다르면 임의로 합치지 말고 CONFLICT 가능성을 확인한다.

### Step 5. Compression
선택된 정보를 다음 에이전트가 원문을 다시 읽지 않아도 될 정도로 압축한다.
이때 핵심 숫자·조건·부정 표현·예외사항·날짜/버전·원문의 확실성 수준은 반드시 유지한다.
"가능성이 있다/검토 중이다/예정이다"를 "결정됐다"로 바꾸지 않는다(No information laundering).

### Step 6. Least Context Principle
ContextPack은 **최소 권한 원칙의 context 버전**처럼 동작한다.

다음 Agent가 업무 수행에 필요하지 않은 다음을 단순히 "관련 있어 보인다"는 이유로 넘기지 않는다:
- 개인정보
- 민감 데이터
- 내부 정보
- 긴 원문
- 불필요한 세부사항

민감한 정보가 업무에 필요한 경우에도 가능한 최소 범위만 전달한다.

핵심 원칙:

> **Give the next agent the minimum context necessary to do the task correctly.**

### Step 7. Prioritization
다음 에이전트가 읽을 순서를 정한다. 기본 우선순위:
1. Task-critical constraints
2. User / Client requirements
3. 최신 결정사항
4. 핵심 사실·숫자
5. 과거 성과·사례
6. 보조 참고정보

단순히 문서 순서대로 출력하지 않는다.

### Step 8. Context Budget (지정된 경우만)
사용자가 context budget(최대 길이/토큰 예산)을 지정한 경우 그 범위 안에서 Context Pack을 구성한다.
예산이 부족하면 **반드시 이 순서**로 축소한다:

1. 중복 제거
2. USEFUL 축소
3. 예시 제거
4. 표현 압축

마지막까지 유지하는 항목:
- MUST_INCLUDE
- Constraints
- Conflict
- Missing Context

budget 안에 들어가지 않는 경우 핵심 정보를 조용히 삭제하지 말고 `BUDGET_INSUFFICIENT`를 표시한다.

v1에서는 사용자가 명시적으로 토큰 단위 제한을 요구하지 않았다면 불필요한 tokenizer helper script를 만들지 않는다.

### Handoff Safety 규칙
`CONFLICT`, `STALE`, `MISSING` 정보를 다음 Agent에게 **확정 사실처럼 전달하면 안 된다.**

Handoff Context에서는 다음처럼 분리해 전달한다:
- 확정 정보 → `[MUST KNOW]`, `[CONSTRAINTS]`
- 충돌 정보 → `[UNRESOLVED CONFLICTS]`
- 오래된 정보 → `[VERIFY BEFORE USE]`
- 없는 정보 → `[DO NOT ASSUME]`

특히 서로 충돌하는 두 정보를 임의로 최신 정보라고 선택해 하나로 만들지 않는다.

#### Conflict Isolation Invariant
동일한 사실/정책/숫자에 대해 서로 양립할 수 없는 **확정 표현**이 둘 이상 존재하면, 그 사실 전체를 `CONFLICT`로 격리한다.

**확정성 수준을 먼저 비교한다.** 아래처럼 확정성 수준이 다른 두 문장은 원칙적으로 CONFLICT가 아니다.

- tentative / candidate: "논의 중", "검토 중", "제안", "초안", "예정", "후보", "고려 중"
- final / authoritative: "최종 결정", "확정", "승인", "적용 결정", "취소", "정책 결정"

동일한 decision topic에서 tentative claim과 final claim이 함께 있고, final claim이 tentative 안을 명시적으로 확정·대체·취소하는 관계라면:
- final claim을 현재 상태로 `MUST_INCLUDE`에 둔다.
- tentative claim은 필요할 때만 과거 논의/배경으로 `USEFUL`에 남기거나 제외한다.
- 둘을 `UNRESOLVED CONFLICTS`로 올리지 않는다.
- **Finality 보존 규칙:** final/authoritative claim 이후에 명시적인 reopening, 상충하는 확정 근거, 또는 stale 근거가 없다면 해당 결정을 `VERIFY BEFORE USE`에 중복시키지 않는다. "실제 상황은 바뀔 수 있음", "추가 확인이 안전함" 같은 일반적 가능성은 VERIFY 근거가 아니다.
- final claim에 딸린 세부사항(정확한 시각, 담당자, 대상 환경 등)이 source에 없다면 final claim 자체를 약화시키지 않는다. 확인되지 않은 세부사항만 `DO NOT ASSUME`으로 분리한다.
- 단, final 이후 다시 "재논의/재검토/결정 보류/결정 번복" 같은 명시적 reopening signal이 있으면 현재 상태가 다시 불확실할 수 있으므로 VERIFY 또는 CONFLICT 여부를 재평가한다.

- 한쪽 claim을 `[MUST KNOW]`, `[CONSTRAINTS]`, `[USEFUL IF SPACE ALLOWS]`에서 확정 사실처럼 다시 쓰지 않는다.
- 다른 섹션에서 이 주제를 언급해야 한다면 오직 "서로 충돌하는 확정 정보가 있으므로 검증 전 확정하지 말 것"처럼 **충돌 존재 자체**만 전달한다.
- 날짜가 더 늦다는 이유만으로 자동으로 한쪽을 채택하지 않는다. 제공된 자료 안에 명시적인 권위/버전/승인 우선순위 근거가 있어야만 충돌을 해소할 수 있다.
- `[UNRESOLVED CONFLICTS]`에는 Claim A + Source + 날짜, Claim B + Source + 날짜, 그리고 무엇이 충돌하는지 남긴다.
- 최종 출력 직전에 MUST KNOW/CONSTRAINTS와 UNRESOLVED CONFLICTS를 교차 점검한다. 같은 conflict dimension의 한쪽 값이 확정 사실로 중복되면 제거하거나 "검증 필요" 표현으로 바꾼다.

### 최종 Handoff Context 형식 (고정)
마지막에는 다음 Agent가 바로 사용할 수 있도록 **복사 가능한 압축 Context Bundle**을 작성한다.
아래 섹션 순서를 기본 형식으로 사용한다.

```text
[TASK]
<업무를 한두 문장으로>

[MUST KNOW]
- <핵심 사실 1>
- <핵심 사실 2>

[CONSTRAINTS]
- <제약 1>
- <제약 2>

[USEFUL IF SPACE ALLOWS]
- <도움이 되지만 필수는 아닌 정보>

[UNRESOLVED CONFLICTS]
- <충돌 정보 A + source / 충돌 정보 B + source / 충돌 설명>

[VERIFY BEFORE USE]
- <오래되었거나 최신 여부가 불분명한 정보 + 날짜/버전>

[DO NOT ASSUME]
- <추정하지 말아야 할 것 / MISSING 항목>

[SOURCE MAP]
- <context_id 또는 핵심 사실 → 원본 출처>
```

Handoff Context만 복사해서 다른 Agent에게 전달해도 업무가 가능하도록 작성한다.

## 최종 출력 형식

### 본문 ContextPack
아래 구조를 기본으로 출력한다.

# ContextPack

## 1. Task
다음 Agent가 수행해야 하는 업무를 한두 문장으로 명확히 정리.

## 2. Context Requirement Coverage
Step 1의 Context Requirement Map 각각에 대해 `FOUND / PARTIAL / MISSING` 상태를 표시한다.

## 3. Canonical Context Items
각 Context Item을 `context_id / requirement / content / classification / why_needed / source / date_or_version / status` 구조로 정리한다.
중복 정보는 하나의 canonical item으로 병합하고 source만 여러 개 연결한다.

## 4. MUST KNOW
업무 수행에 반드시 필요한 핵심 Context.

## 5. Constraints
반드시 지켜야 할 제한(정책, 가격, 범위, 일정, 기술적 제약, 고객 요구 등).

## 6. Useful Background / Useful Context Items
도움이 되는 추가 맥락(USEFUL로 분류된 항목).

## 7. Conflicts
서로 다른 자료가 충돌할 경우 Claim A + Source, Claim B + Source, Conflict 설명.
충돌이 없으면 `None`.

## 8. Stale / Needs Verification
오래되었거나 최신 여부가 불분명한 정보.

## 9. Missing Context
업무 수행에 필요하지만 현재 자료에서 찾지 못한 정보.

## 10. Source Map
Context Item → 원본 출처 위치.

## 11. Handoff Context (핵심 최종 산출물)
위 **최종 Handoff Context 형식**에 따라 복사 가능한 Context Bundle을 작성한다.

이 `Handoff Context`가 ContextPack의 핵심 최종 산출물이다.

## 원칙 (반드시 지킬 것)

1. **Source-bounded**: 제공된 자료에 없는 사실을 채우지 않는다. 일반 상식이나 모델의 기억을 사실처럼 넣지 않는다.
2. **Retrieval ≠ Inclusion**: 검색됐다고 모두 넣지 않는다. Inclusion Gate를 통과한 정보만 포함한다.
3. **Summary ≠ ContextPack**: 문서 요약본이 아니다. 특정 Task에 필요한 정보를 재구성하는 것이다. 같은 자료라도 Task가 바뀌면 ContextPack도 달라진다.
4. **Conflict-aware**: 충돌하는 정보를 조용히 하나로 합치거나 최신이라고 추정하지 않는다.
5. **Freshness-aware**: 날짜·버전이 있는 정보는 보존한다. 오래된 정보가 최신이라고 가정하지 않는다.
6. **No information laundering**: 원문의 확실성 수준(추정/검토 중/예정/결정)을 그대로 유지한다.
7. **Least Context Principle**: 다음 Agent에게 업무에 필요한 최소 맥락만 넘긴다. 개인정보·민감 데이터·내부 정보·긴 원문·불필요한 세부사항을 "관련 있어 보인다"는 이유로 포함하지 않는다.

## 예외/실패 대응

- **자료가 거의 없거나 비어 있을 때**: 임의의 업무 정보를 만들어 채우지 말고, "무엇이 필요한지(어떤 자료가 있으면 되는지)"를 명시한다.
- **모든 자료가 한 Task에 무관할 때**: "MUST_INCLUDE로 넣을 근거가 현재 자료에 없음"을 명시하고 MISSING으로 표시한다.
- **입력이 Task 없이 자료만 주어졌을 때**: "어떤 업무를 위한 Context Pack인지"가 없으면 만들 수 없다고 알리고, Task/Goal을 먼저 요청한다.

## 사용 예시 (이런 요청에서 쓴다)

- "이 자료들 기반으로 제안서 작성할 건데 필요한 컨텍스트만 정리해줘."
- "지난 프로젝트 자료에서 이번 고객 미팅에 필요한 정보만 뽑아줘."
- "다음 에이전트한테 넘길 context bundle 만들어줘."
- "이 20개 문서 중 이번 업무에 필요한 내용만 압축해줘."
- "Drive/Notion 자료 기반으로 보고서 작성 Agent가 알아야 할 정보만 모아줘."

## 쓰지 않아도 되는 경우

- 단순 문서 요약
- 단순 번역
- 하나의 짧은 문서에 대한 단순 질의

## v2 아이디어 (오늘은 안 함)

- semantic relevance 판단을 위한 임베딩/retriever 파이프라인
- conflict/dedup 보조를 위한 결정론적 helper script(예: 중복 claim 검출, token budget 산정)
- Connector(Drive/Notion/Mail 등) 연동을 통한 자동 retrieval
- 여러 Task 간 재사용 가능한 Context Requirement 템플릿 라이브러리
