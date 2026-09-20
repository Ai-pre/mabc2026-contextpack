import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


ANSI_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)


class HermesExecutionError(RuntimeError):
    pass


class HermesTimeoutError(TimeoutError):
    pass


@dataclass
class HermesRunResult:
    duration_sec: float
    handoff: str
    skill_used: bool
    mcp_tool_calls: int
    mcp_tools: list[str]


class CliHermesRunner:
    def __init__(self, timeout_sec: int = 300):
        self.timeout_sec = timeout_sec
        self.project_root = Path(__file__).resolve().parent

        self.hermes_bin = (
            os.getenv("HERMES_BIN")
            or shutil.which("hermes")
        )

        if not self.hermes_bin:
            raise RuntimeError(
                "hermes executable을 찾을 수 없습니다. "
                "PATH 또는 HERMES_BIN을 확인하세요."
            )

    def run(
        self,
        prompt: str,
        workspace_id: str | None = None,
        github_enabled: bool = False,
    ) -> HermesRunResult:
        # Do not pass --toolsets here.
        #
        # Hermes 0.21 can fail to expose dynamically discovered stdio MCP tools
        # when an explicit dynamic MCP toolset is selected for a oneshot
        # session. The default CLI toolset path correctly injects configured
        # MCP tools after discovery. ContextPack constrains source use in the
        # prompt instead.
        cmd = [
            self.hermes_bin,
            "chat",
            "--oneshot",
            "--skills",
            "context-pack",
            "--max-turns",
            "8",
            "-q",
            prompt,
        ]

        env = os.environ.copy()

        if workspace_id:
            env["CONTEXTPACK_WORKSPACE_ID"] = workspace_id

        started = time.perf_counter()

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_sec,
                shell=False,
                env=env,
            )

        except subprocess.TimeoutExpired as exc:
            raise HermesTimeoutError(
                f"Hermes execution timed out after "
                f"{self.timeout_sec}s"
            ) from exc

        duration = time.perf_counter() - started

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()

            raise HermesExecutionError(
                f"Hermes exited with code "
                f"{proc.returncode}\n"
                f"stderr:\n{stderr[-3000:]}\n"
                f"stdout tail:\n{stdout[-1500:]}"
            )

        # 실제 Handoff 후보는 stdout에서 추출
        clean_stdout = self._clean_output(
            proc.stdout or ""
        )

        # MCP trace는 stdout / stderr 양쪽에서 나올 수 있음
        trace_text = self._clean_output(
            (proc.stdout or "")
            + "\n"
            + (proc.stderr or "")
        )

        handoff = self._extract_handoff(
            clean_stdout
        )

        if not handoff:
            required_sections = [
                "[TASK]",
                "[MUST KNOW]",
                "[CONSTRAINTS]",
                "[USEFUL IF SPACE ALLOWS]",
                "[UNRESOLVED CONFLICTS]",
                "[VERIFY BEFORE USE]",
                "[DO NOT ASSUME]",
                "[SOURCE MAP]",
            ]
            detected = [
                section
                for section in required_sections
                if re.search(self._section_pattern(section), clean_stdout)
            ]
            missing = [
                section for section in required_sections
                if section not in detected
            ]
            detail = (
                " Missing sections: " + ", ".join(missing)
                if missing
                else " All section headers were detected; output contained invalid runtime/tool trace ordering."
            )
            raise HermesExecutionError(
                "Hermes succeeded but Handoff Context could not be extracted."
                + detail
            )

        # 이 값은 stdout에서 activation을 추측하는 값이 아니라
        # 실제 Hermes 실행 명령에 context-pack Skill이 enabled되어
        # 있는지를 나타낸다.
        skill_enabled = (
            "--skills" in cmd
            and "context-pack" in cmd
        )

        mcp_tool_calls = self._count_mcp_calls(trace_text)
        mcp_tools = self._extract_mcp_tools(trace_text)

        if mcp_tool_calls == 0:
            raise HermesExecutionError(
                "Hermes produced a Handoff without reading any registered Source. "
                "ContextPack refuses source-free handoffs; retry the analysis."
            )

        return HermesRunResult(
            duration_sec=round(duration, 2),
            handoff=handoff,
            skill_used=skill_enabled,
            mcp_tool_calls=mcp_tool_calls,
            mcp_tools=mcp_tools,
        )

    @staticmethod
    def _clean_output(text: str) -> str:
        text = ANSI_RE.sub("", text)

        return (
            text
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    @staticmethod
    def _clean_cli_noise(text: str) -> str:
        """
        Hermes CLI 표시용 noise만 제거한다.
        Handoff 내용 자체는 생성하거나 수정하지 않는다.
        """

        # 예:
        # ... omitted 13 diff line(s) across ...
        text = re.sub(
            r"(?im)^"
            r"\s*\.\.\.\s*"
            r"omitted\s+\d+\s+diff line\(s\)"
            r".*$",
            "",
            text,
        )

        # standalone Hermes separator/signature
        #
        # — Hermes
        # ──── Hermes
        # ? Hermes
        text = re.sub(
            r"(?im)^\s*[-—─?]+\s*Hermes\s*$",
            "",
            text,
        )

        return text

    @staticmethod
    def _section_pattern(section: str) -> str:
        """Accept exact headers plus harmless Markdown/CLI decoration."""
        return (
            r"(?m)^\s*[│┃]?\s*"
            r"(?:#{1,6}\s*)?"
            r"(?:\*\*)?\s*"
            + re.escape(section)
            + r"\s*(?:\*\*)?\s*:?\s*$"
        )

    @staticmethod
    def _extract_mcp_tools(text: str) -> list[str]:
        """Return the most specific MCP tool names observed in stdout.

        Hermes 0.21 may visually truncate a trace label (for example
        `mcp__mabc`) while the final SOURCE MAP contains the full callable
        identifier. Prefer full `mcp__server__tool` names when available.
        """
        full = re.findall(
            r"(?i)\b(mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+)\b",
            text,
        )
        if full:
            return list(dict.fromkeys(full))

        # Hermes may print only `mcp__mabc` in the trace while the Handoff
        # mentions the aggregate retriever by its leaf name. When exactly one
        # known aggregate retriever is present, recover the full callable name
        # for UI traceability instead of exposing the truncated server label.
        aggregate_leafs = re.findall(
            r"(?i)\b(demo_context_retrieve|document_retrieve|github_retrieve|slack_retrieve|notion_retrieve)\b",
            text,
        )
        aggregate_leafs = list(dict.fromkeys(name.lower() for name in aggregate_leafs))
        if len(aggregate_leafs) == 1:
            return [f"mcp__mabc_sources__{aggregate_leafs[0]}"]

        trace = re.findall(
            r"(?mi)^.*⚡\s+(mcp[^\s(]+)",
            text,
        )
        return list(dict.fromkeys(name.rstrip(",:;") for name in trace))

    @staticmethod
    def _count_mcp_calls(text: str) -> int:
        """Count actual MCP call trace events, independent of display names."""
        return len(
            re.findall(
                r"(?mi)^.*⚡\s+mcp(?:__|_)",
                text,
            )
        )

    @staticmethod
    def _split_section_items(body: str) -> list[str]:
        """Split a section body into bullet items while preserving continuations."""
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

    @staticmethod
    def _sanitize_handoff(handoff: str) -> str:
        """Remove retrieval-process leakage and finality contradictions.

        This is intentionally conservative: it only deletes known execution
        metadata / generic coverage disclaimers, or VERIFY items where an
        explicitly tentative option is superseded by an explicit final decision
        with no reopening signal. It never invents replacement facts.
        """
        section_names = [
            "[TASK]",
            "[MUST KNOW]",
            "[CONSTRAINTS]",
            "[USEFUL IF SPACE ALLOWS]",
            "[UNRESOLVED CONFLICTS]",
            "[VERIFY BEFORE USE]",
            "[DO NOT ASSUME]",
            "[SOURCE MAP]",
        ]
        header_re = re.compile(
            r"(?m)^(" + "|".join(re.escape(name) for name in section_names) + r")\s*$"
        )
        matches = list(header_re.finditer(handoff))
        if [match.group(1) for match in matches] != section_names:
            return handoff

        bodies: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(handoff)
            bodies[match.group(1)] = handoff[match.end():end].strip()

        def kept_items(section: str) -> list[str]:
            items = CliHermesRunner._split_section_items(bodies.get(section, ""))
            kept: list[str] = []

            for item in items:
                normalized = re.sub(r"\s+", " ", item).casefold()

                if section == "[CONSTRAINTS]":
                    retrieval_markers = (
                        "workspace_id",
                        "mcp__",
                        "github_retrieve",
                        "slack_retrieve",
                        "notion_retrieve",
                        "document_retrieve",
                        "등록된 source",
                        "등록 source",
                        "다른 채널이나 workspace",
                        "다른 workspace",
                        "근거로만 제한",
                        "탐색하지 않는다",
                        "연결된 live github repository가 없",
                        "연결된 live notion page가 없",
                    )
                    if any(marker in normalized for marker in retrieval_markers):
                        continue

                if section == "[VERIFY BEFORE USE]":
                    tentative_markers = (
                        "논의 중", "검토 중", "제안", "초안", "예정", "후보", "고려 중"
                    )
                    final_markers = (
                        "최종 결정", "최종 확정", "확정했", "확정됨", "승인", "적용 결정", "취소"
                    )
                    reopening_markers = (
                        "재논의", "재검토", "결정 보류", "번복", "reopen", "reopening"
                    )
                    if (
                        any(marker in normalized for marker in tentative_markers)
                        and any(marker in normalized for marker in final_markers)
                        and not any(marker in normalized for marker in reopening_markers)
                    ):
                        continue

                if section == "[DO NOT ASSUME]":
                    generic_patterns = (
                        r"위 메시지 외.*추가 논의",
                        r"retrieve 결과.*추가 논의",
                        r"retrieve 결과.*더 많은",
                        r"다른 채널.*논의",
                        r"현재 (?:조회|retrieve) 범위 밖",
                        r"접근하지 않은 source.*가능",
                        r"추가 확인이 안전",
                    )
                    if any(re.search(pattern, normalized) for pattern in generic_patterns):
                        continue

                if normalized not in {"- none", "none"}:
                    kept.append(item)

            return kept

        rendered: list[str] = []
        for section in section_names:
            rendered.append(section)
            if section in {"[CONSTRAINTS]", "[VERIFY BEFORE USE]", "[DO NOT ASSUME]"}:
                items = kept_items(section)
                rendered.extend(items if items else ["- None"])
            else:
                body = bodies.get(section, "").strip()
                rendered.append(body if body else "- None")
            if section != section_names[-1]:
                rendered.append("")

        return "\n".join(rendered).strip()

    @staticmethod
    def _extract_handoff(text: str) -> str:
        """
        Hermes stdout에서 완성된 ContextPack Handoff만 추출한다.

        prompt echo / runtime trace / 불완전한 template은 제외하고,
        최종 Handoff 뒤에 붙는 Hermes session 안내는 잘라낸다.
        Hermes 0.21+가 Markdown heading/bold로 section header를 꾸며도
        동일한 Handoff contract로 정규화한다.
        """

        cleaned = CliHermesRunner._clean_cli_noise(text)

        required_sections = [
            "[TASK]",
            "[MUST KNOW]",
            "[CONSTRAINTS]",
            "[USEFUL IF SPACE ALLOWS]",
            "[UNRESOLVED CONFLICTS]",
            "[VERIFY BEFORE USE]",
            "[DO NOT ASSUME]",
            "[SOURCE MAP]",
        ]
        patterns = {
            section: CliHermesRunner._section_pattern(section)
            for section in required_sections
        }

        task_matches = list(
            re.finditer(
                patterns["[TASK]"],
                cleaned,
            )
        )

        if not task_matches:
            return ""

        # 가장 뒤쪽 [TASK]부터 검사한다.
        for index in range(len(task_matches) - 1, -1, -1):
            start = task_matches[index].start()

            if index + 1 < len(task_matches):
                end = task_matches[index + 1].start()
                candidate = cleaned[start:end].strip()
            else:
                candidate = cleaned[start:].strip()

            # 1. ContextPack 8개 section 존재 + 순서 확인
            positions = []
            valid = True

            for section in required_sections:
                match = re.search(
                    patterns[section],
                    candidate,
                )

                if not match:
                    valid = False
                    break

                positions.append(match.start())

            if not valid:
                continue

            if positions != sorted(positions):
                continue

            # 2. SOURCE MAP 이후는 Handoff가 끝난 뒤 붙는
            #    Hermes CLI commentary/session 영역일 수 있다.
            source_map_match = re.search(
                patterns["[SOURCE MAP]"],
                candidate,
            )

            if not source_map_match:
                continue

            search_from = source_map_match.end()
            remainder = candidate[search_from:]

            commentary_patterns = [
                # Hermes CLI closing / signature
                r"(?mi)^\s*[-—─?]+\s*Hermes\s*$",

                # Runtime/tool trace that may be printed after the final answer.
                # Match trace-shaped lines only; ordinary SOURCE MAP text is allowed
                # to mention names such as mcp__mabc_sources__demo_context_retrieve.
                r"(?mi)^\s*Initializing agent\.\.\..*$",
                r"(?mi)^\s*[│┃]?\s*[⚡🔧🧰🔍]\s+.*$",
                r"(?mi)^\s*[│┃]?\s*preparing(?:_|\s).*$",
                r"(?mi)^\s*Local tools require one entry per tool_call.*$",

                # 응답 뒤 commentary
                r"(?mi)^\s*ContextPack\s+(?:완성|complete|ready)\b.*$",
                r"(?mi)^\s*파일:.*$",
                r"(?mi)^\s*추가로 업로드\b.*$",

                # CLI session metadata
                r"(?mi)^\s*Resume this session with:.*$",
                r"(?mi)^\s*Session:.*$",
                r"(?mi)^\s*Title:.*$",
                r"(?mi)^\s*Duration:.*$",
                r"(?mi)^\s*Messages:.*$",
            ]

            cut_positions = []

            for pattern in commentary_patterns:
                match = re.search(
                    pattern,
                    remainder,
                )

                if match:
                    cut_positions.append(
                        search_from + match.start()
                    )

            if cut_positions:
                candidate = candidate[
                    :min(cut_positions)
                ].rstrip()

            # 3. 이제 Handoff 본문 내부에 runtime/tool trace가
            #    섞였는지만 검사한다.
            runtime_line_patterns = [
                r"(?mi)^\s*Initializing agent\.\.\..*$",
                r"(?mi)^\s*[│┃]?\s*[⚡🔧🧰🔍]\s+.*$",
                r"(?mi)^\s*[│┃]?\s*preparing(?:_|\s).*$",
                r"(?mi)^\s*\|\s*DSML\s*\|.*$",
                r"(?mi)^\s*Local tools require one entry per tool_call.*$",
            ]

            if any(
                re.search(pattern, candidate)
                for pattern in runtime_line_patterns
            ):
                continue

            # 4. Header decoration을 고정 Handoff contract로 정규화한다.
            for section in required_sections:
                candidate = re.sub(
                    patterns[section],
                    section,
                    candidate,
                )

            # 5. Hermes box border 제거
            lines = []

            for line in candidate.splitlines():
                stripped = line.lstrip()

                if stripped.startswith("╰"):
                    continue

                if stripped.startswith("╭"):
                    continue

                lines.append(line)

            result = "\n".join(lines).strip()

            if result:
                return CliHermesRunner._sanitize_handoff(result)

        return ""