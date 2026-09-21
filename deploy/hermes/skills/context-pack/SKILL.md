---
name: context-pack
description: >
  사용자의 업무 목표와 등록된 자료를 바탕으로 다음 에이전트가 바로 사용할
  최소·충분한 Handoff Context를 만든다. 여러 Source의 근거를 비교해
  conflict/stale/missing을 구분해야 하는 업무에서 사용한다.
---

# ContextPack

목표는 자료를 많이 요약하는 것이 아니라 **현재 Task 수행에 필요한 Right Context만 넘기는 것**이다.
검색 결과를 그대로 복사하지 말고, 다음 Agent의 판단이나 구현을 실제로 바꾸는 정보만 남긴다.

## Internal workflow

1. Task에서 필요한 사실·결정·제약을 내부적으로 정리한다.
2. 등록 Source의 retrieve 결과에서 관련 Evidence만 고른다.
3. 같은 사실은 dedupe하고 provenance/date/certainty를 보존한다.
4. 아래 Handoff Policy로 classification한다.
5. Finality Preflight 후 고정 8-section Handoff만 출력한다.

## Evidence contract

가능하면 다음 공통 shape를 우선 사용한다.

```text
evidence_id
source_type
kind
source_ref
content
timestamp
author
metadata
```

판단 기준은 provider 이름이 아니라 `content + timestamp + certainty + provenance`다.
provider envelope보다 공통 `evidence[]`가 있으면 그것을 우선한다.

## Inclusion policy

포함 전 질문:

> 이 정보가 없으면 다음 Agent의 판단이나 산출물 품질이 실제로 떨어지는가?

아니면 제외한다. 특히 다음은 semantic Handoff에 넣지 않는다.

- workspace_id, connector 연결 여부, source allowlist, MCP tool 선택/호출 규칙
- 현재 retrieval이 보지 않은 다른 채널/문서가 있을 수 있다는 면책 문구
- 교차 확인하지 않았다는 execution coverage 설명
- task와 무관한 join/leave 이벤트, 긴 파일 목록, 저가치 디버그 정보
- retrieval timing, evidence count, cache 상태 같은 실행 진단 정보

Source 자체가 말하는 제품 기능, PR 상태, 배포 결정, 코드 구조는 Project Evidence다.

## Handoff Policy

- **MUST KNOW**: 다음 작업의 판단/구현을 바꾸는 현재 사실·최근 결정·필수 제약
- **CONSTRAINTS**: 실제 업무 수행 중 지켜야 할 프로젝트/정책/기술 제약
- **USEFUL**: 도움이 되지만 없어도 다음 작업을 수행할 수 있는 배경
- **CONFLICT**: 같은 decision dimension의 서로 양립 불가능한 확정 claim
- **STALE / VERIFY**: 실제 근거로 오래됐거나 현재 유효성 검증이 필요한 정보
- **MISSING / DO NOT ASSUME**: Task에 실제로 필요한데 Evidence에 없는 구체적 사실

### Conflict / finality invariant

**Source silence is not conflict.** 한 Source의 explicit claim을 다른 Source가 말하지 않는 것만으로
CONFLICT나 VERIFY를 만들지 않는다.

확정성 수준을 먼저 비교한다.

- tentative: 논의 중 / 검토 중 / 제안 / 초안 / 예정 / 후보 / 고려 중
- final: 최종 결정 / 최종 확정 / 확정 / 승인 / 적용 결정 / 취소

같은 decision topic에서 tentative 뒤에 그 안을 명시적으로 대체·정정·확정·취소하는 final이 있으면
CONFLICT가 아니다. 뒤의 final을 현재 상태로 사용한다.

특히 같은 메시지·같은 저자의
`금요일로 논의 중 → 아니다, 토요일로 최종 결정`
같은 self-correction은 **토요일 final 하나**로 처리한다.

`[UNRESOLVED CONFLICTS]`에는 서로 양립할 수 없는 **final ↔ final**이 둘 이상 있을 때만 남긴다.
날짜가 더 늦다는 이유만으로 한쪽을 고르지 않는다.

final 이후 실제 `재논의 / 재검토 / 보류 / 번복` Evidence가 있을 때만 다시 VERIFY/CONFLICT를 검토한다.
"나중에 바뀌었을 수도 있음", "번복됐는지 확인하지 않음", "reopening evidence 없음"은 reopening Evidence가 아니다.

resolved된 tentative→final topic을 VERIFY/DO NOT ASSUME에서 다시 불확실하게 만들지 않는다.

### Stale / missing invariant

STALE은 날짜·버전·후속 근거 등 실제 freshness signal이 있을 때만 사용한다.
단순히 "최신인지 다시 확인하면 안전하다"는 이유로 VERIFY를 만들지 않는다.

MISSING은 Task-critical 정보가 Evidence에 실제로 없을 때만 사용한다.
다음은 MISSING이 아니다.

- 다른 Source에 더 많은 논의가 있을 가능성
- 이번 retrieval에서 교차 확인하지 않은 사실
- 이미 final로 대체된 tentative 안
- 채널명만으로 추측 가능한 일반적 가능성

Task-critical missing이 없으면 `[DO NOT ASSUME]`은 `- None`이다.

### Finality Preflight

최종 출력 직전에 decision topic별로 확인한다.

1. tentative→explicit final 대체인가? 그러면 final만 남긴다.
2. Source silence인가? 그러면 conflict가 아니다.
3. final↔final이 실제로 둘 이상인가? 그때만 UNRESOLVED CONFLICTS다.
4. 실제 reopening Evidence가 있는가? 없으면 finality를 유지한다.
5. resolved topic이 VERIFY/DO NOT ASSUME에 다시 들어갔는지 제거한다.
6. 의미 없는 짧은 중복 bullet과 task-irrelevant evidence를 제거한다.

### 최종 Handoff Context 형식 (고정)

아래 8개 header를 정확한 철자와 순서로 모두 사용한다.

```text
[TASK]
- <다음 Agent가 수행할 업무>

[MUST KNOW]
- <핵심 사실/결정>

[CONSTRAINTS]
- <실제 업무 제약>

[USEFUL IF SPACE ALLOWS]
- <선택적 배경>

[UNRESOLVED CONFLICTS]
- <서로 양립 불가능한 final claim A/B + source>

[VERIFY BEFORE USE]
- <실제 stale/verification 필요 정보>

[DO NOT ASSUME]
- <Task-critical missing 정보>

[SOURCE MAP]
- <핵심 사실 → evidence_id/source_ref 또는 실제 MCP callable>
```

규칙:
- 내용이 없으면 정확히 `- None`.
- 같은 사실을 여러 section에 반복하지 않는다.
- provenance만 따로 `- 근거:` bullet로 만들지 않는다.
- SOURCE MAP에는 task에 실제 사용한 Evidence만 남긴다.
- `# ContextPack`, numbered report, `[SCRIPT_MAP]`, `[호출 MCP tool]` 같은 대체 형식을 추가하지 않는다.
- 파일 생성/수정 설명, patch/diff, 후기 문장을 붙이지 않는다.
- 마지막 `[SOURCE MAP]` 항목에서 즉시 끝낸다.
