"""Generic local layout adjustment for reducing node-edge overlap and avoidable tangles."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .geometry import (
    close_parallel_length,
    distance,
    edge_path_option,
    edge_path_points,
    path_intersections,
    point_polyline_distance,
    polyline_length,
)
from .layout import assign_positions
from .schema import Edge, GraphSpec

MIN_NODE_DISTANCE = 1.12
EDGE_NODE_CLEARANCE = 0.68
EDGE_EDGE_CLEARANCE = 0.34
MAX_CLOSE_PARALLEL_LENGTH = 1.75
MAX_CANVAS_ABS = 16.0
REGULAR_GRID = 0.25


def nudge_node_positions(spec: GraphSpec, max_passes: int = 28) -> GraphSpec:
    positions = assign_positions(spec)
    if not positions:
        return spec

    node_ids = spec.node_ids()
    edge_routes = _copy_edge_routes(spec.layout_hints.get("edge_routes", {}))
    total_motion = 0.0
    for _ in range(max_passes):
        deltas = {node_id: [0.0, 0.0] for node_id in node_ids}

        for index, left in enumerate(node_ids):
            for right in node_ids[index + 1 :]:
                current = distance(positions[left], positions[right])
                if current >= MIN_NODE_DISTANCE:
                    continue
                dx = positions[left][0] - positions[right][0]
                dy = positions[left][1] - positions[right][1]
                ux, uy = _unit(dx, dy, fallback_seed=index + 1)
                push = (MIN_NODE_DISTANCE - current + 0.08) * 0.5
                deltas[left][0] += ux * push
                deltas[left][1] += uy * push
                deltas[right][0] -= ux * push
                deltas[right][1] -= uy * push

        for edge in spec.edges:
            if edge.source == edge.target or edge.source not in positions or edge.target not in positions:
                continue
            path = edge_path_points(edge, positions, edge_path_option(spec, edge), waypoints=_route_for_edge(edge, edge_routes), samples=48)
            if not path:
                continue
            for node_id in node_ids:
                if node_id in {edge.source, edge.target}:
                    continue
                current, closest = point_polyline_distance(positions[node_id], path)
                if current >= EDGE_NODE_CLEARANCE:
                    continue
                dx = positions[node_id][0] - closest[0]
                dy = positions[node_id][1] - closest[1]
                ux, uy = _unit(dx, dy, fallback_seed=len(node_id))
                push = EDGE_NODE_CLEARANCE - current + 0.10
                deltas[node_id][0] += ux * push
                deltas[node_id][1] += uy * push

        max_motion = 0.0
        for node_id, (dx, dy) in deltas.items():
            motion = math.hypot(dx, dy)
            if motion == 0:
                continue
            capped = min(motion, 0.42)
            scale = 0.65 * capped / motion
            positions[node_id] = (positions[node_id][0] + dx * scale, positions[node_id][1] + dy * scale)
            max_motion = max(max_motion, capped)
            total_motion += capped

        if max_motion < 0.01:
            break

    positions, search_motion = _crossing_aware_local_search(spec, positions, node_ids, edge_routes)
    total_motion += search_motion
    positions, edge_routes, polish_motion = _regularize_layout(spec, positions, node_ids, edge_routes)
    total_motion += polish_motion

    positions, edge_routes = _scale_for_clearance(spec, positions, node_ids, edge_routes)
    positions, edge_routes = _fit_to_canvas(positions, edge_routes)

    if total_motion < 0.01:
        original_positions = assign_positions(spec)
        if all(distance(positions[node_id], original_positions[node_id]) < 0.01 for node_id in node_ids):
            return spec

    layout_hints = dict(spec.layout_hints)
    layout_hints["style"] = "explicit"
    layout_hints["positions"] = {node_id: [round(x, 3), round(y, 3)] for node_id, (x, y) in positions.items()}
    if edge_routes:
        layout_hints["edge_routes"] = {
            key: [[round(x, 3), round(y, 3)] for x, y in route] for key, route in edge_routes.items()
        }
    layout_hints["disable_auto_expand"] = True
    warnings = list(spec.warnings)
    warnings.append("Applied local geometry-aware layout polishing to reduce overlaps and improve alignment.")
    return GraphSpec(
        nodes=list(spec.nodes),
        edges=list(spec.edges),
        layout_hints=layout_hints,
        warnings=warnings,
        source_text=spec.source_text,
    )


def _crossing_aware_local_search(
    spec: GraphSpec,
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> tuple[dict[str, tuple[float, float]], float]:
    if len(node_ids) < 3 or not spec.edges:
        return positions, 0.0

    current = dict(positions)
    original = dict(positions)
    groups = _candidate_groups(node_ids)
    current_score = _layout_score(spec, current, node_ids, edge_routes, original)
    total_motion = 0.0

    for step in (1.1, 0.72, 0.46, 0.28):
        for _ in range(4):
            best_positions: dict[str, tuple[float, float]] | None = None
            best_score = current_score
            best_motion = 0.0
            for group in groups:
                for candidate in _candidate_group_moves(current, group, step):
                    score = _layout_score(spec, candidate, node_ids, edge_routes, original)
                    if score + max(0.012, step * 0.012) >= best_score:
                        continue
                    best_positions = candidate
                    best_score = score
                    best_motion = max(distance(current[node_id], candidate[node_id]) for node_id in group)

            if best_positions is None:
                break
            current = best_positions
            current_score = best_score
            total_motion += best_motion

    return current, total_motion


def _regularize_layout(
    spec: GraphSpec,
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[float, float]]], float]:
    current_positions = dict(positions)
    current_routes = {key: list(route) for key, route in edge_routes.items()}
    original_positions = dict(current_positions)
    current_score = _layout_score(spec, current_positions, node_ids, current_routes, original_positions)
    total_motion = 0.0

    for _ in range(3):
        improved = False
        for candidate in _position_regularity_candidates(current_positions, node_ids):
            score = _layout_score(spec, candidate, node_ids, current_routes, original_positions)
            if score + 0.001 >= current_score:
                continue
            total_motion += _max_position_motion(current_positions, candidate)
            current_positions = candidate
            current_score = score
            improved = True

        for candidate_routes in _route_regularity_candidates(current_routes, current_positions):
            score = _layout_score(spec, current_positions, node_ids, candidate_routes, original_positions)
            if score + 0.001 >= current_score:
                continue
            total_motion += _max_route_motion(current_routes, candidate_routes)
            current_routes = candidate_routes
            current_score = score
            improved = True

        if not improved:
            break

    return current_positions, current_routes, total_motion


def _position_regularity_candidates(
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
) -> list[dict[str, tuple[float, float]]]:
    candidates = [
        _snap_positions_to_grid(positions),
        _align_close_coordinate_clusters(positions, axis="x"),
        _align_close_coordinate_clusters(positions, axis="y"),
    ]
    candidates.extend(_regularize_prefix_group_lines(positions, node_ids))
    return [candidate for candidate in candidates if candidate != positions]


def _route_regularity_candidates(
    edge_routes: dict[str, list[tuple[float, float]]],
    positions: dict[str, tuple[float, float]],
) -> list[dict[str, list[tuple[float, float]]]]:
    if not edge_routes:
        return []
    candidates = [
        _straighten_route_waypoint_levels(edge_routes),
        _snap_routes_to_grid(edge_routes),
        _align_route_waypoints(edge_routes, positions),
    ]
    candidates.extend(_route_waypoint_removal_candidates(edge_routes))
    return [candidate for candidate in candidates if candidate != edge_routes]


def _snap_positions_to_grid(
    positions: dict[str, tuple[float, float]],
    step: float = REGULAR_GRID,
) -> dict[str, tuple[float, float]]:
    return {node_id: (_snap(x, step), _snap(y, step)) for node_id, (x, y) in positions.items()}


def _align_close_coordinate_clusters(
    positions: dict[str, tuple[float, float]],
    axis: str,
    threshold: float = 0.32,
) -> dict[str, tuple[float, float]]:
    index = 0 if axis == "x" else 1
    values = sorted((point[index], node_id) for node_id, point in positions.items())
    clusters: list[list[tuple[float, str]]] = []
    for value, node_id in values:
        if not clusters or abs(value - clusters[-1][-1][0]) > threshold:
            clusters.append([(value, node_id)])
        else:
            clusters[-1].append((value, node_id))

    moved = dict(positions)
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        target = _snap(sum(value for value, _ in cluster) / len(cluster), REGULAR_GRID)
        for _, node_id in cluster:
            x, y = moved[node_id]
            moved[node_id] = (target, y) if axis == "x" else (x, target)
    return moved


def _regularize_prefix_group_lines(
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
) -> list[dict[str, tuple[float, float]]]:
    candidates: list[dict[str, tuple[float, float]]] = []
    for group in _prefix_groups(node_ids, min_size=3, max_size=9):
        points = [positions[node_id] for node_id in group]
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        x_span = max(xs) - min(xs)
        y_span = max(ys) - min(ys)
        if x_span > max(1.2, y_span * 1.7):
            candidates.append(_regularize_group_on_axis(positions, group, axis="x"))
        elif y_span > max(1.2, x_span * 1.7):
            candidates.append(_regularize_group_on_axis(positions, group, axis="y"))
    return candidates


def _regularize_group_on_axis(
    positions: dict[str, tuple[float, float]],
    group: tuple[str, ...],
    axis: str,
) -> dict[str, tuple[float, float]]:
    moved = dict(positions)
    sorted_group = sorted(group, key=lambda node_id: positions[node_id][0 if axis == "x" else 1])
    coordinates = [positions[node_id][0 if axis == "x" else 1] for node_id in sorted_group]
    if len(coordinates) < 2:
        return moved
    start = _snap(min(coordinates), REGULAR_GRID)
    end = _snap(max(coordinates), REGULAR_GRID)
    if abs(end - start) < 0.1:
        return moved
    other_values = [positions[node_id][1 if axis == "x" else 0] for node_id in sorted_group]
    other = _snap(sum(other_values) / len(other_values), REGULAR_GRID)
    gap = (end - start) / (len(sorted_group) - 1)
    for index, node_id in enumerate(sorted_group):
        primary = _snap(start + gap * index, REGULAR_GRID)
        moved[node_id] = (primary, other) if axis == "x" else (other, primary)
    return moved


def _snap_routes_to_grid(
    edge_routes: dict[str, list[tuple[float, float]]],
    step: float = REGULAR_GRID,
) -> dict[str, list[tuple[float, float]]]:
    return {key: [(_snap(x, step), _snap(y, step)) for x, y in route] for key, route in edge_routes.items()}


def _straighten_route_waypoint_levels(
    edge_routes: dict[str, list[tuple[float, float]]],
    threshold: float = 0.36,
) -> dict[str, list[tuple[float, float]]]:
    straightened: dict[str, list[tuple[float, float]]] = {}
    for key, route in edge_routes.items():
        if len(route) < 2:
            straightened[key] = list(route)
            continue
        xs = [x for x, _ in route]
        ys = [y for _, y in route]
        if max(ys) - min(ys) <= threshold and max(xs) - min(xs) > threshold:
            target_y = _snap(sum(ys) / len(ys), REGULAR_GRID)
            straightened[key] = [(x, target_y) for x, _ in route]
        elif max(xs) - min(xs) <= threshold and max(ys) - min(ys) > threshold:
            target_x = _snap(sum(xs) / len(xs), REGULAR_GRID)
            straightened[key] = [(target_x, y) for _, y in route]
        else:
            straightened[key] = list(route)
    return straightened


def _align_route_waypoints(
    edge_routes: dict[str, list[tuple[float, float]]],
    positions: dict[str, tuple[float, float]],
    threshold: float = 0.32,
) -> dict[str, list[tuple[float, float]]]:
    x_levels = [x for x, _ in positions.values()]
    y_levels = [y for _, y in positions.values()]
    aligned: dict[str, list[tuple[float, float]]] = {}
    for key, route in edge_routes.items():
        adjusted: list[tuple[float, float]] = []
        for x, y in route:
            adjusted.append((_snap_to_near_level(x, x_levels, threshold), _snap_to_near_level(y, y_levels, threshold)))
        aligned[key] = adjusted
    return aligned


def _route_waypoint_removal_candidates(
    edge_routes: dict[str, list[tuple[float, float]]],
) -> list[dict[str, list[tuple[float, float]]]]:
    candidates: list[dict[str, list[tuple[float, float]]]] = []
    for key, route in edge_routes.items():
        for index in range(len(route)):
            candidate = {route_key: list(points) for route_key, points in edge_routes.items()}
            candidate[key] = route[:index] + route[index + 1 :]
            candidates.append(candidate)
    return candidates


def _candidate_groups(node_ids: list[str]) -> list[tuple[str, ...]]:
    groups: list[tuple[str, ...]] = [(node_id,) for node_id in node_ids]
    seen = set(groups)
    for item in _prefix_groups(node_ids, min_size=2, max_size=8):
        if item not in seen:
            groups.append(item)
            seen.add(item)
    return groups


def _prefix_groups(node_ids: list[str], min_size: int, max_size: int) -> list[tuple[str, ...]]:
    prefix_map: dict[str, list[str]] = {}
    for node_id in node_ids:
        prefix = _node_prefix(node_id)
        if not prefix:
            continue
        prefix_map.setdefault(prefix, []).append(node_id)
    return [tuple(group) for group in prefix_map.values() if min_size <= len(group) <= max_size]


def _node_prefix(node_id: str) -> str:
    prefix = []
    for character in node_id:
        if character.isalpha():
            prefix.append(character)
            continue
        if character == "_" and prefix:
            continue
        break
    return "".join(prefix)


def _candidate_group_moves(
    positions: dict[str, tuple[float, float]],
    group: tuple[str, ...],
    step: float,
) -> list[dict[str, tuple[float, float]]]:
    directions = (
        (1.0, 0.0),
        (-1.0, 0.0),
        (0.0, 1.0),
        (0.0, -1.0),
        (0.7, 0.7),
        (-0.7, 0.7),
        (0.7, -0.7),
        (-0.7, -0.7),
    )
    center = _center(positions.values())
    group_center = _center(positions[node_id] for node_id in group)
    candidates = [_translate_group(positions, group, dx * step, dy * step) for dx, dy in directions]

    for anchor in (center, group_center):
        candidates.append(_move_group_toward_anchor(positions, group, anchor, step, scale=1.0))
        candidates.append(_move_group_toward_anchor(positions, group, anchor, -step, scale=1.0))
        candidates.append(_move_group_toward_anchor(positions, group, anchor, step, scale=1.0, axis="x"))
        candidates.append(_move_group_toward_anchor(positions, group, anchor, -step, scale=1.0, axis="x"))
        candidates.append(_move_group_toward_anchor(positions, group, anchor, step, scale=1.0, axis="y"))
        candidates.append(_move_group_toward_anchor(positions, group, anchor, -step, scale=1.0, axis="y"))

    return candidates


def _translate_group(
    positions: dict[str, tuple[float, float]],
    group: tuple[str, ...],
    dx: float,
    dy: float,
) -> dict[str, tuple[float, float]]:
    moved = dict(positions)
    for node_id in group:
        x, y = moved[node_id]
        moved[node_id] = (x + dx, y + dy)
    return moved


def _move_group_toward_anchor(
    positions: dict[str, tuple[float, float]],
    group: tuple[str, ...],
    anchor: tuple[float, float],
    step: float,
    scale: float = 1.0,
    axis: str | None = None,
) -> dict[str, tuple[float, float]]:
    moved = dict(positions)
    for node_id in group:
        x, y = moved[node_id]
        dx = anchor[0] - x
        dy = anchor[1] - y
        if axis == "x":
            dy = 0.0
        elif axis == "y":
            dx = 0.0
        ux, uy = _unit(dx, dy, fallback_seed=len(node_id))
        moved[node_id] = (x + ux * step * scale, y + uy * step * scale)
    return moved


def _layout_score(
    spec: GraphSpec,
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
    edge_routes: dict[str, list[tuple[float, float]]],
    original_positions: dict[str, tuple[float, float]],
) -> float:
    score = 0.0
    edge_paths = _edge_paths(spec, positions, edge_routes)

    for index, left in enumerate(node_ids):
        for right in node_ids[index + 1 :]:
            current = distance(positions[left], positions[right])
            if current < MIN_NODE_DISTANCE:
                score += 95.0 * (MIN_NODE_DISTANCE - current) ** 2

    for edge, path in edge_paths:
        route_length = polyline_length(path)
        straight_length = distance(positions[edge.source], positions[edge.target])
        score += 0.018 * route_length
        if straight_length > 0 and len(path) > 2:
            excess_ratio = max(0.0, route_length / straight_length - 1.45)
            score += 3.5 * excess_ratio * excess_ratio
        for node_id in node_ids:
            if node_id in {edge.source, edge.target}:
                continue
            current, _ = point_polyline_distance(positions[node_id], path)
            if current < EDGE_NODE_CLEARANCE:
                score += 120.0 * (EDGE_NODE_CLEARANCE - current) ** 2
            elif current < EDGE_NODE_CLEARANCE * 1.35:
                score += 7.0 * (EDGE_NODE_CLEARANCE * 1.35 - current) ** 2

    for left_index, (left_edge, left_path) in enumerate(edge_paths):
        for right_edge, right_path in edge_paths[left_index + 1 :]:
            if {left_edge.source, left_edge.target} & {right_edge.source, right_edge.target}:
                continue
            intersections = path_intersections(left_path, right_path)
            if intersections:
                score += 30.0 * len(intersections)
                for point in intersections:
                    score += _near_node_crossing_penalty(point, positions, node_ids, left_edge, right_edge)

            close_length = close_parallel_length(left_path, right_path, EDGE_EDGE_CLEARANCE)
            if close_length > 0.2:
                score += 4.0 * close_length
            if close_length > MAX_CLOSE_PARALLEL_LENGTH:
                score += 22.0 * (close_length - MAX_CLOSE_PARALLEL_LENGTH) ** 2

    score += _regularity_score(positions, node_ids, edge_paths, edge_routes)

    for node_id in node_ids:
        score += 0.012 * distance(positions[node_id], original_positions[node_id]) ** 2
        x, y = positions[node_id]
        overflow = max(0.0, abs(x) - MAX_CANVAS_ABS * 0.95) + max(0.0, abs(y) - MAX_CANVAS_ABS * 0.95)
        score += 6.0 * overflow * overflow

    score += _compactness_penalty(positions)

    return score


def _edge_paths(
    spec: GraphSpec,
    positions: dict[str, tuple[float, float]],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> list[tuple[Edge, list[tuple[float, float]]]]:
    paths: list[tuple[Edge, list[tuple[float, float]]]] = []
    for edge in spec.edges:
        if edge.source == edge.target or edge.source not in positions or edge.target not in positions:
            continue
        paths.append(
            (
                edge,
                edge_path_points(
                    edge,
                    positions,
                    edge_path_option(spec, edge),
                    waypoints=_route_for_edge(edge, edge_routes),
                    samples=40,
                ),
            )
        )
    return paths


def _near_node_crossing_penalty(
    point: tuple[float, float],
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
    left_edge: Edge,
    right_edge: Edge,
) -> float:
    endpoints = {left_edge.source, left_edge.target, right_edge.source, right_edge.target}
    candidate_distances = [distance(point, positions[node_id]) for node_id in node_ids if node_id not in endpoints]
    if not candidate_distances:
        return 0.0
    nearest = min(candidate_distances)
    if nearest >= 1.2:
        return 0.0
    return 24.0 * (1.2 - nearest) ** 2


def _regularity_score(
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
    edge_paths: list[tuple[Edge, list[tuple[float, float]]]],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> float:
    score = 0.0
    for x, y in positions.values():
        score += 0.08 * (_grid_error(x) ** 2 + _grid_error(y) ** 2)

    for index, left in enumerate(node_ids):
        for right in node_ids[index + 1 :]:
            dx = abs(positions[left][0] - positions[right][0])
            dy = abs(positions[left][1] - positions[right][1])
            if 0.02 < dx < 0.32:
                score += 0.6 * dx * dx
            if 0.02 < dy < 0.32:
                score += 0.6 * dy * dy

    for group in _prefix_groups(node_ids, min_size=3, max_size=9):
        score += _prefix_group_regularity_score(positions, group)

    for _, path in edge_paths:
        score += _path_regularity_score(path)
    for route in edge_routes.values():
        score += 0.18 * len(route)
        score += 0.15 * sum(_grid_error(value) ** 2 for point in route for value in point)

    return score


def _prefix_group_regularity_score(
    positions: dict[str, tuple[float, float]],
    group: tuple[str, ...],
) -> float:
    points = [positions[node_id] for node_id in group]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > max(1.2, y_span * 1.7):
        ordered = sorted(xs)
        return 0.35 * _variance(ys) + 0.08 * _spacing_variance(ordered)
    if y_span > max(1.2, x_span * 1.7):
        ordered = sorted(ys)
        return 0.35 * _variance(xs) + 0.08 * _spacing_variance(ordered)
    if len(points) >= 4 and x_span > 1.0 and y_span > 1.0:
        center = _center(points)
        radii = [distance(point, center) for point in points]
        return 0.08 * _variance(radii)
    return 0.0


def _path_regularity_score(path: list[tuple[float, float]]) -> float:
    score = 0.0
    for start, end in zip(path, path[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 0.8:
            continue
        if abs(dy) / length < 0.14:
            score += 0.10 * dy * dy
        if abs(dx) / length < 0.14:
            score += 0.10 * dx * dx
        diagonal_error = abs(abs(dx) - abs(dy))
        if diagonal_error / length < 0.12:
            score += 0.035 * diagonal_error * diagonal_error
    return score


def _compactness_penalty(positions: dict[str, tuple[float, float]]) -> float:
    if len(positions) < 2:
        return 0.0
    xs = [x for x, _ in positions.values()]
    ys = [y for _, y in positions.values()]
    area_per_node = ((max(xs) - min(xs)) * (max(ys) - min(ys))) / len(positions)
    if area_per_node <= 9.0:
        return 0.0
    return 0.035 * (area_per_node - 9.0) ** 2


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _spacing_variance(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    gaps = [right - left for left, right in zip(values, values[1:])]
    return _variance(gaps)


def _max_position_motion(
    before: dict[str, tuple[float, float]],
    after: dict[str, tuple[float, float]],
) -> float:
    return max((distance(before[node_id], after[node_id]) for node_id in before if node_id in after), default=0.0)


def _max_route_motion(
    before: dict[str, list[tuple[float, float]]],
    after: dict[str, list[tuple[float, float]]],
) -> float:
    motions = []
    for key, route in before.items():
        candidate = after.get(key, [])
        motions.extend(distance(left, right) for left, right in zip(route, candidate))
        if len(route) != len(candidate):
            motions.append(0.25 * abs(len(route) - len(candidate)))
    return max(motions, default=0.0)


def _snap(value: float, step: float) -> float:
    return round(value / step) * step


def _grid_error(value: float, step: float = REGULAR_GRID) -> float:
    return abs(value - _snap(value, step))


def _snap_to_near_level(value: float, levels: list[float], threshold: float) -> float:
    if not levels:
        return value
    nearest = min(levels, key=lambda level: abs(level - value))
    if abs(nearest - value) <= threshold:
        return _snap(nearest, REGULAR_GRID)
    return value


def _center(points: Iterable[tuple[float, float]]) -> tuple[float, float]:
    values = list(points)
    if not values:
        return (0.0, 0.0)
    return (sum(x for x, _ in values) / len(values), sum(y for _, y in values) / len(values))


def _unit(dx: float, dy: float, fallback_seed: int = 1) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length > 1e-9:
        return dx / length, dy / length
    angle = (fallback_seed * 137.5) % 360
    return math.cos(math.radians(angle)), math.sin(math.radians(angle))


def _scale_for_clearance(
    spec: GraphSpec,
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[float, float]]]]:
    min_node_distance = float("inf")
    for index, left in enumerate(node_ids):
        for right in node_ids[index + 1 :]:
            min_node_distance = min(min_node_distance, distance(positions[left], positions[right]))

    min_edge_clearance = float("inf")
    for edge in spec.edges:
        if edge.source == edge.target or edge.source not in positions or edge.target not in positions:
            continue
        path = edge_path_points(edge, positions, edge_path_option(spec, edge), waypoints=_route_for_edge(edge, edge_routes), samples=48)
        for node_id in node_ids:
            if node_id in {edge.source, edge.target}:
                continue
            current, _ = point_polyline_distance(positions[node_id], path)
            min_edge_clearance = min(min_edge_clearance, current)

    scale = 1.0
    if 0 < min_node_distance < MIN_NODE_DISTANCE:
        scale = max(scale, MIN_NODE_DISTANCE / min_node_distance * 1.12)
    if 0 < min_edge_clearance < EDGE_NODE_CLEARANCE:
        scale = max(scale, EDGE_NODE_CLEARANCE / min_edge_clearance * 1.12)

    scale = min(scale, 3.0)
    if scale <= 1.01:
        return positions, edge_routes

    center_x = sum(x for x, _ in positions.values()) / len(positions)
    center_y = sum(y for _, y in positions.values()) / len(positions)
    scaled_positions = {
        node_id: (center_x + (x - center_x) * scale, center_y + (y - center_y) * scale)
        for node_id, (x, y) in positions.items()
    }
    scaled_routes = {
        key: [(center_x + (x - center_x) * scale, center_y + (y - center_y) * scale) for x, y in route]
        for key, route in edge_routes.items()
    }
    return scaled_positions, scaled_routes


def _fit_to_canvas(
    positions: dict[str, tuple[float, float]],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[float, float]]]]:
    if not positions:
        return positions, edge_routes
    all_points = list(positions.values()) + [point for route in edge_routes.values() for point in route]
    max_abs = max(max(abs(x), abs(y)) for x, y in all_points)
    if max_abs <= MAX_CANVAS_ABS:
        return positions, edge_routes
    scale = MAX_CANVAS_ABS / max_abs
    fitted_positions = {node_id: (x * scale, y * scale) for node_id, (x, y) in positions.items()}
    fitted_routes = {key: [(x * scale, y * scale) for x, y in route] for key, route in edge_routes.items()}
    return fitted_positions, fitted_routes


def _copy_edge_routes(raw_routes: object) -> dict[str, list[tuple[float, float]]]:
    if not isinstance(raw_routes, dict):
        return {}
    routes: dict[str, list[tuple[float, float]]] = {}
    for key, raw_route in raw_routes.items():
        if not isinstance(raw_route, list):
            continue
        route: list[tuple[float, float]] = []
        for point in raw_route:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                route.append((float(point[0]), float(point[1])))
        if route:
            routes[str(key)] = route
    return routes


def _route_for_edge(edge, edge_routes: dict[str, list[tuple[float, float]]]) -> list[tuple[float, float]]:
    if edge.directed:
        return edge_routes.get(f"directed:{edge.source}:{edge.target}", [])
    left, right = sorted((edge.source, edge.target))
    return edge_routes.get(f"undirected:{left}:{right}", [])
