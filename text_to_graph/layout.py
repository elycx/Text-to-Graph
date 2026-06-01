"""Deterministic layout strategies for small graphs."""

from __future__ import annotations

import math
from collections import defaultdict, deque

from .schema import GraphSpec

Position = tuple[float, float]


def assign_positions(spec: GraphSpec) -> dict[str, Position]:
    nodes = spec.node_ids()
    if not nodes:
        return {}

    explicit = spec.layout_hints.get("positions")
    if isinstance(explicit, dict):
        return _auto_expand_positions(_explicit_layout(nodes, explicit), spec)

    positions: dict[str, Position] = {}
    _apply_manual_relations(spec, positions)

    remaining = [node_id for node_id in nodes if node_id not in positions]
    if not remaining:
        return positions

    style = str(spec.layout_hints.get("style", "circular"))
    offset = len(positions)
    if style == "linear":
        generated = _linear_layout(remaining)
    elif style == "layered":
        generated = _layered_layout(spec, remaining)
    elif style == "star":
        generated = _star_layout(remaining)
    else:
        generated = _circular_layout(remaining)

    if offset:
        generated = {node: (x, y - 2.8) for node, (x, y) in generated.items()}
    positions.update(generated)
    return _auto_expand_positions(positions, spec)


def _circular_layout(nodes: list[str]) -> dict[str, Position]:
    n = len(nodes)
    radius = max(1.6, 0.55 * n)
    if n == 1:
        return {nodes[0]: (0.0, 0.0)}
    return {
        node: (
            radius * math.cos(2 * math.pi * index / n + math.pi / 2),
            radius * math.sin(2 * math.pi * index / n + math.pi / 2),
        )
        for index, node in enumerate(nodes)
    }


def _explicit_layout(nodes: list[str], explicit: dict[object, object]) -> dict[str, Position]:
    positions: dict[str, Position] = {}
    for node in nodes:
        raw_position = explicit.get(node)
        if (
            isinstance(raw_position, (list, tuple))
            and len(raw_position) == 2
            and all(isinstance(value, (int, float)) for value in raw_position)
        ):
            positions[node] = (float(raw_position[0]), float(raw_position[1]))

    missing = [node for node in nodes if node not in positions]
    if missing:
        fallback = _circular_layout(missing)
        positions.update(fallback)
    return positions


def _linear_layout(nodes: list[str]) -> dict[str, Position]:
    spacing = 1.9
    start = -spacing * (len(nodes) - 1) / 2
    return {node: (start + index * spacing, 0.0) for index, node in enumerate(nodes)}


def _star_layout(nodes: list[str]) -> dict[str, Position]:
    if len(nodes) <= 2:
        return _linear_layout(nodes)
    positions = {nodes[0]: (0.0, 0.0)}
    outer = _circular_layout(nodes[1:])
    positions.update({node: (x * 1.15, y * 1.15) for node, (x, y) in outer.items()})
    return positions


def _layered_layout(spec: GraphSpec, nodes: list[str]) -> dict[str, Position]:
    directed_edges = [edge for edge in spec.edges if edge.directed and edge.source in nodes and edge.target in nodes]
    if not directed_edges:
        return _circular_layout(nodes)

    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming_count = {node: 0 for node in nodes}
    for edge in directed_edges:
        outgoing[edge.source].append(edge.target)
        incoming_count[edge.target] += 1

    queue = deque([node for node in nodes if incoming_count[node] == 0])
    levels = {node: 0 for node in nodes}
    visited = 0
    while queue:
        source = queue.popleft()
        visited += 1
        for target in outgoing[source]:
            levels[target] = max(levels[target], levels[source] + 1)
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                queue.append(target)

    if visited < len(nodes):
        return _circular_layout(nodes)

    by_level: dict[int, list[str]] = defaultdict(list)
    for node in nodes:
        by_level[levels[node]].append(node)

    spacing_x = 2.2
    spacing_y = 1.5
    max_level = max(by_level) if by_level else 0
    positions: dict[str, Position] = {}
    for level, level_nodes in by_level.items():
        x = (level - max_level / 2) * spacing_x
        start_y = spacing_y * (len(level_nodes) - 1) / 2
        for index, node in enumerate(level_nodes):
            positions[node] = (x, start_y - index * spacing_y)
    return positions


def _apply_manual_relations(spec: GraphSpec, positions: dict[str, Position]) -> None:
    relations = spec.layout_hints.get("relations", [])
    if not isinstance(relations, list):
        return

    spacing = 2.0
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = relation.get("source")
        target = relation.get("target")
        kind = relation.get("relation")
        if not source or not target:
            continue
        target_pos = positions.get(target, (0.0, 0.0))
        source_pos = positions.get(source)
        if kind == "left_of":
            positions[source] = source_pos or (target_pos[0] - spacing, target_pos[1])
            positions.setdefault(target, target_pos)
        elif kind == "right_of":
            positions[source] = source_pos or (target_pos[0] + spacing, target_pos[1])
            positions.setdefault(target, target_pos)
        elif kind == "above":
            positions[source] = source_pos or (target_pos[0], target_pos[1] + spacing)
            positions.setdefault(target, target_pos)
        elif kind == "below":
            positions[source] = source_pos or (target_pos[0], target_pos[1] - spacing)
            positions.setdefault(target, target_pos)


def _auto_expand_positions(positions: dict[str, Position], spec: GraphSpec) -> dict[str, Position]:
    if not positions:
        return positions
    if spec.layout_hints.get("disable_auto_expand"):
        return positions
    complexity = len(spec.nodes) + len(spec.edges) / 2
    if complexity < 24:
        return positions
    scale = 1.15
    if complexity >= 34:
        scale = 1.3
    if complexity >= 48:
        scale = 1.45

    center_x = sum(x for x, _ in positions.values()) / len(positions)
    center_y = sum(y for _, y in positions.values()) / len(positions)
    return {
        node: (center_x + (x - center_x) * scale, center_y + (y - center_y) * scale)
        for node, (x, y) in positions.items()
    }
