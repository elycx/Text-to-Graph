"""Geometry helpers for graph layout validation and repair."""

from __future__ import annotations

import math
import re

from .schema import Edge, GraphSpec

Position = tuple[float, float]


def edge_path_option(spec: GraphSpec, edge: Edge) -> str:
    options = spec.layout_hints.get("edge_options", {})
    if not isinstance(options, dict):
        return ""
    if edge.directed:
        key = f"directed:{edge.source}:{edge.target}"
    else:
        left, right = sorted((edge.source, edge.target))
        key = f"undirected:{left}:{right}"
    value = options.get(key, "")
    return str(value) if value else ""


def edge_route_waypoints(spec: GraphSpec, edge: Edge) -> list[Position]:
    routes = spec.layout_hints.get("edge_routes", {})
    if not isinstance(routes, dict):
        return []
    value = routes.get(_edge_key(edge), [])
    if not isinstance(value, list):
        return []
    waypoints: list[Position] = []
    for point in value:
        if isinstance(point, tuple) and len(point) == 2:
            waypoints.append((float(point[0]), float(point[1])))
        elif isinstance(point, list) and len(point) == 2:
            waypoints.append((float(point[0]), float(point[1])))
    return waypoints


def edge_path_points(
    edge: Edge,
    positions: dict[str, Position],
    option: str = "",
    waypoints: list[Position] | None = None,
    samples: int = 36,
) -> list[Position]:
    start = positions[edge.source]
    end = positions[edge.target]
    if edge.source == edge.target:
        return []
    if waypoints:
        return [start] + waypoints + [end]

    lowered = option.lower()
    if "out=" in lowered or "in=" in lowered:
        return _out_in_curve(start, end, option, samples)
    if "bend left" in lowered or "bend right" in lowered:
        return _bend_curve(start, end, option, samples)
    return [start, end]


def point_polyline_distance(point: Position, path: list[Position]) -> tuple[float, Position]:
    if not path:
        return (float("inf"), point)
    if len(path) == 1:
        return (_distance(point, path[0]), path[0])

    best_distance = float("inf")
    best_projection = path[0]
    for start, end in zip(path, path[1:]):
        distance, projection = point_segment_distance(point, start, end)
        if distance < best_distance:
            best_distance = distance
            best_projection = projection
    return best_distance, best_projection


def point_segment_distance(point: Position, start: Position, end: Position) -> tuple[float, Position]:
    segment_x = end[0] - start[0]
    segment_y = end[1] - start[1]
    length_squared = segment_x * segment_x + segment_y * segment_y
    if length_squared == 0:
        return (_distance(point, start), start)
    t = ((point[0] - start[0]) * segment_x + (point[1] - start[1]) * segment_y) / length_squared
    t = max(0.0, min(1.0, t))
    projection = (start[0] + t * segment_x, start[1] + t * segment_y)
    return (_distance(point, projection), projection)


def distance(left: Position, right: Position) -> float:
    return _distance(left, right)


def polyline_length(path: list[Position]) -> float:
    if len(path) < 2:
        return 0.0
    return sum(_distance(start, end) for start, end in zip(path, path[1:]))


def close_parallel_length(path_a: list[Position], path_b: list[Position], threshold: float, step: float = 0.32) -> float:
    if len(path_a) < 2 or len(path_b) < 2:
        return 0.0
    return min(
        _close_length_one_way(path_a, path_b, threshold, step),
        _close_length_one_way(path_b, path_a, threshold, step),
    )


def path_intersections(path_a: list[Position], path_b: list[Position]) -> list[Position]:
    if len(path_a) < 2 or len(path_b) < 2:
        return []
    intersections: list[Position] = []
    for start_a, end_a in zip(path_a, path_a[1:]):
        for start_b, end_b in zip(path_b, path_b[1:]):
            point = _segment_intersection(start_a, end_a, start_b, end_b)
            if point is None:
                continue
            if any(_distance(point, existing) < 0.05 for existing in intersections):
                continue
            intersections.append(point)
    return intersections


def _edge_key(edge: Edge) -> str:
    if edge.directed:
        return f"directed:{edge.source}:{edge.target}"
    left, right = sorted((edge.source, edge.target))
    return f"undirected:{left}:{right}"


def _close_length_one_way(path_a: list[Position], path_b: list[Position], threshold: float, step: float) -> float:
    close_length = 0.0
    for start, end in zip(path_a, path_a[1:]):
        segment_length = _distance(start, end)
        if segment_length == 0:
            continue
        steps = max(1, math.ceil(segment_length / step))
        increment = segment_length / steps
        for index in range(steps):
            t = (index + 0.5) / steps
            sample = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
            current_distance, _ = point_polyline_distance(sample, path_b)
            if current_distance < threshold:
                close_length += increment
    return close_length


def _segment_intersection(start_a: Position, end_a: Position, start_b: Position, end_b: Position) -> Position | None:
    ax = end_a[0] - start_a[0]
    ay = end_a[1] - start_a[1]
    bx = end_b[0] - start_b[0]
    by = end_b[1] - start_b[1]
    denominator = ax * by - ay * bx
    if abs(denominator) < 1e-9:
        return None

    qx = start_b[0] - start_a[0]
    qy = start_b[1] - start_a[1]
    t = (qx * by - qy * bx) / denominator
    u = (qx * ay - qy * ax) / denominator
    if -1e-7 <= t <= 1.0 + 1e-7 and -1e-7 <= u <= 1.0 + 1e-7:
        return (start_a[0] + t * ax, start_a[1] + t * ay)
    return None


def _bend_curve(start: Position, end: Position, option: str, samples: int) -> list[Position]:
    length = _distance(start, end)
    if length == 0:
        return []
    direction = -1.0 if "bend right" in option.lower() else 1.0
    angle = _extract_number(r"bend\s+(?:left|right)\s*=\s*(-?\d+(?:\.\d+)?)", option, 30.0)
    offset = max(0.18 * length, math.tan(math.radians(abs(angle))) * 0.55 * length)
    dx = (end[0] - start[0]) / length
    dy = (end[1] - start[1]) / length
    normal = (-dy * direction, dx * direction)
    control = ((start[0] + end[0]) / 2 + normal[0] * offset, (start[1] + end[1]) / 2 + normal[1] * offset)
    return [_quadratic(start, control, end, index / samples) for index in range(samples + 1)]


def _out_in_curve(start: Position, end: Position, option: str, samples: int) -> list[Position]:
    length = max(_distance(start, end), 1.0)
    looseness = _extract_number(r"looseness\s*=\s*(-?\d+(?:\.\d+)?)", option, 1.0)
    out_angle = _extract_number(r"out\s*=\s*(-?\d+(?:\.\d+)?)", option, 0.0)
    in_angle = _extract_number(r"in\s*=\s*(-?\d+(?:\.\d+)?)", option, 180.0)
    handle = 0.34 * length * max(0.35, min(abs(looseness), 3.5))
    c1 = (start[0] + math.cos(math.radians(out_angle)) * handle, start[1] + math.sin(math.radians(out_angle)) * handle)
    c2 = (end[0] + math.cos(math.radians(in_angle)) * handle, end[1] + math.sin(math.radians(in_angle)) * handle)
    return [_cubic(start, c1, c2, end, index / samples) for index in range(samples + 1)]


def _quadratic(start: Position, control: Position, end: Position, t: float) -> Position:
    u = 1 - t
    return (
        u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
        u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
    )


def _cubic(start: Position, c1: Position, c2: Position, end: Position, t: float) -> Position:
    u = 1 - t
    return (
        u**3 * start[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * end[0],
        u**3 * start[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * end[1],
    )


def _extract_number(pattern: str, text: str, default: float) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return default
    try:
        return float(match.group(1))
    except ValueError:
        return default


def _distance(left: Position, right: Position) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])
