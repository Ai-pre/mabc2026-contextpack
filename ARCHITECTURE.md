# MABC 2026 Finals — AI Agent MVP Architecture
# 역할별 책임 정의 (각 1~2문장)

## 1. User Interface (frontend/)
- 역할 선택(Role), 현재 Task 입력, "Analyze Context" 트리거, Agent 진행 상태 표시, 최종 ContextPack 결과 및 Source provenance 표시, "Send to Coding Agent" 전달을 제공하는 최소 Web UI.
- MVP에서는 Flask 또는 FastAPI의 단순 템플릿으로도 충분하며, 통신은 같은 backend의 REST endpoint를 사용한다.

## 2. Backend API (backend/)
- Role/Task 입력을 받아 Agent loop를 kicks off하고, 단계별 trace와 최종 Handoff Context를 JSON으로 반환하는 최소 HTTP 서버.
- 모델 설정(provider/model)은 한 곳에서 관리하며, downstream Coding Agent에 전달할 수 있는 Handoff Context 페이로드를 생성한다.

## 3. Agent Router / Planner (agent/router/)
- Solar Pro 4를 사용하여 사용자 Role + Current Task를 이해한 뒤, Context Requirement Map(무엇을 알아야 하는가)을 생성한다.
- 어떤 Source(GitHub/Jira/Slack/Notion)를 어떤 순서로 확인할지 LLM 판단으로 결정하며, 단순 if/else 고정 라우팅이 아니다.

## 4. Source Connectors (connectors/*)
- GitHub / Jira / Slack / Notion 각각에 대해 동일한 Tool interface를 가진 mock connector.
- MVP에서는 실제 OAuth 연동 없이, 미리 준비한 demo_project 데이터셋을 조회하는 형태로 동일한 interface를 제공한다.

## 5. Demo Project Data (data/demo_project/)
- 부분환불 시나리오를 기준으로 MUST / STALE / CONFLICT / MISSING / irrelevant / duplicate 사례를 모두 포함하는 테스트 데이터.
- Role + Task에 따라 다른 Context가 선택되도록 충분히 다양한 정보를 포함한다.

## 6. ContextPack Invoker (agent/context/)
- 수집된 후보 Context를 실제 Hermes `context-pack` Skill에 전달하여 MUST_INCLUDE / USEFUL / CONFLICT / STALE / MISSING 분류, 중복 제거, 압축, Handoff Context 생성을 수행한다.
- SKILL.md의 핵심 동작을 복제하지 않고, Skill을 실제 호출해서 사용한다.

## 7. Eval (eval/)
- Baseline(전체/단순 RAG 결과 직접 전달) vs ContextPack(선별 후 전달)을 비교할 수 있는 평가 구조.
- 필수 Context 포함률, irrelevant 포함률, stale 사용 여부, conflict 확정 사용 여부, missing hallucinate 여부, downstream 결과 정확도를 측정한다.

## 책임 분리 요약
- Hermes Agent(우리 코드): Task 이해, Role 이해, Requirement 도출, Tool/Source 선택, Retrieval, 추가 탐색 여부 판단.
- ContextPack Skill: Retrieval된 정보 중 Task에 필요한 Context 선택, 분류, Deduplication, Compression, Handoff Context 생성.
- Solar Pro 4: 모든 Router/Planner/semantic judgment/Context Requirement 생성/추가 탐색 판단을 수행.
