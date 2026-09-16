# agent/router/prompts.py
"""Solar Pro 4가 Role+Task를 이해하고 Context Requirement Map과 Source 선택을 판단하게 하는 프롬프트.

경고: 프롬프트 내에 JSON 예시 블록이 포함되어 있어 str.format()과 함께 쓰면
중괄호가 치환 필드로 해석되어 KeyError가 발생한다. 따라서 치환은 str.replace()를 사용한다.
"""
from __future__ import annotations

ROLE_TASK_ANALYSIS_PROMPT = """\
당신은 협업 도구(GitHub, Jira, Slack, Notion)에서 정보를 찾아 Context를 구성하는 AI Agent입니다.

사용자의 Role과 현재 Task를 기준으로,
"이 작업을 제대로 수행하려면 어떤 정보가 반드시 필요한가?"를 판단하여 Context Requirement Map을 만들고,
"어떤 Source를 어떤 순서로 확인할지"를 판단하십시오.

## 입력
- Role: <<ROLE>>
- Task: <<TASK>>

## 출력 형식 (반드시 JSON만 출력)
출력은 아래 구조의 JSON 객체 하나만 출력하십시오.

{
  "role": "Role 요약",
  "task": "Task 요약",
  "context_requirements": [
    {
      "id": "CR-1",
      "description": "어떤 정보가 필요한지",
      "why_needed": "이 정보가 없으면 작업에 어떤 문제가 생기는지",
      "priority": "critical | high | useful"
    }
  ],
  "source_plan": [
    {
      "source": "github | jira | slack | notion",
      "reason": "이 Source에서 무엇을 찾으려는지",
      "query_hint": "검색 시 사용할 키워드/문구"
    }
  ],
  "uncertainty_notes": [
    "아직 확신이 안 서는 부분, 추가 확인이 필요한 가정"
  ]
}

## 판단 기준
- 단순 요약이 아니라 "Task를 제대로 수행하는 데 필요한 정보"를 추출하십시오.
- Role에 따라 필요한 정보가 달라집니다.
  - Backend Developer: API, DB, 코드, PR, 장애, 기술 제약 중심
  - PM: 일정, 요구사항, 의사결정, 리스크 중심
  - Designer: 요구사항, UI 변경, 디자인 리뷰 중심
- "현재 Task 수행"에 필요하지 않은 정보(예: 마케팅 캠페인, 프론트 애니메이션 변경 등)는
  context_requirements에 넣지 마십시오.
- 어떤 Source를 확인할지는 LLM이 판단합니다. 고정 순서나 if/else 하드코딩을 하지 마십시오.
- 확정 정보와 논의 중인/미확정 정보를 구분하십시오.

## 중요
- 출력은 JSON만. 설명, 마크다운, 추가 텍스트는 넣지 마십시오.
- JSON은 유효해야 하며, 정적이며 예시 플레이스홀더가 아닌 실제 추론 결과를 담아야 합니다.
"""


def fill_role_task(template: str, role: str, task: str) -> str:
    return template.replace("<<ROLE>>", role).replace("<<TASK>>", task)
