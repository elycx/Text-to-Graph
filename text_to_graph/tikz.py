"""TikZ generation from structured graph specs."""

from __future__ import annotations

import re

from .layout import assign_positions
from .schema import GraphSpec


def generate_latex_document(spec: GraphSpec) -> str:
    picture = generate_tikz_picture(spec)
    return "\n".join(
        [
            r"\documentclass[tikz,border=8pt]{standalone}",
            r"\usepackage{amsmath,amssymb}",
            r"\usetikzlibrary{arrows.meta,automata,positioning,quotes}",
            r"\begin{document}",
            picture,
            r"\end{document}",
            "",
        ]
    )


def generate_tikz_picture(spec: GraphSpec) -> str:
    positions = assign_positions(spec)
    node_names = _node_name_map(spec.node_ids())
    lines = [
        r"\begin{tikzpicture}[x=1cm,y=1cm]",
        r"\tikzset{",
        r"  graphnode/.style={circle,draw=black,line width=0.55pt,minimum size=8mm,inner sep=1pt,font=\sffamily},",
        r"  graphedge/.style={draw=black,line width=0.55pt,line cap=round,line join=round},",
        r"  directed/.style={graphedge,-{Stealth[length=2.4mm,width=2.0mm]}},",
        r"  undirected/.style={graphedge}",
        r"}",
    ]

    for node in spec.nodes:
        x, y = positions.get(node.id, (0.0, 0.0))
        node_options = _node_options(spec, node.id)
        style = f"graphnode,{node_options}" if node_options else "graphnode"
        lines.append(rf"\node[{style}] ({node_names[node.id]}) at ({x:.2f},{y:.2f}) {{{latex_label(node.display_label)}}};")

    for edge in spec.edges:
        style = "directed" if edge.directed else "undirected"
        label = ""
        if edge.label:
            label = rf" node[midway,fill=white,inner sep=1pt] {{{latex_escape(edge.label)}}}"
        path_option = _edge_path_option(spec, edge)
        route = _edge_route(spec, edge)
        if edge.source == edge.target:
            loop_option = path_option or "loop above"
            lines.append(
                rf"\draw[{style}] ({node_names[edge.source]}) to[{loop_option}]{label} ({node_names[edge.target]});"
            )
        elif route:
            waypoints = "".join(rf" -- ({x:.2f},{y:.2f})" for x, y in route)
            lines.append(
                rf"\draw[{style},rounded corners=5pt] ({node_names[edge.source]}){waypoints} --{label} ({node_names[edge.target]});"
            )
        elif path_option:
            lines.append(
                rf"\draw[{style}] ({node_names[edge.source]}) to[{path_option}]{label} ({node_names[edge.target]});"
            )
        else:
            lines.append(rf"\draw[{style}] ({node_names[edge.source]}) --{label} ({node_names[edge.target]});")

    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines)


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in str(value))


def latex_label(value: str) -> str:
    text = str(value)
    if text.startswith("$") and text.endswith("$"):
        return text
    return latex_escape(text)


def _node_name_map(node_ids: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for node_id in node_ids:
        base = re.sub(r"[^A-Za-z0-9_]", "_", node_id)
        if not base or base[0].isdigit():
            base = f"n_{base}"
        base = f"n_{base}"
        candidate = base
        counter = 2
        while candidate in used:
            candidate = f"{base}_{counter}"
            counter += 1
        used.add(candidate)
        result[node_id] = candidate
    return result


def _edge_path_option(spec: GraphSpec, edge) -> str:
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


def _edge_route(spec: GraphSpec, edge) -> list[tuple[float, float]]:
    routes = spec.layout_hints.get("edge_routes", {})
    if not isinstance(routes, dict):
        return []
    if edge.directed:
        key = f"directed:{edge.source}:{edge.target}"
    else:
        left, right = sorted((edge.source, edge.target))
        key = f"undirected:{left}:{right}"
    value = routes.get(key, [])
    if not isinstance(value, list):
        return []
    route: list[tuple[float, float]] = []
    for point in value:
        if isinstance(point, (list, tuple)) and len(point) == 2:
            route.append((float(point[0]), float(point[1])))
    return route


def _node_options(spec: GraphSpec, node_id: str) -> str:
    options = spec.layout_hints.get("node_options", {})
    if not isinstance(options, dict):
        return ""
    value = options.get(node_id, "")
    return str(value) if value else ""
