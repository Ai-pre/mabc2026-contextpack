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
        r"(?:github|slack|notion|document)\s*근거는\s*(?:연결된|등록된).*(?:만\s*사용|제한)",
        r"(?:연결된|등록된)\s*(?:repository|repo|channel|page).*(?:만\s*사용|다른 .*근거로 쓰지|범위로 제한)",
        r"다른\s*(?:repository|repo|channel|page).*(?:근거로 쓰지|사용하지|탐색하지)",
        r"(?:repository|repo|channel|page)\s*allowlist",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _strip_analysis_scope_prefix(item: str) -> str:
    """Remove a current-workspace registration clause but keep project facts.

    Example:
    "- Slack connector 관련: Workspace에는 C123 채널이 등록되어 있으며 retrieval은 ..."
    becomes:
    "- retrieval은 ..."
    """
    match = re.match(r"^(\s*-\s*)", item)
    bullet = match.group(1) if match else ""
    body = item[match.end():] if match else item
    body = re.sub(
        r"(?i)^(?:slack\s+connector\s+관련:\s*)?"
        r"(?:이\s*)?(?:workspace|작업공간)(?:에는|에)\s+.*?"
        r"(?:등록되어\s*있으며|연결되어\s*있으며)\s*",
        "",
        body,
    ).strip()
    if not body:
        return ""
    return bullet + body


def _looks_like_analysis_scope_metadata(text: str) -> bool:
    """Detect facts about this analysis run's configured workspace/sources.

    Keep project facts such as "Workspace isolation is implemented" intact.
    Only remove self-referential execution state such as "this workspace is
    GitHub-only" or "Slack is not connected in the current workspace".
    """
    patterns = (
        r"(?:이|현재|해당)\s*workspace.*(?:only|연결|등록|source|connector|retrieve|근거|scope)",
        r"(?:this|current)\s+(?:analysis\s+)?workspace.*(?:only|connected|not connected|source|connector|retriev|valid evidence|scope)",
        r"(?:현재|해당)\s*(?:분석|task).*workspace.*(?:source|connector|retrieve|근거)",
        r"(?:이|현재|해당)\s*workspace에서.*(?:사용 가능|사용할 수|유효한 근거|연결|retrieve)",
        r"(?:slack_retrieve|notion_retrieve|github_retrieve|document_retrieve).*"
        r"(?:이|현재|해당)\s*workspace",
        r"(?:이|현재|해당)\s*workspace.*(?:slack_retrieve|notion_retrieve|github_retrieve|document_retrieve)",
        r"(?:이 task 수행 시점|현재 분석 시점).*(?:source|connector|retrieve|근거)",
        r"(?:source|connector).*(?:이|현재|해당)\s*workspace.*(?:없|연결되지|등록되지)",
        r"(?:live\s+)?(?:github\s+repository|slack\s+channel|notion\s+page).*(?:현재\s*)?(?:연결되어\s*있지|연결되지|미연결)",
        r"(?:현재\s*)?(?:연결|등록)되어\s*있지\s*않.*(?:retrieve|사용하지)",
        r"^-\s*등록(?:된)?\s*workspace\s*source만\s*근거로\s*사용",
        r"등록(?:된)?\s*(?:workspace|작업공간)\s*source만.*근거",
        r"\(이\s*workspace:\s*(?:github|slack|notion|document)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _is_absence_only_conflict(text: str) -> bool:
    """A missing corroborating source is not a conflicting claim."""
    patterns = (
        r"(?:교차\s*검증|cross[- ]?check|corroborat).*(?:안|못|없|not)",
        r"(?:다른|타|other)\s*(?:source|근거).*(?:확인되지|언급\s*없|찾지\s*못|not found|no mention)",
        r"(?:github|slack|notion|document|source).*(?:확인되지|언급\s*없|찾지\s*못|not found|no mention).*(?:충돌|conflict|검증)",
        r"(?:충돌|conflict).*근거.*(?:확인되지|없음|not found)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _is_generic_retrieval_coverage(text: str) -> bool:
    """Drop statements about what the current retrieval did not cover."""
    patterns = (
        r"한\s*개\s*(?:메시지|문서|source).*(?:존재|근거|확인)",
        r"다른\s*(?:채널|문서|repository|source).*(?:확인하지|확인되지|조회하지|탐색하지)",
        r"(?:현재|이번)\s*(?:retrieve|retrieval|조회)\s*(?:결과|범위).*(?:확인되지|없|못)",
        r"(?:별도|추가)\s*(?:충돌|conflict)\s*근거.*(?:확인되지|없)",
        r"(?:other|additional)\s*(?:channels|documents|sources).*(?:not checked|not retrieved|not found)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _has_actual_reopening_signal(text: str) -> bool:
    if not any(marker in text for marker in REOPENING_MARKERS):
        return False

    absence_patterns = (
        r"(?:재논의|재검토|재조정|번복|reopen(?:ing|ed)?|reconsider).{0,50}(?:증거|근거).{0,20}(?:없|확인되지|not found|no evidence)",
        r"(?:재논의|재검토|재조정|번복|reopen(?:ing|ed)?|reconsider).{0,40}(?:있었는지|여부|확인할 수 없|unknown|not confirmed|not verified)",
        r"(?:증거|근거).{0,20}(?:없|확인되지|no evidence).{0,50}(?:재논의|재검토|재조정|번복|reopen(?:ing|ed)?|reconsider)",
        r"(?:reopening|재논의|재검토|번복).{0,40}(?:없다|없음|없고|아니다)",
    )
    return not any(re.search(pattern, text) for pattern in absence_patterns)


def _is_superseded_history(text: str) -> bool:
    return (
        any(marker in text for marker in TENTATIVE_MARKERS)
        and any(marker in text for marker in FINAL_MARKERS)
        and any(marker in text for marker in REPLACEMENT_MARKERS)
        and not _has_actual_reopening_signal(text)
    )


def _extract_final_claim_from_superseded_conflict(item: str) -> str | None:
    """Recover an explicit final claim from a misclassified tentative->final conflict."""
    quoted = re.findall(r'["“]([^"”]+)["”]', item)
    for span in reversed(quoted):
        text = _normalized(span)
        if (
            any(marker in text for marker in FINAL_MARKERS)
            and not any(marker in text for marker in TENTATIVE_MARKERS)
        ):
            claim = re.sub(r"^(?:아니다[.!?]?\s*|no[,.]?\s*)", "", span.strip(), flags=re.I)
            source = re.search(r"\((?:source|출처):\s*([^)]+)\)", item, flags=re.I)
            suffix = f" (source: {source.group(1).strip()})" if source else ""
            return f"- {claim}{suffix}"
    return None


def _source_identity_tokens(text: str) -> set[str]:
    """Extract stable source-ish tokens used to pair split claims conservatively."""
    tokens = set(re.findall(r"\b\d{10,}(?:\.\d+)?\b", text))
    tokens.update(
        token.casefold()
        for token in re.findall(
            r"\b(?:slack|github|notion|document)[:/][A-Za-z0-9_.:#/@-]+",
            text,
            flags=re.I,
        )
    )
    return tokens


def _clean_split_final_claim(item: str) -> str:
    body = re.sub(r"^\s*-\s*", "", item).strip()
    body = re.sub(r"(?i)^claim\s*[ab]\s*:\s*", "", body).strip()
    body = re.sub(
        r"\s*\((?:slack|github|notion|document)\b[^)]*\)\s*$",
        "",
        body,
        flags=re.I,
    ).strip()
    return f"- {body}" if body else ""


def _resolve_split_tentative_final_conflicts(items: list[str]) -> tuple[set[str], list[str]]:
    """Resolve split Claim A/Claim B when one is tentative and the other final.

    We only auto-resolve when the pair is explicitly Claim A/B or both bullets
    share a stable source token (for example the same Slack message timestamp).
    True final-vs-final conflicts are never touched.
    """
    resolved: set[str] = set()
    promoted: list[str] = []

    for i, first in enumerate(items):
        first_text = _normalized(first)
        first_tentative = any(marker in first_text for marker in TENTATIVE_MARKERS)
        first_final = any(marker in first_text for marker in FINAL_MARKERS)
        if not first_tentative or first_final or _has_actual_reopening_signal(first_text):
            continue

        for second in items[i + 1:]:
            second_text = _normalized(second)
            second_tentative = any(marker in second_text for marker in TENTATIVE_MARKERS)
            second_final = any(marker in second_text for marker in FINAL_MARKERS)
            if not second_final or second_tentative or _has_actual_reopening_signal(second_text):
                continue

            explicit_pair = (
                re.search(r"(?i)^\s*-\s*claim\s*a\s*:", first) is not None
                and re.search(r"(?i)^\s*-\s*claim\s*b\s*:", second) is not None
            )
            shared_source = bool(
                _source_identity_tokens(first_text)
                & _source_identity_tokens(second_text)
            )
            if not (explicit_pair or shared_source):
                continue

            resolved.update({first, second})
            cleaned = _clean_split_final_claim(second)
            if cleaned:
                promoted.append(cleaned)
            break

    return resolved, promoted


def _is_provenance_only_item(text: str) -> bool:
    return bool(re.match(r"^-?\s*(?:근거|source|출처)\s*:", text))


def _is_degenerate_semantic_label(text: str) -> bool:
    label = re.sub(r"^-?\s*", "", text).strip(" .:")
    return label in {
        "최종 결정",
        "최종 확정",
        "확정",
        "final decision",
        "final",
        "approved",
        "resolved",
    }


def _dedupe_subsumed_items(items: list[str]) -> list[str]:
    """Drop short bullets whose semantic tokens are already contained in a richer bullet."""
    normalized = [
        re.sub(r"^-?\s*", "", _normalized(item)).strip(" .:")
        for item in items
    ]
    keep: list[str] = []
    for index, item in enumerate(items):
        body = normalized[index]
        tokens = re.findall(r"[0-9A-Za-z가-힣_]+", body)
        informative = [token for token in tokens if len(token) >= 2]
        if 2 <= len(informative) <= 8 and len(body) <= 60:
            subsumed = False
            for other_index, other_body in enumerate(normalized):
                if other_index == index or len(other_body) <= len(body) + 12:
                    continue
                if all(token in other_body for token in informative):
                    subsumed = True
                    break
            if subsumed:
                continue
        keep.append(item)
    return keep


def _count_final_signals(items: list[str]) -> int:
    return sum(
        sum(text.count(marker) for marker in FINAL_MARKERS)
        for text in (_normalized(item) for item in items)
    )


def _is_runtime_provenance_summary(text: str) -> bool:
    return bool(re.search(
        r"(?:retrieval\s+provenance|retrieval\s+summary|retrieval\s+결과|evidence\s+count|timing_ms)",
        text,
    ))


def _is_hypothetical_reopening_gap(text: str) -> bool:
    """Drop invented uncertainty about a final decision being reopened later.

    Real reopening evidence belongs in VERIFY/CONFLICT. This only catches
    absence-style speculation such as "whether there was another reversal".
    """
    reopening_terms = (
        "재논의", "재검토", "재조정", "번복", "결정 변경", "취소",
        "reopen", "reconsider", "reversal", "rolled back", "changed later",
    )
    hypothetical_terms = (
        "있었는지", "여부", "확인되지", "확인할 수 없", "모르", "가능성",
        "whether", "unknown", "not confirmed", "not verified", "may have",
    )
    return (
        any(term in text for term in reopening_terms)
        and any(term in text for term in hypothetical_terms)
    )


def _is_low_value_retrieved_item(text: str) -> bool:
    """Drop retrieved noise explicitly described as irrelevant to the task."""
    patterns = (
        r"증거 가치 낮음",
        r"결정 사항과 직접 관련 없어",
        r"결정사항과 직접 관련 없어",
        r"참여자? 진입 메시지",
        r"채널 참여 메시지",
        r"참여 이벤트",
        r"새\s*멤버.*참여",
        r"멤버\s*참여",
        r"채널에?.*참여(?:했|함|했다)",
        r"결정 근거로는? 미사용",
        r"join message",
        r"join events?",
        r"member join events?",
        r"joined (?:the )?channel",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _is_crosscheck_only_verify(text: str) -> bool:
    """Detect VERIFY items that only describe what this retrieval did not cross-check."""
    has_crosscheck_gap = any(phrase in text for phrase in (
        "교차 확인하지",
        "교차 검증하지",
        "교차 확인 못",
        "cross-check하지",
        "cross check하지",
        "not cross-checked",
    ))
    has_retrieval_scope = any(term in text for term in (
        "retrieval",
        "retrieve",
        "이번 조회",
        "현재 조회",
        "이 조회",
    ))
    has_match_gap = (
        ("일치 여부" in text or "동일 여부" in text)
        and any(term in text for term in ("확인하지", "검증하지", "확인되지"))
    )
    return has_crosscheck_gap or (has_retrieval_scope and has_match_gap)


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
        r"(?:slack|github|notion|document).*(?:외|밖).*추가.*(?:확정|결정|설정)",
        r"교차\s*확인.*(?:않|못)",
        r"(?:현재|이번|이)\s*retrieval.*(?:확인하지|검증하지|확인되지)",
        r"(?:github|slack|notion|document).*(?:일치 여부|동일 여부).*(?:확인하지|검증하지|확인되지)",
        r"(?:외|밖).*추가.*(?:환경변수|설정값|일정).*(?:가정하지|추정하지|확정되어)",
        r"(?:환경변수|설정값|일정).*(?:모두|전부).*retrieval.*(?:가정하지|추정하지)",
        r"채널명만으로.*(?:단정|추정)",
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

    conflict_items = split_section_items(bodies.get("[UNRESOLVED CONFLICTS]", ""))
    resolved_conflict_items, split_promotions = _resolve_split_tentative_final_conflicts(conflict_items)

    promoted_must_know: list[str] = list(split_promotions)
    for item in conflict_items:
        text = _normalized(item)
        if _is_superseded_history(text):
            promoted = _extract_final_claim_from_superseded_conflict(item)
            if promoted:
                promoted_must_know.append(promoted)

    unresolved_candidates = [
        item for item in conflict_items
        if item not in resolved_conflict_items
        and not _is_absence_only_conflict(_normalized(item))
        and not _is_superseded_history(_normalized(item))
    ]
    if unresolved_candidates and _count_final_signals(unresolved_candidates) < 2:
        for item in unresolved_candidates:
            text = _normalized(item)
            if any(marker in text for marker in FINAL_MARKERS):
                cleaned = _clean_split_final_claim(item)
                if cleaned:
                    promoted_must_know.append(cleaned)
            resolved_conflict_items.add(item)

    def filtered(section: str) -> list[str]:
        kept: list[str] = []
        for item in split_section_items(bodies.get(section, "")):
            item = _strip_analysis_scope_prefix(item)
            if not item:
                continue
            text = _normalized(item)
            if text in {"- none", "none"}:
                continue

            semantic_sections = {
                "[MUST KNOW]",
                "[CONSTRAINTS]",
                "[USEFUL IF SPACE ALLOWS]",
                "[UNRESOLVED CONFLICTS]",
                "[VERIFY BEFORE USE]",
                "[DO NOT ASSUME]",
            }
            if section in semantic_sections and _looks_like_analysis_scope_metadata(text):
                continue

            if section in semantic_sections and _looks_like_retrieval_mechanics(text):
                continue

            if section in semantic_sections and _is_generic_retrieval_coverage(text):
                continue

            if section in semantic_sections and _is_low_value_retrieved_item(text):
                continue

            if section in semantic_sections and _is_provenance_only_item(text):
                continue

            if section in semantic_sections and _is_degenerate_semantic_label(text):
                continue

            if section == "[UNRESOLVED CONFLICTS]":
                if (
                    item in resolved_conflict_items
                    or _is_absence_only_conflict(text)
                    or _is_superseded_history(text)
                ):
                    continue

            if section == "[VERIFY BEFORE USE]":
                if (
                    _is_superseded_history(text)
                    or _is_hypothetical_reopening_gap(text)
                    or _is_crosscheck_only_verify(text)
                ):
                    continue

            if section == "[DO NOT ASSUME]":
                if (
                    _is_generic_coverage_disclaimer(text)
                    or _is_superseded_history(text)
                    or _is_hypothetical_reopening_gap(text)
                ):
                    continue

            kept.append(item)
        return kept

    rendered: list[str] = []
    for section in SECTION_NAMES:
        rendered.append(section)
        if section in {
            "[MUST KNOW]",
            "[CONSTRAINTS]",
            "[USEFUL IF SPACE ALLOWS]",
            "[UNRESOLVED CONFLICTS]",
            "[VERIFY BEFORE USE]",
            "[DO NOT ASSUME]",
        }:
            items = filtered(section)
            if section == "[MUST KNOW]" and promoted_must_know:
                normalized_existing = {_normalized(item) for item in items}
                for promoted in promoted_must_know:
                    if _normalized(promoted) not in normalized_existing:
                        items.append(promoted)
                        normalized_existing.add(_normalized(promoted))
            if section == "[MUST KNOW]":
                items = _dedupe_subsumed_items(items)
            rendered.extend(items if items else ["- None"])
        elif section == "[SOURCE MAP]":
            source_items = []
            for item in split_section_items(bodies.get(section, "")):
                text = _normalized(item)
                if _is_low_value_retrieved_item(text) or _is_runtime_provenance_summary(text):
                    continue
                source_items.append(item)
            rendered.extend(source_items if source_items else ["- None"])
        else:
            body = bodies.get(section, "").strip()
            rendered.append(body if body else "- None")
        if section != SECTION_NAMES[-1]:
            rendered.append("")

    return "\n".join(rendered).strip()
