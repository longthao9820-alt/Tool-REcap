from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .models import ProjectData


# Internal editorial guardrails. They help keep original commentary central but
# are not thresholds published or guaranteed by Facebook.
EDITORIAL_PURPOSES = frozenset(
    {
        "ORIGINAL_HOOK",
        "CONTEXT",
        "CAUSAL_ANALYSIS",
        "CHARACTER_ANALYSIS",
        "RELATIONSHIP_CHANGE",
        "HIDDEN_DETAIL",
        "COMPARISON",
        "CRITIQUE",
        "INTERPRETATION",
        "CONSEQUENCE",
        "THEMATIC_INSIGHT",
        "ORIGINAL_CONCLUSION",
        "ORIGINAL_DIALOGUE_EVIDENCE",
    }
)

DESCRIPTIVE_PURPOSES = frozenset({"RECAP", "PLOT_DESCRIPTION", "CHRONOLOGY", "SUMMARY"})


@dataclass(frozen=True)
class OriginalityIssue:
    severity: str
    code: str
    message: str
    output_id: str = ""
    segment_id: str = ""


@dataclass(frozen=True)
class OriginalityReport:
    issues: tuple[OriginalityIssue, ...]
    metrics: tuple[dict[str, Any], ...]

    @property
    def blocking_issues(self) -> tuple[OriginalityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "BLOCK")

    @property
    def passes(self) -> bool:
        return not self.blocking_issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes_internal_guardrails": self.passes,
            "disclaimer": "Internal editorial checks only; not a Facebook originality certification.",
            "metrics": list(self.metrics),
            "issues": [asdict(issue) for issue in self.issues],
        }


def _purpose(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def _clip_duration_ms(segment: dict[str, Any]) -> int:
    return round(
        sum(
            max(0.0, float(clip.get("end_ms") or 0) - float(clip.get("start_ms") or 0))
            for clip in segment.get("source_clips") or []
            if isinstance(clip, dict)
        )
    )


def _selected_outputs(raw: dict[str, Any], recap_mode: str | None) -> Iterable[dict[str, Any]]:
    desired = None
    if recap_mode == "FULL_EPISODE":
        desired = "FULL_RECAP"
    elif recap_mode == "MAIN_STORIES":
        desired = "MAIN_STORY"
    for output in raw.get("outputs") or []:
        if isinstance(output, dict) and (desired is None or output.get("type") == desired):
            yield output


def audit_project(project: ProjectData, *, recap_mode: str | None = None) -> OriginalityReport:
    return audit_outputs(
        project.raw,
        recap_mode=recap_mode,
        tv_show=project.content_type == "US_TV_SHOW",
    )


def audit_episode(episode: dict[str, Any], *, content_type: str, recap_mode: str | None = None) -> OriginalityReport:
    return audit_outputs(episode, recap_mode=recap_mode, tv_show=content_type == "US_TV_SHOW")


def audit_outputs(raw: dict[str, Any], *, recap_mode: str | None, tv_show: bool) -> OriginalityReport:
    issues: list[OriginalityIssue] = []
    metrics: list[dict[str, Any]] = []
    if not tv_show:
        return OriginalityReport(tuple(), tuple())

    rights = raw.get("source_rights")
    rights_status = str(rights.get("status") if isinstance(rights, dict) else "UNVERIFIED").upper()
    if rights_status not in {"OWNED", "LICENSED", "FIRST_PUBLICATION_RIGHTS"}:
        issues.append(
            OriginalityIssue(
                "WARN",
                "SOURCE_RIGHTS_UNVERIFIED",
                "Quyền sử dụng footage chưa được người dùng xác nhận; originality không thay thế kiểm tra bản quyền.",
            )
        )

    outputs = list(_selected_outputs(raw, recap_mode))
    if not outputs:
        issues.append(OriginalityIssue("BLOCK", "NO_OUTPUT", "Không có output TV Show phù hợp để kiểm tra."))
        return OriginalityReport(tuple(issues), tuple(metrics))

    for output in outputs:
        output_id = str(output.get("render_id") or "unknown-output")
        narration_ms = 0
        dialogue_ms = 0
        narration_count = 0
        analytical_count = 0
        purposes: set[str] = set()
        segments = [item for item in output.get("segments") or [] if isinstance(item, dict)]
        for segment in segments:
            segment_id = str(segment.get("segment_id") or "unknown-segment")
            segment_type = str(segment.get("segment_type") or "")
            purpose = _purpose(segment.get("purpose"))
            purposes.add(purpose)
            visual_ms = int(segment.get("source_visual_duration_ms") or _clip_duration_ms(segment))

            if segment_type == "narration":
                narration_count += 1
                narration_ms += int(segment.get("estimated_voice_duration_ms") or visual_ms)
                editorial_value = str(segment.get("editorial_value") or "").strip()
                if purpose in EDITORIAL_PURPOSES and purpose != "ORIGINAL_DIALOGUE_EVIDENCE":
                    analytical_count += 1
                else:
                    issues.append(
                        OriginalityIssue(
                            "BLOCK",
                            "NON_EDITORIAL_PURPOSE",
                            f"{segment_id} chưa có editorial purpose phân tích hợp lệ.",
                            output_id,
                            segment_id,
                        )
                    )
                if purpose in DESCRIPTIVE_PURPOSES:
                    issues.append(
                        OriginalityIssue(
                            "BLOCK",
                            "PLOT_DESCRIPTION_ONLY",
                            f"{segment_id} chỉ được gắn mục đích kể lại cốt truyện.",
                            output_id,
                            segment_id,
                        )
                    )
                if not editorial_value:
                    issues.append(
                        OriginalityIssue(
                            "BLOCK",
                            "MISSING_EDITORIAL_VALUE",
                            f"{segment_id} chưa nêu giá trị mới mà lời bình bổ sung.",
                            output_id,
                            segment_id,
                        )
                    )
                longest_clip = max(
                    (
                        float(clip.get("end_ms") or 0) - float(clip.get("start_ms") or 0)
                        for clip in segment.get("source_clips") or []
                        if isinstance(clip, dict)
                    ),
                    default=0.0,
                )
                if longest_clip > 7_000:
                    issues.append(
                        OriginalityIssue(
                            "WARN",
                            "LONG_THIRD_PARTY_CLIP",
                            f"{segment_id} có clip minh họa dài hơn 7 giây; hãy xác nhận độ dài này cần thiết cho luận điểm.",
                            output_id,
                            segment_id,
                        )
                    )
            elif segment_type == "original_dialogue":
                dialogue_ms += visual_ms
                if purpose != "ORIGINAL_DIALOGUE_EVIDENCE":
                    issues.append(
                        OriginalityIssue(
                            "BLOCK",
                            "DIALOGUE_WITHOUT_EVIDENCE_PURPOSE",
                            f"{segment_id} giữ audio gốc nhưng không xác định vai trò bằng chứng.",
                            output_id,
                            segment_id,
                        )
                    )
                if visual_ms > 15_000:
                    issues.append(
                        OriginalityIssue(
                            "BLOCK",
                            "LONG_ORIGINAL_DIALOGUE",
                            f"{segment_id} giữ hơn 15 giây audio gốc liên tục.",
                            output_id,
                            segment_id,
                        )
                    )

        total_ms = narration_ms + dialogue_ms
        commentary_ratio = narration_ms / total_ms if total_ms else 0.0
        metrics.append(
            {
                "render_id": output_id,
                "narration_segments": narration_count,
                "editorial_narration_segments": analytical_count,
                "narration_duration_ms": narration_ms,
                "original_dialogue_duration_ms": dialogue_ms,
                "commentary_ratio": round(commentary_ratio, 4),
                "editorial_purposes": sorted(purpose for purpose in purposes if purpose),
            }
        )
        if narration_count == 0:
            issues.append(OriginalityIssue("BLOCK", "NO_COMMENTARY", f"{output_id} không có original commentary.", output_id))
        elif analytical_count != narration_count:
            issues.append(
                OriginalityIssue(
                    "BLOCK",
                    "COMMENTARY_NOT_CONSISTENT",
                    f"{output_id} còn narration không thể hiện giá trị phân tích mới.",
                    output_id,
                )
            )
        if commentary_ratio < 0.60:
            issues.append(
                OriginalityIssue(
                    "BLOCK",
                    "COMMENTARY_NOT_CENTRAL",
                    f"{output_id} có tỷ trọng commentary dự kiến {commentary_ratio:.0%}, thấp hơn guardrail nội bộ 60%.",
                    output_id,
                )
            )
        analytical_purposes = purposes & EDITORIAL_PURPOSES - {"ORIGINAL_DIALOGUE_EVIDENCE"}
        if narration_count >= 2 and len(analytical_purposes) < 2:
            issues.append(
                OriginalityIssue(
                    "BLOCK",
                    "LOW_EDITORIAL_VARIETY",
                    f"{output_id} cần ít nhất hai loại giá trị biên tập khác nhau.",
                    output_id,
                )
            )

    return OriginalityReport(tuple(issues), tuple(metrics))
