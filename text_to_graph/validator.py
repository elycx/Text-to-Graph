"""Validation and metric helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .geometry import (
    close_parallel_length,
    distance,
    edge_path_option,
    edge_path_points,
    edge_route_waypoints,
    path_intersections,
    point_polyline_distance,
    polyline_length,
)
from .layout import assign_positions
from .layout_adjust import EDGE_NODE_CLEARANCE, MIN_NODE_DISTANCE
from .schema import Edge, GraphSpec, Node

EDGE_EDGE_CLEARANCE = 0.34
MAX_CLOSE_PARALLEL_LENGTH = 1.75
MAX_CLEAR_DETOUR_RATIO = 2.25
MAX_CLEAR_DETOUR_EXCESS = 2.5


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, list[str]]:
        return {"errors": self.errors, "warnings": self.warnings}


def repair_graph(spec: GraphSpec) -> GraphSpec:
    nodes: dict[str, Node] = {}
    for node in spec.nodes:
        if node.id not in nodes:
            nodes[node.id] = node

    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    warnings = list(spec.warnings)
    for edge in spec.edges:
        nodes.setdefault(edge.source, Node(edge.source, edge.source))
        nodes.setdefault(edge.target, Node(edge.target, edge.target))
        key = edge.key()
        if key in seen_edges:
            warnings.append(f"Removed duplicate edge {edge.source} to {edge.target}.")
            continue
        seen_edges.add(key)
        edges.append(edge)

    return GraphSpec(
        nodes=list(nodes.values()),
        edges=edges,
        layout_hints=dict(spec.layout_hints),
        warnings=warnings,
        source_text=spec.source_text,
    )


def validate_graph(spec: GraphSpec) -> ValidationReport:
    report = ValidationReport(warnings=list(spec.warnings))
    seen_nodes: set[str] = set()
    for node in spec.nodes:
        if not node.id:
            report.errors.append("A node has an empty id.")
        if node.id in seen_nodes:
            report.errors.append(f"Duplicate node id: {node.id}.")
        seen_nodes.add(node.id)

    if not spec.nodes:
        report.errors.append("No nodes were extracted.")

    seen_edges: set[tuple[str, str, str]] = set()
    for edge in spec.edges:
        if edge.source not in seen_nodes:
            report.errors.append(f"Edge source {edge.source} is missing from node list.")
        if edge.target not in seen_nodes:
            report.errors.append(f"Edge target {edge.target} is missing from node list.")
        key = edge.key()
        if key in seen_edges:
            report.errors.append(f"Duplicate edge {edge.source} to {edge.target}.")
        seen_edges.add(key)

    if spec.nodes and not spec.edges:
        report.warnings.append("No edges were extracted.")

    return report


def validate_generated_code(code: str, spec: GraphSpec) -> ValidationReport:
    report = ValidationReport()
    if r"\begin{tikzpicture}" not in code or r"\end{tikzpicture}" not in code:
        report.errors.append("TikZ picture environment is missing.")
    for edge in spec.edges:
        if edge.source not in spec.node_ids() or edge.target not in spec.node_ids():
            report.errors.append(f"Edge {edge.source} to {edge.target} references a missing node.")
    return report


def validate_layout_quality(spec: GraphSpec) -> ValidationReport:
    report = ValidationReport()
    positions = assign_positions(spec)
    node_ids = spec.node_ids()
    edge_paths: list[tuple[Edge, list[tuple[float, float]]]] = []
    for index, left in enumerate(node_ids):
        for right in node_ids[index + 1 :]:
            current_distance = distance(positions[left], positions[right])
            if current_distance < MIN_NODE_DISTANCE:
                report.errors.append(
                    f"Nodes {left} and {right} overlap or are too close: distance {current_distance:.2f}."
                )

    for edge in spec.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in positions or edge.target not in positions:
            continue
        path = edge_path_points(
            edge,
            positions,
            edge_path_option(spec, edge),
            waypoints=edge_route_waypoints(spec, edge),
            samples=48,
        )
        edge_paths.append((edge, path))
        for node_id in node_ids:
            if node_id in {edge.source, edge.target}:
                continue
            current_distance, _ = point_polyline_distance(positions[node_id], path)
            if current_distance < EDGE_NODE_CLEARANCE:
                report.errors.append(
                    f"Edge {edge.source}-{edge.target} passes too close to unrelated node {node_id}: distance {current_distance:.2f}."
                )

        _validate_unnecessary_detour(report, edge, path, positions, node_ids)

    for left_index, (left_edge, left_path) in enumerate(edge_paths):
        for right_edge, right_path in edge_paths[left_index + 1 :]:
            if {left_edge.source, left_edge.target} & {right_edge.source, right_edge.target}:
                continue
            close_length = close_parallel_length(left_path, right_path, EDGE_EDGE_CLEARANCE)
            if close_length > MAX_CLOSE_PARALLEL_LENGTH:
                report.errors.append(
                    f"Edges {left_edge.source}-{left_edge.target} and {right_edge.source}-{right_edge.target} "
                    f"run too close together for a long segment: close length {close_length:.2f}."
                )

    return report


def layout_quality_metrics(spec: GraphSpec) -> dict[str, float | None]:
    positions = assign_positions(spec)
    node_ids = spec.node_ids()
    min_node_distance: float | None = None
    min_edge_node_clearance: float | None = None
    min_edge_edge_clearance: float | None = None
    max_close_parallel_length: float | None = None
    max_clear_detour_ratio: float | None = None
    edge_crossings = 0
    edge_paths: list[tuple[Edge, list[tuple[float, float]]]] = []

    for index, left in enumerate(node_ids):
        for right in node_ids[index + 1 :]:
            current = distance(positions[left], positions[right])
            min_node_distance = current if min_node_distance is None else min(min_node_distance, current)

    for edge in spec.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in positions or edge.target not in positions:
            continue
        path = edge_path_points(
            edge,
            positions,
            edge_path_option(spec, edge),
            waypoints=edge_route_waypoints(spec, edge),
            samples=48,
        )
        edge_paths.append((edge, path))
        route_length = polyline_length(path)
        straight_length = distance(positions[edge.source], positions[edge.target])
        if straight_length > 0:
            ratio = route_length / straight_length
            max_clear_detour_ratio = ratio if max_clear_detour_ratio is None else max(max_clear_detour_ratio, ratio)
        for node_id in node_ids:
            if node_id in {edge.source, edge.target}:
                continue
            current, _ = point_polyline_distance(positions[node_id], path)
            min_edge_node_clearance = (
                current if min_edge_node_clearance is None else min(min_edge_node_clearance, current)
            )

    for left_index, (left_edge, left_path) in enumerate(edge_paths):
        for right_edge, right_path in edge_paths[left_index + 1 :]:
            if {left_edge.source, left_edge.target} & {right_edge.source, right_edge.target}:
                continue
            close_length = close_parallel_length(left_path, right_path, EDGE_EDGE_CLEARANCE)
            max_close_parallel_length = (
                close_length if max_close_parallel_length is None else max(max_close_parallel_length, close_length)
            )
            edge_crossings += len(path_intersections(left_path, right_path))
            for point in _path_samples(left_path):
                current, _ = point_polyline_distance(point, right_path)
                min_edge_edge_clearance = (
                    current if min_edge_edge_clearance is None else min(min_edge_edge_clearance, current)
                )

    return {
        "min_node_distance": min_node_distance,
        "min_edge_node_clearance": min_edge_node_clearance,
        "min_edge_edge_clearance": min_edge_edge_clearance,
        "max_close_parallel_length": max_close_parallel_length,
        "max_clear_detour_ratio": max_clear_detour_ratio,
        "edge_crossings": float(edge_crossings),
    }


def _validate_unnecessary_detour(
    report: ValidationReport,
    edge: Edge,
    path: list[tuple[float, float]],
    positions: dict[str, tuple[float, float]],
    node_ids: list[str],
) -> None:
    if len(path) < 3:
        return
    straight_path = [positions[edge.source], positions[edge.target]]
    straight_length = polyline_length(straight_path)
    route_length = polyline_length(path)
    if straight_length <= 1.0 or route_length - straight_length <= MAX_CLEAR_DETOUR_EXCESS:
        return
    if route_length / straight_length <= MAX_CLEAR_DETOUR_RATIO:
        return

    direct_clearance = float("inf")
    for node_id in node_ids:
        if node_id in {edge.source, edge.target}:
            continue
        current, _ = point_polyline_distance(positions[node_id], straight_path)
        direct_clearance = min(direct_clearance, current)
    if direct_clearance >= EDGE_NODE_CLEARANCE * 1.35:
        report.errors.append(
            f"Edge {edge.source}-{edge.target} appears over-routed: routed length {route_length:.2f} "
            f"vs direct length {straight_length:.2f}, while a direct or shallow route has node clearance {direct_clearance:.2f}."
        )


def _path_samples(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []
    for start, end in zip(path, path[1:]):
        segment_length = distance(start, end)
        steps = max(1, int(segment_length / 0.35))
        for index in range(steps):
            t = (index + 0.5) / steps
            samples.append((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
    return samples


def compare_graphs(predicted: GraphSpec, expected: dict[str, Any]) -> dict[str, Any]:
    expected_nodes = set(_expected_node_ids(expected))
    predicted_nodes = set(predicted.node_ids())
    expected_edges = {_edge_key(Edge.from_dict(edge)) for edge in expected.get("edges", [])}
    predicted_edges = {_edge_key(edge) for edge in predicted.edges}
    expected_directed = {_directed_key(Edge.from_dict(edge)) for edge in expected.get("edges", []) if edge.get("directed", False)}
    predicted_directed = {_directed_key(edge) for edge in predicted.edges if edge.directed}

    node_correct = len(expected_nodes & predicted_nodes)
    edge_correct = len(expected_edges & predicted_edges)
    direction_correct = len(expected_directed & predicted_directed)

    return {
        "node_accuracy": _safe_div(node_correct, len(expected_nodes)),
        "edge_accuracy": _safe_div(edge_correct, len(expected_edges)),
        "direction_accuracy": _safe_div(direction_correct, len(expected_directed)),
        "node_exact": expected_nodes == predicted_nodes,
        "edge_exact": expected_edges == predicted_edges,
        "graph_exact": expected_nodes == predicted_nodes and expected_edges == predicted_edges,
        "expected_nodes": sorted(expected_nodes),
        "predicted_nodes": sorted(predicted_nodes),
        "expected_edges": sorted(expected_edges),
        "predicted_edges": sorted(predicted_edges),
    }


def _expected_node_ids(expected: dict[str, Any]) -> list[str]:
    nodes = expected.get("nodes", [])
    result: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            result.append(node)
        else:
            result.append(str(node["id"]))
    return result


def _edge_key(edge: Edge) -> tuple[str, str, str]:
    return edge.key()


def _directed_key(edge: Edge) -> tuple[str, str]:
    return (edge.source, edge.target)


def _safe_div(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator
