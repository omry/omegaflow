"""Media-neutral timing constraints and presentation materialization."""

from __future__ import annotations

import heapq
import hashlib
import json
import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .presentation import (
    serialize_browser_payload,
    validate_browser_payload,
)
from .presentation_schema import PresentationAudioIntervalV1
from .recording_plan import (
    BeatPlan,
    BrowserActionPlan,
    FrozenMapping,
    NarrationTakePlan,
    RecordingPlan,
    TerminalActionPlan,
    terminal_action_id,
)


class PresentationCompileError(RuntimeError):
    """Compilation failure with a stable user-facing code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def milliseconds_half_up(value: int | float | Decimal) -> int:
    """Round one finite non-negative millisecond value exactly once."""

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError("millisecond value must be numeric")
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if not decimal.is_finite() or decimal < 0:
        raise ValueError("millisecond value must be finite and non-negative")
    return int(decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ConstraintEdge:
    before: str
    after: str
    gap_ms: int
    reason: str


@dataclass(frozen=True)
class ConstraintSolution:
    times_ms: Mapping[str, int]
    order: tuple[str, ...]

    def time(self, node: str) -> int:
        try:
            return self.times_ms[node]
        except KeyError as exc:
            raise KeyError(f"unknown constraint node {node!r}") from exc


class ConstraintGraph:
    """A deterministic DAG of lower-bound millisecond constraints."""

    def __init__(self) -> None:
        self._minimums: dict[str, int] = {}
        self._order: dict[str, int] = {}
        self._edges: list[ConstraintEdge] = []

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(self._minimums)

    @property
    def edges(self) -> tuple[ConstraintEdge, ...]:
        return tuple(self._edges)

    def add_node(self, node: str, *, minimum_ms: int = 0) -> None:
        if not isinstance(node, str) or not node:
            raise ValueError("constraint node must be a non-empty string")
        minimum = milliseconds_half_up(minimum_ms)
        if node not in self._minimums:
            self._order[node] = len(self._order)
            self._minimums[node] = minimum
        else:
            self._minimums[node] = max(self._minimums[node], minimum)

    def constrain(
        self,
        before: str,
        after: str,
        *,
        gap_ms: int = 0,
        reason: str,
    ) -> None:
        if not isinstance(reason, str) or not reason:
            raise ValueError("constraint reason must be a non-empty string")
        self.add_node(before)
        self.add_node(after)
        self._edges.append(
            ConstraintEdge(
                before=before,
                after=after,
                gap_ms=milliseconds_half_up(gap_ms),
                reason=reason,
            )
        )

    def solve(self) -> ConstraintSolution:
        adjacency: dict[str, list[ConstraintEdge]] = {
            node: [] for node in self._minimums
        }
        indegree = {node: 0 for node in self._minimums}
        for edge in self._edges:
            adjacency[edge.before].append(edge)
            indegree[edge.after] += 1
        for edges in adjacency.values():
            edges.sort(
                key=lambda edge: (
                    self._order[edge.after],
                    edge.gap_ms,
                    edge.reason,
                )
            )

        ready = [
            (self._order[node], node) for node, degree in indegree.items() if degree == 0
        ]
        heapq.heapify(ready)
        times = dict(self._minimums)
        order: list[str] = []
        while ready:
            _, node = heapq.heappop(ready)
            order.append(node)
            for edge in adjacency[node]:
                times[edge.after] = max(
                    times[edge.after], times[node] + edge.gap_ms
                )
                indegree[edge.after] -= 1
                if indegree[edge.after] == 0:
                    heapq.heappush(
                        ready, (self._order[edge.after], edge.after)
                    )

        if len(order) != len(self._minimums):
            cycle = self._shortest_cycle(adjacency)
            chain = " -> ".join(cycle) if cycle else "unknown dependency cycle"
            raise PresentationCompileError(
                "PRESENTATION_CYCLE", f"timing dependency cycle: {chain}"
            )
        return ConstraintSolution(
            times_ms=MappingProxyType(times),
            order=tuple(order),
        )

    def _shortest_cycle(
        self, adjacency: Mapping[str, list[ConstraintEdge]]
    ) -> tuple[str, ...]:
        best: tuple[str, ...] | None = None
        for start in self._minimums:
            queue: deque[tuple[str, tuple[str, ...]]] = deque(
                (edge.after, (start, edge.after)) for edge in adjacency[start]
            )
            visited = {start}
            while queue:
                node, path = queue.popleft()
                if node == start:
                    if best is None or (len(path), path) < (len(best), best):
                        best = path
                    break
                if node in visited or (best is not None and len(path) >= len(best)):
                    continue
                visited.add(node)
                queue.extend(
                    (edge.after, (*path, edge.after))
                    for edge in adjacency[node]
                )
        return best or ()


def solved_intervals(
    solution: ConstraintSolution,
    pairs: Iterable[tuple[str, str]],
) -> tuple[tuple[int, int], ...]:
    """Resolve and validate half-open intervals from a solved graph."""

    intervals = []
    for start_node, end_node in pairs:
        start = solution.time(start_node)
        end = solution.time(end_node)
        if end < start:
            raise PresentationCompileError(
                "PRESENTATION_CYCLE",
                f"negative solved interval {start_node!r} to {end_node!r}",
            )
        intervals.append((start, end))
    return tuple(intervals)


@dataclass(frozen=True)
class CompiledBeatTiming:
    id: str
    offset_ms: int
    duration_ms: int


@dataclass(frozen=True)
class CompiledActionTiming:
    beat_id: str
    action_id: str
    presentation_start_ms: int
    presentation_end_ms: int
    local_start_ms: int
    local_end_ms: int


@dataclass(frozen=True)
class CompiledRecordingTiming:
    duration_ms: int
    beats: tuple[CompiledBeatTiming, ...]
    actions: tuple[CompiledActionTiming, ...]
    anchor_times_ms: Mapping[tuple[str, str], int]
    audio_intervals: tuple[PresentationAudioIntervalV1, ...]

    def beat(self, beat_id: str) -> CompiledBeatTiming:
        for beat in self.beats:
            if beat.id == beat_id:
                return beat
        raise KeyError(f"unknown compiled beat {beat_id!r}")

    def action(self, beat_id: str, action_id: str) -> CompiledActionTiming:
        for action in self.actions:
            if action.beat_id == beat_id and action.action_id == action_id:
                return action
        raise KeyError(f"unknown compiled action {beat_id!r}/{action_id!r}")


@dataclass(frozen=True)
class _TakeTimestamps:
    duration_ms: int
    members: Mapping[str, tuple[int, int]]
    anchors: Mapping[tuple[str, str], int]
    waits: tuple[tuple[str, str, int, int, int], ...]


@dataclass(frozen=True)
class _ScheduledAction:
    beat_id: str
    action_id: str
    start_node: str
    end_node: str


def compile_recording_timing(
    plan: RecordingPlan,
    *,
    timestamp_sidecars: Mapping[str, Mapping[str, Any]],
    action_durations_ms: Mapping[tuple[str, str], int] | None = None,
    beat_visual_durations_ms: Mapping[str, int] | None = None,
    take_source_starts_ms: Mapping[str, int] | None = None,
) -> CompiledRecordingTiming:
    """Solve one recording timeline without inserting unauthored audio pauses."""

    action_durations_ms = (
        {} if action_durations_ms is None else action_durations_ms
    )
    beat_visual_durations_ms = (
        {} if beat_visual_durations_ms is None else beat_visual_durations_ms
    )
    sidecars = {
        take.id: _validate_take_timestamps(
            take,
            timestamp_sidecars.get(take.id),
        )
        for take in plan.narration_takes
    }
    source_starts = _take_source_starts(
        plan.narration_takes,
        sidecars,
        take_source_starts_ms,
    )
    graph = ConstraintGraph()
    beat_by_id = {beat.id: beat for beat in plan.beats}
    beat_start_nodes = {beat.id: f"beat:{beat.id}:start" for beat in plan.beats}
    for node in beat_start_nodes.values():
        graph.add_node(node)
    if plan.beats:
        graph.add_node(beat_start_nodes[plan.beats[0].id], minimum_ms=0)

    take_point_nodes: dict[tuple[str, int], str] = {}
    anchor_nodes: dict[tuple[str, str], str] = {}
    wait_resume_nodes: dict[tuple[str, str, int], str] = {}
    member_end_nodes: dict[str, str] = {}
    take_end_nodes: dict[str, str] = {}
    cross_boundaries: set[tuple[str, str]] = set()
    guided_beat_ids = frozenset(
        beat.id for beat in plan.beats if beat.guide is not None
    )

    for take in plan.narration_takes:
        timestamps = sidecars[take.id]
        _add_take_audio_constraints(
            graph,
            take,
            timestamps,
            beat_start_nodes=beat_start_nodes,
            take_point_nodes=take_point_nodes,
            anchor_nodes=anchor_nodes,
            wait_resume_nodes=wait_resume_nodes,
            member_end_nodes=member_end_nodes,
            take_end_nodes=take_end_nodes,
            guided_beat_ids=guided_beat_ids,
        )
        for previous, following in zip(take.members, take.members[1:]):
            cross_boundaries.add((previous.beat_id, following.beat_id))

    scheduled_actions: list[_ScheduledAction] = []
    action_end_nodes: dict[tuple[str, str], str] = {}
    visual_end_nodes: dict[str, str] = {}
    for beat in plan.beats:
        scheduled = _add_beat_action_constraints(
            graph,
            beat,
            beat_start_node=beat_start_nodes[beat.id],
            anchor_nodes=anchor_nodes,
            action_durations_ms=action_durations_ms,
        )
        scheduled_actions.extend(scheduled)
        for action in scheduled:
            action_end_nodes[(action.beat_id, action.action_id)] = action.end_node
        visual_end = f"beat:{beat.id}:visual-end"
        graph.constrain(
            beat_start_nodes[beat.id],
            visual_end,
            gap_ms=_duration_value(
                beat_visual_durations_ms.get(beat.id, 0),
                field=f"visual duration for beat {beat.id!r}",
            ),
            reason=f"visual baseline for beat {beat.id}",
        )
        for action in scheduled:
            graph.constrain(
                action.end_node,
                visual_end,
                reason=f"visual completion for beat {beat.id}",
            )
        visual_end_nodes[beat.id] = visual_end

    for take in plan.narration_takes:
        timestamps = sidecars[take.id]
        for wait_index, wait in enumerate(take.waits):
            target = (wait.beat_id, wait.target)
            target_node = action_end_nodes.get(target)
            if target_node is None:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA",
                    f"narration wait target {wait.target!r} in beat "
                    f"{wait.beat_id!r} has no timing milestone",
                )
            resume = wait_resume_nodes[(take.id, wait.target, wait_index)]
            graph.constrain(
                target_node,
                resume,
                gap_ms=timestamps.waits[wait_index][4],
                reason=f"narration wait for {wait.beat_id}/{wait.target}",
            )

    content_nodes: dict[str, str] = {}
    take_for_beat = {
        member.beat_id: (take, index)
        for take in plan.narration_takes
        for index, member in enumerate(take.members)
    }
    for index, beat in enumerate(plan.beats):
        content = f"beat:{beat.id}:content-end"
        graph.constrain(
            visual_end_nodes[beat.id],
            content,
            reason=f"visual content for beat {beat.id}",
        )
        take_member = take_for_beat.get(beat.id)
        if take_member is not None:
            take, member_index = take_member
            narration_end = (
                take_end_nodes[take.id]
                if member_index + 1 == len(take.members)
                else member_end_nodes[beat.id]
            )
            graph.constrain(
                narration_end,
                content,
                reason=f"narration content for beat {beat.id}",
            )
        content_nodes[beat.id] = content

        following_node = (
            beat_start_nodes[plan.beats[index + 1].id]
            if index + 1 < len(plan.beats)
            else "recording:end"
        )
        if index + 1 >= len(plan.beats) or (
            beat.id,
            plan.beats[index + 1].id,
        ) not in cross_boundaries:
            graph.constrain(
                content,
                following_node,
                gap_ms=beat.viewer_hold_ms,
                reason=f"viewer hold after beat {beat.id}",
            )

    solution = graph.solve()

    for previous_id, following_id in sorted(cross_boundaries):
        previous = beat_by_id[previous_id]
        required_end = solution.time(content_nodes[previous_id]) + previous.viewer_hold_ms
        boundary = solution.time(beat_start_nodes[following_id])
        if required_end > boundary:
            overflow_ms = required_end - boundary
            raise PresentationCompileError(
                "PRESENTATION_OVERFLOW",
                f"beat {previous_id!r} needs {overflow_ms}ms beyond its "
                "cross-beat narration boundary",
            )

    recording_end = solution.time("recording:end") if plan.beats else 0
    beat_timings: list[CompiledBeatTiming] = []
    for index, beat in enumerate(plan.beats):
        offset = solution.time(beat_start_nodes[beat.id])
        end = (
            solution.time(beat_start_nodes[plan.beats[index + 1].id])
            if index + 1 < len(plan.beats)
            else recording_end
        )
        if end < offset:
            raise PresentationCompileError(
                "PRESENTATION_CYCLE", f"beat {beat.id!r} has a negative duration"
            )
        beat_timings.append(
            CompiledBeatTiming(id=beat.id, offset_ms=offset, duration_ms=end - offset)
        )

    action_timings = tuple(
        CompiledActionTiming(
            beat_id=action.beat_id,
            action_id=action.action_id,
            presentation_start_ms=solution.time(action.start_node),
            presentation_end_ms=solution.time(action.end_node),
            local_start_ms=(
                solution.time(action.start_node)
                - solution.time(beat_start_nodes[action.beat_id])
            ),
            local_end_ms=(
                solution.time(action.end_node)
                - solution.time(beat_start_nodes[action.beat_id])
            ),
        )
        for action in scheduled_actions
    )
    audio_intervals = _resolve_audio_intervals(
        plan.narration_takes,
        sidecars,
        source_starts,
        take_point_nodes,
        wait_resume_nodes,
        solution,
    )
    return CompiledRecordingTiming(
        duration_ms=recording_end,
        beats=tuple(beat_timings),
        actions=action_timings,
        anchor_times_ms=MappingProxyType(
            {key: solution.time(node) for key, node in anchor_nodes.items()}
        ),
        audio_intervals=audio_intervals,
    )


def _validate_take_timestamps(
    take: NarrationTakePlan,
    raw: Mapping[str, Any] | None,
) -> _TakeTimestamps:
    if not isinstance(raw, Mapping):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"missing timestamp sidecar for narration take {take.id!r}"
        )
    if raw.get("version") != 1 or raw.get("take_id") != take.id:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"timestamp sidecar for take {take.id!r} has the wrong identity"
        )
    duration = _duration_value(
        raw.get("duration_ms"), field=f"duration for narration take {take.id!r}"
    )
    if duration == 0:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"narration take {take.id!r} has no audio duration"
        )

    raw_members = raw.get("members")
    if not isinstance(raw_members, list) or len(raw_members) != len(take.members):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"timestamp members do not match take {take.id!r}"
        )
    members: dict[str, tuple[int, int]] = {}
    previous_end = 0
    for planned, value in zip(take.members, raw_members, strict=True):
        if not isinstance(value, Mapping) or (
            value.get("beat_id"), value.get("text_start"), value.get("text_end")
        ) != (planned.beat_id, planned.text_start, planned.text_end):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"timestamp member order does not match take {take.id!r}"
            )
        start = _duration_value(
            value.get("source_start_ms"), field=f"member start for beat {planned.beat_id!r}"
        )
        end = _duration_value(
            value.get("source_end_ms"), field=f"member end for beat {planned.beat_id!r}"
        )
        if start < previous_end or end < start or end > duration:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"timestamp member range for beat {planned.beat_id!r} is invalid",
            )
        members[planned.beat_id] = (start, end)
        previous_end = end

    raw_anchors = raw.get("anchors")
    if not isinstance(raw_anchors, list) or len(raw_anchors) != len(take.anchors):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"timestamp anchors do not match take {take.id!r}"
        )
    anchors: dict[tuple[str, str], int] = {}
    for planned, value in zip(take.anchors, raw_anchors, strict=True):
        if not isinstance(value, Mapping) or (
            value.get("beat_id"), value.get("id"), value.get("text_offset")
        ) != (planned.beat_id, planned.id, planned.text_offset):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"timestamp anchor order does not match take {take.id!r}"
            )
        source_ms = _duration_value(
            value.get("source_ms"), field=f"timestamp for anchor {planned.id!r}"
        )
        if source_ms > duration:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"timestamp for anchor {planned.id!r} is outside its take"
            )
        anchors[(planned.beat_id, planned.id)] = source_ms

    raw_waits = raw.get("waits")
    if not isinstance(raw_waits, list) or len(raw_waits) != len(take.waits):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"timestamp waits do not match take {take.id!r}"
        )
    waits: list[tuple[str, str, int, int, int]] = []
    previous_wait_source = 0
    for planned, value in zip(take.waits, raw_waits, strict=True):
        if not isinstance(value, Mapping) or (
            value.get("beat_id"),
            value.get("target"),
            value.get("text_offset"),
            value.get("gap_ms"),
        ) != (
            planned.beat_id,
            planned.target,
            planned.text_offset,
            planned.gap_ms,
        ):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"timestamp wait order does not match take {take.id!r}"
            )
        source_ms = _duration_value(
            value.get("source_ms"), field=f"timestamp for wait {planned.target!r}"
        )
        if source_ms < previous_wait_source or source_ms > duration:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"timestamp for wait {planned.target!r} is outside its take"
            )
        waits.append(
            (
                planned.beat_id,
                planned.target,
                planned.text_offset,
                source_ms,
                planned.gap_ms,
            )
        )
        previous_wait_source = source_ms
    return _TakeTimestamps(
        duration_ms=duration,
        members=MappingProxyType(members),
        anchors=MappingProxyType(anchors),
        waits=tuple(waits),
    )


def _take_source_starts(
    takes: tuple[NarrationTakePlan, ...],
    sidecars: Mapping[str, _TakeTimestamps],
    supplied: Mapping[str, int] | None,
) -> Mapping[str, int]:
    result: dict[str, int] = {}
    cursor = 0
    for take in takes:
        if supplied is not None:
            if take.id not in supplied:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA", f"missing audio source start for take {take.id!r}"
                )
            source_start = _duration_value(
                supplied[take.id], field=f"audio source start for take {take.id!r}"
            )
            if source_start != cursor:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA", "narration take audio sources are not contiguous"
                )
        else:
            source_start = cursor
        result[take.id] = source_start
        cursor = source_start + sidecars[take.id].duration_ms
    return MappingProxyType(result)


def _add_take_audio_constraints(
    graph: ConstraintGraph,
    take: NarrationTakePlan,
    timestamps: _TakeTimestamps,
    *,
    beat_start_nodes: Mapping[str, str],
    take_point_nodes: dict[tuple[str, int], str],
    anchor_nodes: dict[tuple[str, str], str],
    wait_resume_nodes: dict[tuple[str, str, int], str],
    member_end_nodes: dict[str, str],
    take_end_nodes: dict[str, str],
    guided_beat_ids: frozenset[str],
) -> None:
    guided_member_boundaries: dict[str, int] = {}
    for previous, following in zip(take.members, take.members[1:]):
        if previous.beat_id not in guided_beat_ids:
            continue
        previous_end = timestamps.members[previous.beat_id][1]
        following_start = timestamps.members[following.beat_id][0]
        guided_member_boundaries[following.beat_id] = max(
            previous_end,
            following_start - GUIDED_AUDIO_LEAD_MS,
        )

    positions = {0, timestamps.duration_ms}
    positions.update(start for start, _ in timestamps.members.values())
    positions.update(end for _, end in timestamps.members.values())
    positions.update(timestamps.anchors.values())
    positions.update(wait[3] for wait in timestamps.waits)
    positions.update(guided_member_boundaries.values())
    for position in sorted(positions):
        node = (
            beat_start_nodes[take.members[0].beat_id]
            if position == 0
            else f"take:{take.id}:source:{position}"
        )
        graph.add_node(node)
        take_point_nodes[(take.id, position)] = node

    markers_by_source: dict[int, list[tuple[int, int, str, object]]] = {}
    for member_index, member in enumerate(take.members):
        start, end = timestamps.members[member.beat_id]
        markers_by_source.setdefault(end, []).append(
            (member.text_end, 0, "member_end", member.beat_id)
        )
        if member_index > 0:
            markers_by_source.setdefault(start, []).append(
                (member.text_start, 3, "member_start", member.beat_id)
            )
            boundary_source = guided_member_boundaries.get(member.beat_id)
            if boundary_source is not None:
                markers_by_source.setdefault(boundary_source, []).append(
                    (member.text_start, 3, "guided_boundary", member.beat_id)
                )
    for anchor in take.anchors:
        source_ms = timestamps.anchors[(anchor.beat_id, anchor.id)]
        markers_by_source.setdefault(source_ms, []).append(
            (anchor.text_offset, 1, "anchor", (anchor.beat_id, anchor.id))
        )
    for wait_index, wait in enumerate(timestamps.waits):
        markers_by_source.setdefault(wait[3], []).append(
            (wait[2], 2, "wait", (wait_index, wait))
        )
    for markers in markers_by_source.values():
        markers.sort(key=lambda marker: (marker[0], marker[1], repr(marker[3])))

    previous_source = 0
    cursor_node = take_point_nodes[(take.id, 0)]
    for source_ms in sorted(positions):
        point_node = take_point_nodes[(take.id, source_ms)]
        if source_ms != 0:
            graph.constrain(
                cursor_node,
                point_node,
                gap_ms=source_ms - previous_source,
                reason=f"continuous narration for take {take.id}",
            )
        cursor_node = point_node
        for _, _, kind, marker in markers_by_source.get(source_ms, []):
            if kind == "member_end":
                member_end_nodes[str(marker)] = cursor_node
            elif kind == "member_start":
                beat_id = str(marker)
                if beat_id in guided_member_boundaries:
                    continue
                boundary = beat_start_nodes[beat_id]
                graph.constrain(
                    cursor_node,
                    boundary,
                    reason=f"narration member boundary for beat {beat_id}",
                )
                cursor_node = boundary
            elif kind == "guided_boundary":
                beat_id = str(marker)
                boundary = beat_start_nodes[beat_id]
                graph.constrain(
                    cursor_node,
                    boundary,
                    reason=f"guided narration boundary for beat {beat_id}",
                )
                cursor_node = boundary
            elif kind == "anchor":
                anchor_nodes[marker] = cursor_node
            else:
                wait_index, wait = marker
                resume = f"take:{take.id}:wait:{wait_index}:resume"
                graph.constrain(
                    cursor_node,
                    resume,
                    reason=f"narration reaches wait {wait[1]}",
                )
                wait_resume_nodes[(take.id, wait[1], wait_index)] = resume
                cursor_node = resume
        previous_source = source_ms

    take_end_nodes[take.id] = cursor_node


def _add_beat_action_constraints(
    graph: ConstraintGraph,
    beat: BeatPlan,
    *,
    beat_start_node: str,
    anchor_nodes: Mapping[tuple[str, str], str],
    action_durations_ms: Mapping[tuple[str, str], int],
) -> tuple[_ScheduledAction, ...]:
    definitions: list[tuple[str, str | None, bool]] = []
    for action_index, action in enumerate(beat.actions):
        if isinstance(action, BrowserActionPlan):
            definitions.append((action.id, action.config.get("after"), True))
            continue
        if not isinstance(action, TerminalActionPlan):
            continue
        commands = action.config.get("commands")
        if commands:
            for command_index, command in enumerate(commands):
                action_id = terminal_action_id(action_index, command_index, command)
                after = command.get("after")
                if command_index == 0 and action.config.get("after") is not None:
                    after = action.config.get("after")
                definitions.append((action_id, after, command.get("id") is not None))
        else:
            definitions.append(
                (terminal_action_id(action_index, None), action.config.get("after"), False)
            )

    scheduled: list[_ScheduledAction] = []
    previous_end = beat_start_node
    for action_id, after, requires_duration in definitions:
        start_node = f"action:{beat.id}:{action_id}:start"
        end_node = f"action:{beat.id}:{action_id}:end"
        graph.constrain(
            previous_end,
            start_node,
            reason=f"source action order in beat {beat.id}",
        )
        if after is not None:
            anchor_id = after[1:-1]
            anchor_node = anchor_nodes.get((beat.id, anchor_id))
            if anchor_node is None:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA",
                    f"missing timestamp for anchor {after!r} in beat {beat.id!r}",
                )
            graph.constrain(
                anchor_node,
                start_node,
                reason=f"action {beat.id}/{action_id} follows anchor {anchor_id}",
            )
        duration_key = (beat.id, action_id)
        if requires_duration and duration_key not in action_durations_ms:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"missing presentation duration for action {beat.id!r}/{action_id!r}",
            )
        duration = _duration_value(
            action_durations_ms.get(duration_key, 0),
            field=f"duration for action {beat.id!r}/{action_id!r}",
        )
        graph.constrain(
            start_node,
            end_node,
            gap_ms=duration,
            reason=f"duration of action {beat.id}/{action_id}",
        )
        scheduled.append(
            _ScheduledAction(
                beat_id=beat.id,
                action_id=action_id,
                start_node=start_node,
                end_node=end_node,
            )
        )
        previous_end = end_node
    return tuple(scheduled)


def _resolve_audio_intervals(
    takes: tuple[NarrationTakePlan, ...],
    sidecars: Mapping[str, _TakeTimestamps],
    source_starts: Mapping[str, int],
    take_point_nodes: Mapping[tuple[str, int], str],
    wait_resume_nodes: Mapping[tuple[str, str, int], str],
    solution: ConstraintSolution,
) -> tuple[PresentationAudioIntervalV1, ...]:
    intervals: list[PresentationAudioIntervalV1] = []
    for take in takes:
        timestamps = sidecars[take.id]
        take_source_start = source_starts[take.id]
        source_cursor = 0
        presentation_start = solution.time(take_point_nodes[(take.id, 0)])
        for wait_index, wait in enumerate(timestamps.waits):
            wait_source = wait[3]
            if wait_source > source_cursor:
                presentation_end = solution.time(
                    take_point_nodes[(take.id, wait_source)]
                )
                intervals.append(
                    PresentationAudioIntervalV1(
                        presentation_start_ms=presentation_start,
                        presentation_end_ms=presentation_end,
                        source_start_ms=take_source_start + source_cursor,
                        source_end_ms=take_source_start + wait_source,
                    )
                )
            source_cursor = wait_source
            presentation_start = solution.time(
                wait_resume_nodes[(take.id, wait[1], wait_index)]
            )
        if source_cursor < timestamps.duration_ms:
            intervals.append(
                PresentationAudioIntervalV1(
                    presentation_start_ms=presentation_start,
                    presentation_end_ms=solution.time(
                        take_point_nodes[(take.id, timestamps.duration_ms)]
                    ),
                    source_start_ms=take_source_start + source_cursor,
                    source_end_ms=take_source_start + timestamps.duration_ms,
                )
            )
    return tuple(intervals)


def _duration_value(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"{field} must be a numeric millisecond value"
        )
    try:
        return milliseconds_half_up(value)
    except (TypeError, ValueError) as exc:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"{field} must be finite and non-negative"
        ) from exc


FINGERPRINT_VERSION = 1
CAPTURE_FINGERPRINT_POLICY = "capture-v1"
PRESENTATION_FINGERPRINT_POLICY = "presentation-v1"
GUIDED_AUDIO_LEAD_MS = 350


@dataclass(frozen=True)
class ArtifactFingerprints:
    capture_fingerprint: str
    presentation_fingerprint: str

    def payload(self) -> dict[str, object]:
        return {
            "version": FINGERPRINT_VERSION,
            "capture_fingerprint": self.capture_fingerprint,
            "presentation_fingerprint": self.presentation_fingerprint,
        }


class ArtifactFreshness(str, Enum):
    fresh = "fresh"
    recompile = "recompile"
    recapture = "recapture"


def compile_artifact_fingerprints(
    plan: RecordingPlan,
    *,
    capture_environment: Mapping[str, Any],
    source_dependencies: Mapping[str, str],
    capture_policy_versions: Mapping[str, str],
    visual_asset_hashes: Iterable[str] = (),
    narration_take_hashes: Mapping[str, str] | None = None,
    timestamp_hashes: Mapping[str, str] | None = None,
    presentation_policy_versions: Mapping[str, str] | None = None,
    auth_state_sha256: str | None = None,
) -> ArtifactFingerprints:
    """Hash recapture and recompile inputs separately and deterministically."""

    dependency_hashes = _hash_mapping(
        source_dependencies, field="source dependency hashes"
    )
    capture_policies = _string_mapping(
        capture_policy_versions, field="capture policy versions"
    )
    auth_hash = (
        None
        if auth_state_sha256 is None
        else _sha256_value(auth_state_sha256, field="auth state hash")
    )
    capture_input = {
        "policy": CAPTURE_FINGERPRINT_POLICY,
        "plan": _capture_plan_value(plan),
        "environment": _canonical_value(capture_environment),
        "dependencies": dependency_hashes,
        "policies": capture_policies,
        "auth_state_sha256": auth_hash,
    }
    capture_fingerprint = _canonical_sha256(capture_input)

    assets = sorted(
        _sha256_value(value, field="visual asset hash")
        for value in visual_asset_hashes
    )
    narration_hashes = _hash_mapping(
        {} if narration_take_hashes is None else narration_take_hashes,
        field="narration take hashes",
    )
    timestamps = _hash_mapping(
        {} if timestamp_hashes is None else timestamp_hashes,
        field="timestamp hashes",
    )
    presentation_policies = _string_mapping(
        {} if presentation_policy_versions is None else presentation_policy_versions,
        field="presentation policy versions",
    )
    presentation_input = {
        "policy": PRESENTATION_FINGERPRINT_POLICY,
        "capture_fingerprint": capture_fingerprint,
        "plan": _presentation_plan_value(plan),
        "visual_asset_hashes": assets,
        "narration_take_hashes": narration_hashes,
        "timestamp_hashes": timestamps,
        "policies": presentation_policies,
    }
    return ArtifactFingerprints(
        capture_fingerprint=capture_fingerprint,
        presentation_fingerprint=_canonical_sha256(presentation_input),
    )


def artifact_freshness(
    stored: Mapping[str, Any] | None,
    current: ArtifactFingerprints,
    *,
    capture_artifacts_exist: bool,
    presentation_artifacts_exist: bool,
) -> ArtifactFreshness:
    """Return the least expensive safe repair for stale generated artifacts."""

    if not capture_artifacts_exist or stored is None:
        return ArtifactFreshness.recapture
    if stored.get("version") != FINGERPRINT_VERSION:
        return ArtifactFreshness.recapture
    if stored.get("capture_fingerprint") != current.capture_fingerprint:
        return ArtifactFreshness.recapture
    if not presentation_artifacts_exist:
        return ArtifactFreshness.recompile
    if stored.get("presentation_fingerprint") != current.presentation_fingerprint:
        return ArtifactFreshness.recompile
    return ArtifactFreshness.fresh


def _capture_plan_value(plan: RecordingPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "browser": _canonical_value(plan.browser),
        "setup": _canonical_value(plan.setup),
        "beats": [
            {
                "id": beat.id,
                "medium": beat.medium.value,
                "actions": [
                    _capture_action_value(action) for action in beat.actions
                ],
                "checks": _canonical_value(beat.checks),
            }
            for beat in plan.beats
        ],
        "cleanup": _canonical_value(plan.cleanup),
    }


def _capture_action_value(action: TerminalActionPlan | BrowserActionPlan) -> Any:
    value = _canonical_value(action)
    if not isinstance(value, dict):
        return value
    if isinstance(action, BrowserActionPlan):
        config = dict(value["config"])
        for field in (
            "after",
            "hold_before_ms",
            "hold_after_ms",
            "transition",
            "display_url_after",
        ):
            config.pop(field, None)
        open_page = config.get("open_page")
        if isinstance(open_page, dict):
            open_page = dict(open_page)
            open_page.pop("display_url", None)
            config["open_page"] = open_page
        value["config"] = config
        return value
    config = dict(value["config"])
    _strip_terminal_presentation_fields(config)
    commands = config.get("commands")
    if isinstance(commands, list):
        stripped_commands = []
        for command in commands:
            command = dict(command)
            _strip_terminal_presentation_fields(command)
            stripped_commands.append(command)
        config["commands"] = stripped_commands
    value["config"] = config
    return value


def _strip_terminal_presentation_fields(value: dict[str, Any]) -> None:
    value.pop("after", None)


def _presentation_plan_value(plan: RecordingPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "title": plan.title,
        "presentation": _canonical_value(plan.presentation),
        "beats": [
            {
                "id": beat.id,
                "medium": beat.medium.value,
                "heading": beat.heading,
                "narration_text": beat.narration_text,
                "narration_take": beat.explicit_narration_take,
                "viewer_hold_ms": beat.viewer_hold_ms,
                "guide": _canonical_value(beat.guide),
                "anchors": _canonical_value(beat.anchors),
                "waits": _canonical_value(beat.waits),
                "actions": _canonical_value(beat.actions),
            }
            for beat in plan.beats
        ],
        "narration_takes": _canonical_value(plan.narration_takes),
    }


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _canonical_value(value),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported fingerprint value {type(value).__name__}")


def _hash_mapping(value: Mapping[str, str], *, field: str) -> dict[str, str]:
    return {
        key: _sha256_value(item, field=f"{field} entry {key!r}")
        for key, item in _string_mapping(value, field=field).items()
    }


def _string_mapping(value: Mapping[str, str], *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    result: dict[str, str] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ValueError(f"{field} must contain non-empty string keys and values")
        result[key] = item
    return result


def _sha256_value(value: str, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return value.lower()


@dataclass(frozen=True)
class TerminalBeatMaterialization:
    path: Path
    duration_ms: int
    captured_duration_ms: int
    sha256: str
    bytes: int


@dataclass(frozen=True)
class TerminalTextHighlightEvent:
    id: str
    text: str
    occurrence: int
    start_ms: int
    end_ms: int


def materialize_terminal_beat(
    source: Path,
    destination: Path,
    *,
    duration_ms: int,
    captured_action_intervals_ms: Mapping[str, tuple[int, int]] | None = None,
    action_starts_ms: Mapping[str, int] | None = None,
    text_highlights: tuple[TerminalTextHighlightEvent, ...] = (),
) -> TerminalBeatMaterialization:
    """Relocate a beat-local cast and extend its final hold to solved duration."""

    solved_duration = milliseconds_half_up(duration_ms)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
    except (OSError, UnicodeDecodeError, IndexError, json.JSONDecodeError) as exc:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "terminal beat cast is invalid"
        ) from exc
    if not isinstance(header, dict) or header.get("version") not in {2, 3}:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "terminal beat cast must use version 2 or 3"
        )
    version = int(header["version"])
    # Asciinema records the private control-script command and wall-clock
    # timestamp in its physical capture header.  Neither is presentation data,
    # and the command contains the private run directory, so publish only the
    # stable terminal geometry and optional authored title.
    public_header = {
        key: header[key]
        for key in ("version", "width", "height", "term", "title")
        if key in header
    }
    events: list[list[object]] = []
    event_absolute_ms: list[int] = []
    captured_seconds = 0.0
    previous_absolute = 0.0
    for index, line in enumerate(lines[1:], 2):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"terminal cast event {index} is invalid"
            ) from exc
        if (
            not isinstance(event, list)
            or len(event) < 3
            or isinstance(event[0], bool)
            or not isinstance(event[0], (int, float))
            or event[0] < 0
        ):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"terminal cast event {index} is invalid"
            )
        event_time = float(event[0])
        if not math.isfinite(event_time):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"terminal cast event {index} is invalid"
            )
        if version == 3:
            captured_seconds += event_time
        else:
            if event_time < previous_absolute:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA", "terminal cast events are unordered"
                )
            previous_absolute = event_time
            captured_seconds = event_time
        events.append(event)
        event_absolute_ms.append(
            milliseconds_half_up(Decimal(str(captured_seconds)) * 1000)
        )

    captured_ms = milliseconds_half_up(Decimal(str(captured_seconds)) * 1000)
    materialized_ms = captured_ms
    if captured_action_intervals_ms is not None or action_starts_ms is not None:
        if captured_action_intervals_ms is None or action_starts_ms is None:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                "terminal action intervals and solved starts must be supplied together",
            )
        events, materialized_ms = _relocate_terminal_events(
            events,
            event_absolute_ms,
            version=version,
            captured_action_intervals_ms=captured_action_intervals_ms,
            action_starts_ms=action_starts_ms,
        )
    if materialized_ms > solved_duration:
        raise PresentationCompileError(
            "PRESENTATION_OVERFLOW",
            f"terminal media is {materialized_ms}ms but its beat is {solved_duration}ms",
        )
    if materialized_ms < solved_duration:
        if version == 3:
            hold_time = (solved_duration - materialized_ms) / 1000
        else:
            hold_time = solved_duration / 1000
        events.append([hold_time, "o", ""])
    events = _insert_terminal_text_highlights(
        events,
        version=version,
        duration_ms=solved_duration,
        highlights=text_highlights,
    )

    payload = "\n".join(
        [
            json.dumps(public_header, separators=(",", ":")),
            *(json.dumps(event, separators=(",", ":")) for event in events),
        ]
    ) + "\n"
    content = payload.encode("utf-8")
    if destination.is_symlink():
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "terminal beat destination is unsafe"
        )
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    destination.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return TerminalBeatMaterialization(
        path=destination,
        duration_ms=solved_duration,
        captured_duration_ms=captured_ms,
        sha256=digest,
        bytes=len(content),
    )


def _insert_terminal_text_highlights(
    events: list[list[object]],
    *,
    version: int,
    duration_ms: int,
    highlights: tuple[TerminalTextHighlightEvent, ...],
) -> list[list[object]]:
    if not highlights:
        return events

    timeline: list[tuple[int, int, int, list[object]]] = []
    absolute_seconds = Decimal(0)
    for sequence, event in enumerate(events):
        event_seconds = Decimal(str(event[0]))
        absolute_seconds = (
            absolute_seconds + event_seconds if version == 3 else event_seconds
        )
        absolute_ms = milliseconds_half_up(absolute_seconds * 1000)
        timeline.append((absolute_ms, 0, sequence, [*event[1:]]))

    seen_ids: set[str] = set()
    marker_sequence = len(events)
    for highlight in highlights:
        if (
            not highlight.id
            or highlight.id in seen_ids
            or not highlight.text
            or isinstance(highlight.occurrence, bool)
            or not isinstance(highlight.occurrence, int)
            or highlight.occurrence <= 0
            or isinstance(highlight.start_ms, bool)
            or not isinstance(highlight.start_ms, int)
            or isinstance(highlight.end_ms, bool)
            or not isinstance(highlight.end_ms, int)
            or not 0 <= highlight.start_ms < highlight.end_ms <= duration_ms
        ):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", "terminal text highlight is invalid"
            )
        seen_ids.add(highlight.id)
        start_payload = {
            "active": True,
            "id": highlight.id,
            "occurrence": highlight.occurrence,
            "text": highlight.text,
        }
        end_payload = {"active": False, "id": highlight.id}
        prefix = "omegaflow:highlight:"
        timeline.append(
            (
                highlight.start_ms,
                2,
                marker_sequence,
                [
                    "m",
                    prefix
                    + json.dumps(
                        start_payload, separators=(",", ":"), sort_keys=True
                    ),
                ],
            )
        )
        marker_sequence += 1
        timeline.append(
            (
                highlight.end_ms,
                1,
                marker_sequence,
                [
                    "m",
                    prefix
                    + json.dumps(end_payload, separators=(",", ":"), sort_keys=True),
                ],
            )
        )
        marker_sequence += 1

    timeline.sort(key=lambda item: item[:3])
    result: list[list[object]] = []
    previous_ms = 0
    for event_ms, _priority, _sequence, payload in timeline:
        timestamp_ms = event_ms - previous_ms if version == 3 else event_ms
        result.append([timestamp_ms / 1000, *payload])
        previous_ms = event_ms
    return result


def _relocate_terminal_events(
    events: list[list[object]],
    event_absolute_ms: list[int],
    *,
    version: int,
    captured_action_intervals_ms: Mapping[str, tuple[int, int]],
    action_starts_ms: Mapping[str, int],
) -> tuple[list[list[object]], int]:
    if set(captured_action_intervals_ms) != set(action_starts_ms):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "terminal action timing identities do not match"
        )
    intervals: list[tuple[str, int, int, int]] = []
    previous_end = 0
    for action_id, interval in captured_action_intervals_ms.items():
        if (
            not isinstance(interval, tuple)
            or len(interval) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in interval
            )
        ):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", "terminal action interval is invalid"
            )
        start, end = interval
        target = action_starts_ms[action_id]
        if (
            start < previous_end
            or end < start
            or isinstance(target, bool)
            or not isinstance(target, int)
            or target < 0
        ):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", "terminal action timing is invalid"
            )
        intervals.append((action_id, start, end, target))
        previous_end = end

    relocated_absolute: list[int] = []
    previous = 0
    for source_ms in event_absolute_ms:
        target_ms = source_ms
        for _action_id, start, end, target in intervals:
            if start <= source_ms <= end:
                target_ms = target + source_ms - start
                break
        target_ms = max(previous, target_ms)
        relocated_absolute.append(target_ms)
        previous = target_ms

    relocated: list[list[object]] = []
    previous = 0
    for event, target_ms in zip(events, relocated_absolute, strict=True):
        timestamp_ms = target_ms - previous if version == 3 else target_ms
        relocated.append([timestamp_ms / 1000, *event[1:]])
        previous = target_ms
    return relocated, (relocated_absolute[-1] if relocated_absolute else 0)


POINTER_MIN_DURATION_MS = 260
POINTER_MAX_DURATION_MS = 1000
CLICK_DURATION_MS = 120
FOCUS_DURATION_MS = 100
STATE_FADE_DURATION_MS = 180
KEY_DURATION_MS = 300
SCROLL_MIN_DURATION_MS = 280
SCROLL_MAX_DURATION_MS = 900


@dataclass(frozen=True)
class CompiledAssetSource:
    id: str
    path: Path
    sha256: str
    media_type: str
    bytes: int
    width: int
    height: int
    duration_ms: int | None = None


@dataclass(frozen=True)
class CompiledBrowserBeat:
    payload: Mapping[str, Any]
    assets: Mapping[str, CompiledAssetSource]
    action_starts_ms: Mapping[str, int]
    action_completions_ms: Mapping[str, int]


@dataclass(frozen=True)
class BrowserCaptureLog:
    profile: Mapping[str, Any]
    initial_state: Mapping[str, Any]
    actions_by_beat: Mapping[str, tuple[Mapping[str, Any], ...]]
    clip_assets: Mapping[tuple[str, str], Mapping[str, Any]]

    @property
    def viewport(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "width": self.profile["viewport_width"],
                "height": self.profile["viewport_height"],
                "device_scale_factor": self.profile["device_scale_factor"],
            }
        )


def load_browser_capture_log(path: Path) -> BrowserCaptureLog:
    """Load only a complete private capture log into compiler inputs."""

    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser capture log is invalid"
        ) from exc
    if not records or not all(isinstance(record, dict) for record in records):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser capture log is empty or malformed"
        )
    if records[0].get("type") != "run_start" or records[-1].get("type") != "run_end":
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser capture log is incomplete"
        )
    if records[-1].get("status") != "completed":
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser capture log did not complete successfully"
        )
    allowed_types = {
        "run_start",
        "run_end",
        "beat_start",
        "beat_end",
        "action",
        "check",
        "warning",
        "diagnostic",
    }
    for index, record in enumerate(records, 1):
        if record.get("capture_version") != 1 or record.get("seq") != index:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", "browser capture log sequence is invalid"
            )
        if record.get("type") not in allowed_types:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"browser capture log record type {record.get('type')!r} is unsupported",
            )
    start = records[0]
    profile = _mapping_value(start.get("profile"), field="browser capture profile")
    initial_state = _mapping_value(
        start.get("initial_state"), field="browser initial state"
    )
    actions: dict[str, list[Mapping[str, Any]]] = {}
    clips: dict[tuple[str, str], Mapping[str, Any]] = {}
    seen_actions: set[str] = set()
    for record in records[1:-1]:
        if record["type"] == "action":
            beat_id = _required_string(record.get("beat_id"), field="browser beat id")
            action_id = _required_string(
                record.get("action_id"), field="browser action id"
            )
            if action_id in seen_actions:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA",
                    f"duplicate browser action record {action_id!r}",
                )
            seen_actions.add(action_id)
            actions.setdefault(beat_id, []).append(MappingProxyType(dict(record)))
        elif record["type"] == "diagnostic" and record.get("kind") == "dynamic_fragment":
            beat_id = _required_string(record.get("beat_id"), field="browser beat id")
            action_id = _required_string(
                record.get("action_id"), field="browser action id"
            )
            key = (beat_id, action_id)
            if key in clips:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA",
                    f"duplicate browser clip record for action {action_id!r}",
                )
            clips[key] = MappingProxyType(dict(record))
    return BrowserCaptureLog(
        profile=MappingProxyType(profile),
        initial_state=MappingProxyType(initial_state),
        actions_by_beat=MappingProxyType(
            {beat_id: tuple(values) for beat_id, values in actions.items()}
        ),
        clip_assets=MappingProxyType(clips),
    )


def pointer_motion(
    recording_id: str,
    beat_id: str,
    action_id: str,
    start: Mapping[str, Any],
    end: Mapping[str, Any],
) -> tuple[int, dict[str, float]]:
    """Return pointer-v1 duration and deterministic cubic control points."""

    start_x, start_y = _point_values(start, field="pointer start")
    end_x, end_y = _point_values(end, field="pointer end")
    dx = end_x - start_x
    dy = end_y - start_y
    distance = math.hypot(dx, dy)
    duration = min(
        POINTER_MAX_DURATION_MS,
        max(POINTER_MIN_DURATION_MS, milliseconds_half_up(260 + distance * 0.75)),
    )
    if distance == 0:
        return duration, {
            "x1": start_x,
            "y1": start_y,
            "x2": end_x,
            "y2": end_y,
        }
    seed = hashlib.sha256(
        f"{recording_id}\0{beat_id}\0{action_id}".encode("utf-8")
    ).digest()
    direction = 1 if seed[0] & 1 else -1
    offset = min(110.0, max(12.0, distance * 0.12)) * direction
    perpendicular_x = -dy / distance * offset
    perpendicular_y = dx / distance * offset
    first_fraction = 0.25 + seed[1] / 2550
    second_fraction = 0.65 + seed[2] / 2550
    second_offset_scale = 0.55 + seed[3] / 850
    return duration, {
        "x1": start_x + dx * first_fraction + perpendicular_x,
        "y1": start_y + dy * first_fraction + perpendicular_y,
        "x2": start_x + dx * second_fraction + perpendicular_x * second_offset_scale,
        "y2": start_y + dy * second_fraction + perpendicular_y * second_offset_scale,
    }


def natural_text_duration_ms(text: str) -> int:
    """Return deterministic natural-v1 pacing for one safe public string."""

    if not isinstance(text, str):
        raise TypeError("text pacing input must be a string")
    if not text:
        return 0
    total = 0.0
    for character in text:
        if character in ".!?":
            total += 145
        elif character in ",;:":
            total += 105
        elif character.isspace():
            total += 80
        else:
            total += 52
    acceleration = max(0.58, 1.0 - max(0, len(text) - 18) * 0.012)
    return milliseconds_half_up(total * acceleration)


def compile_browser_beat(
    recording_id: str,
    beat: BeatPlan,
    *,
    action_captures: Iterable[Mapping[str, Any]],
    viewport: Mapping[str, Any],
    initial_state: Mapping[str, Any],
    clip_assets: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
    action_starts_ms: Mapping[str, int] | None = None,
    duration_ms: int | None = None,
    initial_pointer: Mapping[str, Any] | None = None,
    initial_display_url: str | None = None,
    default_transition: str = "cut",
) -> CompiledBrowserBeat:
    """Compile private browser action facts into a publish-safe beat payload."""

    actions = tuple(
        action for action in beat.actions if isinstance(action, BrowserActionPlan)
    )
    captures = tuple(action_captures)
    if len(actions) != len(captures):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser action capture count does not match its plan"
        )
    capture_by_id: dict[str, Mapping[str, Any]] = {}
    for action, capture in zip(actions, captures, strict=True):
        if capture.get("action_id") != action.id or capture.get("kind") != action.kind:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"browser capture does not match action {action.id!r}",
            )
        capture_by_id[action.id] = capture

    width = _positive_integer(viewport.get("width"), field="browser viewport width")
    height = _positive_integer(viewport.get("height"), field="browser viewport height")
    scale = _positive_number(
        viewport.get("device_scale_factor", 1), field="browser device scale factor"
    )
    pointer = (
        {"x": width / 2, "y": height / 2, "visible": True}
        if initial_pointer is None
        else {
            "x": _number(initial_pointer.get("x"), field="initial pointer x"),
            "y": _number(initial_pointer.get("y"), field="initial pointer y"),
            "visible": bool(initial_pointer.get("visible", True)),
        }
    )
    if beat.browser_pointer_visible is not None:
        pointer["visible"] = beat.browser_pointer_visible
    initial_pointer_state = dict(pointer)
    assets: dict[str, CompiledAssetSource] = {}
    initial_asset = _register_state_asset(initial_state, assets)
    events: list[dict[str, Any]] = []
    starts: dict[str, int] = {}
    completions: dict[str, int] = {}
    cursor = 0
    clip_assets = {} if clip_assets is None else clip_assets
    for action in actions:
        capture = capture_by_id[action.id]
        config = _thaw_mapping(action.config)
        payload = _mapping_value(config.get(action.kind), field=f"action {action.id}")
        if action_starts_ms is None:
            start_ms = cursor
        else:
            if action.id not in action_starts_ms:
                raise PresentationCompileError(
                    "PRESENTATION_SCHEMA", f"missing start time for action {action.id!r}"
                )
            start_ms = milliseconds_half_up(action_starts_ms[action.id])
            if start_ms < cursor:
                raise PresentationCompileError(
                    "PRESENTATION_CYCLE",
                    f"action {action.id!r} starts before its predecessor completes",
                )
        starts[action.id] = start_ms
        visual_start_ms = start_ms
        hold_before_ms = config.get("hold_before_ms")
        if hold_before_ms is not None:
            visual_start_ms += milliseconds_half_up(hold_before_ms)
        action_events, end_ms, pointer = _compile_browser_action(
            recording_id,
            beat.id,
            action,
            config,
            payload,
            capture,
            start_ms=visual_start_ms,
            pointer=pointer,
            assets=assets,
            clip_asset=clip_assets.get((beat.id, action.id)),
            default_transition=default_transition,
        )
        events.extend(action_events)
        hold_after_ms = config.get("hold_after_ms")
        if hold_after_ms is not None:
            end_ms += milliseconds_half_up(hold_after_ms)
        events.append(
            {
                "kind": "complete",
                "action_id": action.id,
                "at_ms": end_ms,
                "end_ms": end_ms,
            }
        )
        display_url = config.get("display_url_after")
        if action.kind == "open_page" and payload.get("display_url") is not None:
            display_url = payload["display_url"]
            if display_url == "$handoff":
                completion = capture.get("completion")
                display_url = (
                    completion.get("url")
                    if isinstance(completion, Mapping)
                    else None
                )
                if not isinstance(display_url, str) or not display_url:
                    raise PresentationCompileError(
                        "PRESENTATION_SCHEMA",
                        f"open_page action {action.id!r} has no captured handoff URL",
                    )
        if display_url is not None:
            events.append(
                {
                    "kind": "display_url",
                    "action_id": action.id,
                    "at_ms": end_ms,
                    "end_ms": end_ms,
                    "value": display_url,
                }
            )
        completions[action.id] = end_ms
        cursor = end_ms

    resolved_duration = cursor if duration_ms is None else milliseconds_half_up(duration_ms)
    if resolved_duration < cursor:
        raise PresentationCompileError(
            "PRESENTATION_OVERFLOW",
            f"browser visuals end at {cursor}ms but beat {beat.id!r} ends at {resolved_duration}ms",
        )
    payload_mapping = {
        "payload_version": 1,
        "beat_id": beat.id,
        "duration_ms": resolved_duration,
        "viewport": {
            "width": width,
            "height": height,
            "device_scale_factor": scale,
        },
        "initial_state": initial_asset,
        "initial_pointer": initial_pointer_state,
        "initial_display_url": initial_display_url,
        "animation_policies": {"pointer": "pointer-v1", "typing": "natural-v1"},
        "events": events,
    }
    serialized = serialize_browser_payload(
        validate_browser_payload(payload_mapping),
        action_ids=[action.id for action in actions],
    )
    return CompiledBrowserBeat(
        payload=MappingProxyType(serialized),
        assets=MappingProxyType(assets),
        action_starts_ms=MappingProxyType(starts),
        action_completions_ms=MappingProxyType(completions),
    )


def _compile_browser_action(
    recording_id: str,
    beat_id: str,
    action: BrowserActionPlan,
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    capture: Mapping[str, Any],
    *,
    start_ms: int,
    pointer: Mapping[str, Any],
    assets: dict[str, CompiledAssetSource],
    clip_asset: Mapping[str, Any] | None,
    default_transition: str,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = start_ms
    resolved_pointer = dict(pointer)
    target = capture.get("target")
    target = target if isinstance(target, Mapping) else None
    if action.kind == "set_pointer":
        visible = payload.get("visible")
        if not isinstance(visible, bool):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"set_pointer action {action.id!r} visibility is invalid",
            )
        events.append(
            {
                "kind": "pointer_visibility",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor,
                "visible": visible,
            }
        )
        resolved_pointer["visible"] = visible
        return events, cursor, resolved_pointer
    if action.kind in {"click", "move_pointer"}:
        point = _target_point(target, action.id)
        move_duration, curve = pointer_motion(
            recording_id, beat_id, action.id, resolved_pointer, point
        )
        events.append(
            {
                "kind": "pointer_move",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor + move_duration,
                "start": {
                    "x": float(resolved_pointer["x"]),
                    "y": float(resolved_pointer["y"]),
                },
                "end": point,
                "curve": curve,
            }
        )
        cursor += move_duration
        if action.kind == "click":
            events.append(
                {
                    "kind": "click",
                    "action_id": action.id,
                    "at_ms": cursor,
                    "end_ms": cursor + CLICK_DURATION_MS,
                    "point": point,
                    "button": payload.get("button", "left"),
                }
            )
            cursor += CLICK_DURATION_MS
        resolved_pointer.update(point)
    elif action.kind in {"fill", "type_keys"}:
        bounds = _target_bounds(target, action.id)
        events.append(
            {
                "kind": "focus",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor + FOCUS_DURATION_MS,
                "target": bounds,
            }
        )
        cursor += FOCUS_DURATION_MS
        overlay = target.get("text_overlay") if target is not None else None
        if isinstance(overlay, Mapping) and overlay.get("eligible") is True:
            presentation = _safe_input_presentation(capture)
            if presentation is not None:
                mode, initial, final = presentation
                text_duration = natural_text_duration_ms(final)
                events.append(
                    {
                        "kind": "text",
                        "action_id": action.id,
                        "at_ms": cursor,
                        "end_ms": cursor + text_duration,
                        "target": bounds,
                        "initial": initial,
                        "final": final,
                        "mode": mode,
                        "style": _mapping_value(
                            overlay.get("style"), field="browser text style"
                        ),
                    }
                )
                cursor += text_duration
    elif action.kind == "press":
        if target is not None:
            bounds = _target_bounds(target, action.id)
            events.append(
                {
                    "kind": "focus",
                    "action_id": action.id,
                    "at_ms": cursor,
                    "end_ms": cursor + FOCUS_DURATION_MS,
                    "target": bounds,
                }
            )
            cursor += FOCUS_DURATION_MS
        key = payload.get("key")
        if not isinstance(key, str) or not key:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"press action {action.id!r} has no key"
            )
        events.append(
            {
                "kind": "key",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor + KEY_DURATION_MS,
                "key": key,
                "label": key.replace("+", " + "),
            }
        )
        cursor += KEY_DURATION_MS

    visual = _mapping_value(capture.get("visual"), field="browser visual")
    visual_kind = visual.get("kind")
    synthesized_scroll = False
    if action.kind == "scroll" and visual_kind == "state" and target is not None:
        scroll = target.get("scroll")
        if isinstance(scroll, Mapping) and scroll.get("eligible") is True:
            before = _mapping_value(
                capture.get("before_state"), field="browser scroll before state"
            )
            after = _mapping_value(visual.get("state"), field="browser scroll state")
            start_asset = _register_state_asset(before, assets)
            end_asset = _register_state_asset(after, assets)
            start_offset = _mapping_value(scroll.get("start"), field="scroll start")
            end_offset = _mapping_value(scroll.get("end"), field="scroll end")
            distance = math.hypot(
                _number(end_offset.get("x"), field="scroll end x")
                - _number(start_offset.get("x"), field="scroll start x"),
                _number(end_offset.get("y"), field="scroll end y")
                - _number(start_offset.get("y"), field="scroll start y"),
            )
            scroll_duration = min(
                SCROLL_MAX_DURATION_MS,
                max(SCROLL_MIN_DURATION_MS, milliseconds_half_up(260 + distance)),
            )
            events.append(
                {
                    "kind": "scroll",
                    "action_id": action.id,
                    "at_ms": cursor,
                    "end_ms": cursor + scroll_duration,
                    "container": _target_bounds(target, action.id),
                    "start": {
                        "x": _number(start_offset.get("x"), field="scroll start x"),
                        "y": _number(start_offset.get("y"), field="scroll start y"),
                    },
                    "end": {
                        "x": _number(end_offset.get("x"), field="scroll end x"),
                        "y": _number(end_offset.get("y"), field="scroll end y"),
                    },
                    "start_asset": start_asset,
                    "end_asset": end_asset,
                }
            )
            cursor += scroll_duration
            synthesized_scroll = True

    if visual_kind == "state" and not synthesized_scroll:
        state = _mapping_value(visual.get("state"), field="browser visual state")
        asset = _register_state_asset(state, assets)
        transition = config.get("transition") or default_transition
        if transition == "captured":
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"action {action.id!r} requested captured motion but has a static state",
            )
        if transition not in {"cut", "fade"}:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", f"action {action.id!r} transition is invalid"
            )
        state_duration = STATE_FADE_DURATION_MS if transition == "fade" else 0
        events.append(
            {
                "kind": "state",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor + state_duration,
                "asset": asset,
                "transition": transition,
            }
        )
        cursor += state_duration
    elif visual_kind == "clip":
        if clip_asset is None:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA",
                f"captured clip for action {action.id!r} is unavailable",
            )
        asset_id, clip_duration = _register_clip_asset(clip_asset, assets)
        events.append(
            {
                "kind": "clip",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor + clip_duration,
                "asset": asset_id,
                "trim_start_ms": 0,
                "trim_end_ms": clip_duration,
            }
        )
        cursor += clip_duration
        end_state = _mapping_value(
            visual.get("end_state"), field=f"browser clip {action.id} end state"
        )
        end_asset = _register_state_asset(end_state, assets)
        events.append(
            {
                "kind": "state",
                "action_id": action.id,
                "at_ms": cursor,
                "end_ms": cursor,
                "asset": end_asset,
                "transition": "cut",
            }
        )
    elif visual_kind == "deferred":
        _required_string(
            visual.get("owner_action_id"),
            field=f"deferred browser visual owner for {action.id}",
        )
    elif visual_kind != "state":
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA",
            f"action {action.id!r} has unsupported visual kind {visual_kind!r}",
        )
    return events, cursor, resolved_pointer


def _safe_input_presentation(
    capture: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    completion = _mapping_value(capture.get("completion"), field="input completion")
    value = _mapping_value(completion.get("input"), field="input presentation")
    kind = value.get("kind")
    if kind == "text":
        text = value.get("text")
        if not isinstance(text, str):
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", "literal input presentation is invalid"
            )
        return "literal", "", text
    if kind != "secret":
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "input presentation kind is invalid"
        )
    presentation = value.get("presentation", "masked")
    if presentation == "omitted":
        return None
    if presentation == "masked":
        return "masked", "", "••••••••"
    if presentation == "placeholder":
        placeholder = value.get("placeholder")
        if not isinstance(placeholder, str) or not placeholder:
            raise PresentationCompileError(
                "PRESENTATION_SCHEMA", "secret placeholder is invalid"
            )
        return "placeholder", "", placeholder
    raise PresentationCompileError(
        "PRESENTATION_SCHEMA", "secret presentation is invalid"
    )


def _register_state_asset(
    value: Mapping[str, Any], assets: dict[str, CompiledAssetSource]
) -> str:
    digest = value.get("sha256")
    if not _is_sha256(digest):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser state hash is invalid"
        )
    asset_id = f"state-{digest}"
    asset = CompiledAssetSource(
        id=asset_id,
        path=Path(_required_string(value.get("path"), field="browser state path")),
        sha256=digest,
        media_type=_required_string(
            value.get("media_type"), field="browser state media type"
        ),
        bytes=_positive_integer(value.get("bytes"), field="browser state bytes"),
        width=_positive_integer(value.get("width"), field="browser state width"),
        height=_positive_integer(value.get("height"), field="browser state height"),
    )
    _deduplicate_asset(asset, assets)
    return asset_id


def _register_clip_asset(
    value: Mapping[str, Any], assets: dict[str, CompiledAssetSource]
) -> tuple[str, int]:
    digest = value.get("sha256")
    if not _is_sha256(digest):
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", "browser clip hash is invalid"
        )
    duration = _positive_integer(value.get("duration_ms"), field="browser clip duration")
    asset_id = f"clip-{digest}"
    asset = CompiledAssetSource(
        id=asset_id,
        path=Path(_required_string(value.get("path"), field="browser clip path")),
        sha256=digest,
        media_type=_required_string(
            value.get("media_type"), field="browser clip media type"
        ),
        bytes=_positive_integer(
            value.get("encoded_bytes", value.get("bytes")), field="browser clip bytes"
        ),
        width=_positive_integer(value.get("width"), field="browser clip width"),
        height=_positive_integer(value.get("height"), field="browser clip height"),
        duration_ms=duration,
    )
    _deduplicate_asset(asset, assets)
    return asset_id, duration


def _deduplicate_asset(
    asset: CompiledAssetSource, assets: dict[str, CompiledAssetSource]
) -> None:
    existing = assets.get(asset.id)
    if existing is not None and existing != asset:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"conflicting content asset {asset.id!r}"
        )
    assets[asset.id] = asset


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _target_point(
    target: Mapping[str, Any] | None, action_id: str
) -> dict[str, float]:
    if target is None:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"action {action_id!r} has no captured target"
        )
    point = _mapping_value(target.get("point"), field="browser target point")
    return {
        "x": _number(point.get("x"), field="browser target point x"),
        "y": _number(point.get("y"), field="browser target point y"),
    }


def _target_bounds(
    target: Mapping[str, Any] | None, action_id: str
) -> dict[str, float]:
    if target is None:
        raise PresentationCompileError(
            "PRESENTATION_SCHEMA", f"action {action_id!r} has no captured target"
        )
    bounds = _mapping_value(target.get("bounds"), field="browser target bounds")
    return {
        name: _number(bounds.get(name), field=f"browser target bounds {name}")
        for name in ("x", "y", "width", "height")
    }


def _point_values(value: Mapping[str, Any], *, field: str) -> tuple[float, float]:
    return (
        _number(value.get("x"), field=f"{field} x"),
        _number(value.get("y"), field=f"{field} y"),
    )


def _mapping_value(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PresentationCompileError("PRESENTATION_SCHEMA", f"{field} is invalid")
    return dict(value)


def _thaw_mapping(value: FrozenMapping) -> dict[str, Any]:
    def thaw(item: Any) -> Any:
        if isinstance(item, FrozenMapping):
            return {key: thaw(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [thaw(child) for child in item]
        return item

    return {key: thaw(item) for key, item in value.items()}


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PresentationCompileError("PRESENTATION_SCHEMA", f"{field} is invalid")
    return value


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresentationCompileError("PRESENTATION_SCHEMA", f"{field} is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise PresentationCompileError("PRESENTATION_SCHEMA", f"{field} is invalid")
    return number


def _positive_number(value: object, *, field: str) -> float:
    number = _number(value, field=field)
    if number <= 0:
        raise PresentationCompileError("PRESENTATION_SCHEMA", f"{field} is invalid")
    return number


def _positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PresentationCompileError("PRESENTATION_SCHEMA", f"{field} is invalid")
    return value
