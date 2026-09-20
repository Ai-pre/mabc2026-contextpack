from __future__ import annotations

import re

SECTION_NAMES = [
    "[TASK]",
    "[MUST KNOW]",
    "[CONSTRAINTS]",
    "[USEFUL IF SPACE ALLOWS]",
    "[UNRESOLVED CONFLICTS]",
    "[VERIFY BEFORE USE]",
    "[DO NOT ASSUME]",
    "[SOURCE MAP]",
]

TENTATIVE_MARKERS = (
    "논의 중", "검토 중", "제안", "초안", "예정", "후보", "고려 중",
    "proposed", "proposal", "draft", "candidate", "under discussion", "considering",
)
FINAL_MARKERS = (
    "최종 결정", "최종 확정", "확정했", "확정됨", "승인", "적용 결정", "취소",
    "final decision", "finalized", "approved", "merged", "resolved", "cancelled", "canceled",
)
REOPENING_MARKERS = (
    "재논의", "재검토", "결정 보류", "번복",
    "reopen", "reopened", "reconsider", "on hold", "rolled back",
)
REPLACEMENT_MARKERS = (
    "전에 있었", "이전", "대체", "더 이상", "현재", "최종",
    "previous", "earlier", "replaced", "superseded", "no longer", "current", "final",
)


def split_section_items(body: str) -> list[str]:
    """Split bullet items while preserving continuation lines."""
    items: list[list[str]] = []
    current: list[str] = []

    for line in body.splitlines():
        if re.match(r"^\s*-\s+", line):
            if current:
                items.append(current)
            current = [line]
        elif current:
            current.append(line)
        elif line.strip():
            current = [line]

    if current:
        items.append(current)

    return ["\n".join(item).strip() for item in items if any(part.strip() for part in item)]


def _normalized(item: str) -> str:
    return re.sub(r"\s+", " ", item).casefold()


def _looks_like_retrieval_mechanics(text: str) -> bool:
    """Detect execution-scope rules, not provider/domain facts."""
    patterns = (
        r"\bworkspace_id\b",
        r"등록(?:된)? source.*(?:범위|제한|만)",
        r"source allowlist",
        r"다른 workspace",
        r"근거로만 제한",
        r"retrieve 결과로만 제한",
        r"(?:mcp|tool).*(?:호출|재호출|탐색하지|사용하지)",
        r"(?:연결된|등록된).*(?:없으므로|없어서).*(?:retrieve|mcp|tool)",
        r"(?:registered|connected).*(?:source|connector).*(?:only|scope|not available)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _is_superseded_history(text: str) -> bool:
    return (
        any(marker in text for marker in TENTATIVE_MARKERS)
        and any(marker in text for marker in FINAL_MARKERS)
        and any(marker in text for marker in REPLACEMENT_MARKERS)
        and not any(marker in text for marker in REOPENING_MARKERS)
    )


def _is_generic_coverage_disclaimer(text: str) -> bool:
    patterns = (
        r"위 .* 외.*추가 논의",
        r"retrieve 결과.*추가 논의",
        r"retrieve 결과.*더 많은",
        r"다른 (?:채널|문서|source).*논의",
        r"현재 (?:조회|retrieve) 범위 밖",
        r"접근하지 않은 source.*가능",
        r"추가 확인이 안전",
        r"more (?:messages|documents|sources|discussion).*may exist",
        r"outside (?:the )?(?:retrieval|current) scope",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def sanitize_handoff(handoff: str) -> str:
    """Apply one source-agnostic Handoff policy to every connector.

    The policy is intentionally narrow: remove execution metadata, generic
    coverage disclaimers, and resolved tentative→final history that was
    incorrectly placed in VERIFY/MISSING. Provider-specific facts are not
    rewritten and no new facts are invented.
    """
    header_re = re.compile(
        r"(?m)^(" + "|".join(re.escape(name) for name in SECTION_NAMES) + r")\s*$"
    )
    matches = list(header_re.finditer(handoff))
    if [match.group(1) for match in matches] != SECTION_NAMES:
        return handoff

    bodies: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(handoff)
        bodies[match.group(1)] = handoff[match.end():end].strip()

    def filtered(section: str) -> list[str]:
        kept: list[str] = []
        for item in split_section_items(bodies.get(section, "")):
            text = _normalized(item)
            if text in {"- none", "none"}:
                continue

            if section == "[CONSTRAINTS]" and _looks_like_retrieval_mechanics(text):
                continue

            if section == "[VERIFY BEFORE USE]" and _is_superseded_history(text):
                continue

            if section == "[DO NOT ASSUME]":
                if _is_generic_coverage_disclaimer(text) or _is_superseded_history(text):
                    continue

            kept.append(item)
        return kept

    rendered: list[str] = []
    for section in SECTION_NAMES:
        rendered.append(section)
        if section in {"[CONSTRAINTS]", "[VERIFY BEFORE USE]", "[DO NOT ASSUME]"}:
            items = filtered(section)
            rendered.extend(items if items else ["- None"])
        else:
            body = bodies.get(section, "").strip()
            rendered.append(body if body else "- None")
        if section != SECTION_NAMES[-1]:
            rendered.append("")

    return "\n".join(rendered).strip()
