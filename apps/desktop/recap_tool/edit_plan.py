from __future__ import annotations

from typing import Any


class SmoothEditPlanError(ValueError):
    pass


def validate_smooth_edit_plan(document: dict[str, Any], *, content_type: str) -> None:
    """Validate pacing decisions before a recap reaches the renderer.

    These rules are content-agnostic. They inspect only timing, clip density and
    requested visual speed, so they apply to future TV episodes regardless of
    series, characters, genre or source duration.
    """
    if str(content_type).upper() == "BODYCAM":
        return

    failures: list[str] = []
    for output in document.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        output_id = str(output.get("render_id") or "unknown-output")
        segments = [item for item in output.get("segments") or [] if isinstance(item, dict)]
        ordered = sorted(segments, key=lambda item: int(item.get("order") or 0))

        for position, segment in enumerate(ordered):
            segment_id = str(segment.get("segment_id") or "unknown-segment")
            segment_type = str(segment.get("segment_type") or "")
            purpose = str(segment.get("purpose") or "").upper()
            clips = [clip for clip in segment.get("source_clips") or [] if isinstance(clip, dict)]
            clip_count = len(clips)
            durations = [
                max(0.0, float(clip.get("end_ms") or 0) - float(clip.get("start_ms") or 0))
                for clip in clips
            ]
            visual_duration = sum(durations)
            try:
                speed = float(segment.get("recommended_visual_speed") or 0)
            except (TypeError, ValueError):
                speed = 0.0

            if segment_type == "original_dialogue":
                if speed and abs(speed - 1.0) > 0.0001:
                    failures.append(f"{output_id}/{segment_id}: original dialogue phải giữ speed 1.0.")
                if position == 0 and clip_count != 1:
                    failures.append(
                        f"{output_id}/{segment_id}: hook phải là một source range liên tục, không phải {clip_count} clips."
                    )
                continue

            if segment_type != "narration":
                continue

            if clip_count > 4:
                failures.append(
                    f"{output_id}/{segment_id}: có {clip_count} clips; tối đa 4 và thông thường chỉ 1–2."
                )
            if clip_count > 1 and visual_duration / clip_count < 2_000:
                failures.append(
                    f"{output_id}/{segment_id}: shot trung bình dưới 2 giây; hãy dùng cảnh dài, liền mạch hơn."
                )
            if clip_count > 2 and any(duration < 1_500 for duration in durations):
                failures.append(
                    f"{output_id}/{segment_id}: có micro-clip dưới 1,5 giây trong chuỗi nhiều clip."
                )
            if not 0.90 <= speed <= 1.10:
                failures.append(
                    f"{output_id}/{segment_id}: recommended_visual_speed={speed:.4f}; phải nằm trong 0.90–1.10."
                )
            if purpose == "ORIGINAL_CONCLUSION" and clip_count > 3:
                failures.append(f"{output_id}/{segment_id}: phần kết chỉ được dùng tối đa 3 clips.")

            # At most three cuts may occur in any rolling five-second window.
            # Use the planned rendered duration after the requested speed.
            if speed > 0 and clip_count > 1:
                boundaries: list[float] = []
                cursor = 0.0
                for duration in durations[:-1]:
                    cursor += duration / 1000.0 / speed
                    boundaries.append(cursor)
                for start_index, boundary in enumerate(boundaries):
                    cuts = sum(boundary <= value < boundary + 5.0 for value in boundaries[start_index:])
                    if cuts > 3:
                        failures.append(
                            f"{output_id}/{segment_id}: có hơn 3 cuts trong một khoảng 5 giây."
                        )
                        break

    if failures:
        raise SmoothEditPlanError("Smooth-edit gate chưa đạt: " + " ".join(failures[:12]))
