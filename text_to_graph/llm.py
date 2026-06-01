"""GPT-5 graph extraction through the local OpenAI-compatible API."""

from __future__ import annotations

import json
import re
import sys
import base64
import io
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import requests

from .schema import Edge, GraphSpec, Node

DEFAULT_BASE_URL = "http://localhost:1455/v1"
DEFAULT_HEALTH_URL = "http://localhost:1455/health"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_API_KEY = "sk-"

GRAPH_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges", "layout_hints", "warnings"],
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "target", "directed"],
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "directed": {"type": "boolean"},
                    "label": {"type": "string"},
                },
            },
        },
        "layout_hints": {
            "type": "object",
            "additionalProperties": True,
            "required": ["style", "positions"],
            "properties": {
                "style": {"type": "string"},
                "positions": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                },
                "edge_options": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "edge_routes": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "node_options": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """You are the graph-understanding module for a Text-to-Graph system.

Your job is to convert the user's graph description into one finite graph JSON object.
The downstream code will validate the JSON and generate TikZ; do not generate TikZ.

Output rules:
- Return only JSON. No markdown, no commentary, no code fences.
- The JSON must match this schema:
  {
    "nodes": [{"id": "A", "label": "A"}],
    "edges": [{"source": "A", "target": "B", "directed": false}],
      "layout_hints": {
        "style": "explicit",
        "positions": {"A": [0, 0], "B": [2, 0]},
        "edge_options": {},
        "edge_routes": {},
        "node_options": {}
      },
    "warnings": []
  }
- Every edge endpoint must exactly match a node id.
- Use stable ASCII ids: A, B, u_0, v_3, w_2, p, q.
- Labels may be plain text or LaTeX math, for example "$u_{0}$".
- If the input describes a graph family with parameters, instantiate it using the params JSON.
- If a required integer parameter is missing, use m=4 and add a warning.
- For named graph classes, use the standard graph-theoretic meaning. If a name has multiple common meanings, choose the most relevant one and add a warning.
- For undirected set notation like {{a,b}}, set directed=false.
- For arrows, "points to", "from ... to", or directed edges, set directed=true.
- Always provide readable coordinates in layout_hints.positions.
- Use layout_hints.node_options only for semantic node styling when helpful, for example "double" for accepting automaton states.
- Node and edge options must use standard TikZ keys available from arrows.meta, positioning, quotes, and automata libraries. Do not use style keys that change edge color, opacity, or line width; the renderer keeps all graph edges visually uniform.
- Layout quality matters. Avoid placing an edge so that it passes through an unrelated node.
- Do not over-route an edge. If a straight segment or a small bend is clear of nodes and labels, use that instead of a large loop or long waypoint corridor.
- Do not let two unrelated edges run close together for a long distance. If two routed edges share a corridor, separate them or simplify one of them.
- Put hubs outside the main node rows or columns instead of collinear with the nodes they connect to.
- For layered or indexed graph families, align layers cleanly and route long hub edges around the outside.
- For dense artificial graphs, increase spacing generously instead of compressing the layout.
- Prefer balanced, orderly layouts: align nodes that naturally form rows or columns, keep repeated modules equally spaced, and use approximate mirror or rotational symmetry when the graph structure suggests it.
- Keep routed edges orderly too: prefer short horizontal, vertical, diagonal, or gently curved corridors over irregular zigzags.
- If a small similarly named local group can remove crossings by moving inward, outward, upward, or downward, move the nodes instead of adding large bends.
- For ring, wheel, or circular gadget layouts, put local satellite nodes radially outward from their anchor and route long skip edges on a larger outer radius. Do not let long skip edges cut through local node labels.
- For long skip edges in ring-like layouts, prefer edge_routes waypoint corridors over central chords or huge arcs.
- For difficult long edges, use layout_hints.edge_routes. Keys are the same as edge_options keys; values are arrays of intermediate waypoint coordinates, for example "directed:a:b": [[8, 8], [8, -8]]. The renderer draws these as rounded-corner polylines.
- Use at most 4 waypoints per routed edge. Put waypoints in open corridors outside node clusters. Keep routes compact; do not draw giant decorative arcs.
- Use edge_routes only when a straight or small-bend edge would be visually worse. If the direct route has clear space, leave the edge straight or use a mild bend.
- Edge crossings are acceptable when mathematically unavoidable, but node-edge overlaps and label-edge overlaps are not acceptable.
- Use edge_options for mild routing only. Keys are "undirected:min_id:max_id" or "directed:source:target"; values are TikZ to[...] options such as "bend left=20". Avoid bend angles above 45 and avoid looseness above 1.3 unless absolutely necessary.
- If a symbol such as T is referenced but not explicitly defined, make the most mathematically reasonable interpretation and add a warning.
"""

VISUAL_REVIEW_SYSTEM_PROMPT = """You are the visual critic in a Text-to-Graph feedback loop.

You inspect one rendered graph figure and decide whether another layout iteration is needed.
Return only JSON. No markdown, no commentary, no code fences.

Schema:
{
  "acceptable": true,
  "score": 8.0,
  "issues": [],
  "suggestions": []
}

Be strict about:
- any edge that touches, crosses through, or crowds a non-incident node or label;
- nodes or labels that are too close to each other;
- labels covered by edges;
- avoidable tangles that make graph semantics hard to read;
- satellite/local nodes placed in a way that obscures their anchor gadget.
- edges that take a very long detour even though a direct or small-bend route would be clear;
- two unrelated edges that run close together for a long visible segment.
- visibly chaotic placement when the graph has repeated modules, symmetric substructures, layers, rows, columns, or ring-like order that could be drawn more evenly.

Ordinary edge crossings are acceptable only when the graph remains clearly readable and the crossings do not occur near nodes or labels.
If a figure needs another iteration, set acceptable=false and give concrete, local suggestions such as aligning a named row/column, spacing a repeated group evenly, moving a named pair/group inward or outward, or rerouting specific long edges.
"""

MACRO_RELAYOUT_SYSTEM_PROMPT = """You are the macro-relayout module in a Text-to-Graph feedback loop.

You receive the original graph description, the previous graph JSON, concrete visual-review complaints, and usually the previous rendered image.
Return only a complete corrected graph JSON object. No markdown, no commentary, no code fences.

Hard requirements:
- Preserve the graph semantics: keep the same intended nodes and edges unless the issue explicitly says the structure is wrong.
- Treat visual-review rejection as a layout failure, not a small styling problem.
- Re-plan coordinates globally. Move node groups, widen clusters, separate satellites from anchors, and route long edges through open space.
- Do not merely tweak edge_options. Node coordinates must change substantially when the previous image was rejected visually.
- Prefer the simplest readable route for each edge: straight first, then a small bend, then a compact edge_routes corridor only when necessary.
- If two routed edges run side by side for a long distance, separate their waypoint corridors or simplify one of the routes.
- For small similarly named bridge or connector groups, consider moving the whole local group before adding curvature: squeeze inward to clear external corridors, or move outward to separate crossing diagonals from nearby edges.
- Improve global order whenever possible: align natural rows/columns, preserve equal spacing inside repeated modules, center paired substructures around a shared axis, and make ring-like layouts evenly spaced around their center.
- Keep waypoint corridors visually organized. Prefer shared horizontal/vertical levels only when they do not create long near-overlaps; otherwise use separated parallel levels with consistent spacing.
- Prefer larger, cleaner drawings over compact drawings. Empty whitespace is acceptable.
- Keep coordinates practical for TikZ: use a canvas roughly within x,y = -14..14, never beyond -18..18.
- Typical adjacent spacing should be 1.6 to 4.0 coordinate units. Do not use 20+ unit gaps.
- Keep edge looseness moderate, usually 0.8 to 1.3. Avoid huge loops made only by looseness.
- Prefer edge_routes for long external corridors instead of high-curvature bends. edge_routes values are waypoint arrays, such as [[8, 8], [8, -8]], and the renderer draws rounded-corner polylines.
- Do not use edge_routes for an edge whose straight or lightly curved route is already clear. Over-routing is a visual failure.
- Keep bends visually modest. Use bend angles around 15..40 for normal edges; use edge_routes instead of bend angles above 50.
- Use node_options sparingly. Do not set large fonts such as \\Large; rely on the default graphnode style unless a special shape is truly needed.
- For multi-module graphs, separate modules into clearly distinct regions and route inter-module edges through corridors between modules.
- For hub-and-grid graphs, move the hub outside the grid with enough clearance and fan hub edges around the outside when possible.
- For rectangular grids with hub edges, preserve the grid as a rectangular scaffold. Put the hub on one side or above/below with a clear external corridor. Hub edges to interior grid nodes should be routed around the grid perimeter and enter from the nearest side; do not send a fan of hub edges through the grid interior.
- For teleport edges on grids, route teleports outside the rectangle using distinct top, bottom, left, or right corridors. Keep teleport arcs separated from corner and side nodes.
- For ring-like graphs with repeated local gadgets, use concentric or radial levels so anchors, satellites, and outer routes do not crowd each other.
- For composite graphs that combine a dense base gadget with a sparse attached structure, put the dense and sparse parts in distinct zones with a wide corridor between them.
- Edge crossings may remain, but keep crossings away from nodes and labels.

The output schema is the same graph JSON schema used by the extractor:
{
  "nodes": [{"id": "A", "label": "A"}],
  "edges": [{"source": "A", "target": "B", "directed": false}],
  "layout_hints": {
    "style": "explicit",
    "positions": {"A": [0, 0], "B": [3, 0]},
    "edge_options": {},
    "edge_routes": {},
    "node_options": {}
  },
  "warnings": []
}
"""


@dataclass(frozen=True)
class LLMClientConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = DEFAULT_API_KEY
    model: str = DEFAULT_MODEL
    health_url: str = DEFAULT_HEALTH_URL
    timeout: int = 300


class LLMGraphError(RuntimeError):
    """Raised when GPT-5 cannot produce a valid graph structure."""


@dataclass(frozen=True)
class VisualReview:
    acceptable: bool
    score: float | None = None
    issues: list[str] = None  # type: ignore[assignment]
    suggestions: list[str] = None  # type: ignore[assignment]
    raw_response: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", list(self.issues or []))
        object.__setattr__(self, "suggestions", list(self.suggestions or []))

    @property
    def ok(self) -> bool:
        return self.acceptable

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptable": self.acceptable,
            "score": self.score,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "raw_response": self.raw_response,
        }


def check_health(config: LLMClientConfig | None = None) -> Mapping[str, Any]:
    config = config or LLMClientConfig()
    response = requests.get(config.health_url, timeout=10)
    response.raise_for_status()
    return response.json()


def list_models(config: LLMClientConfig | None = None) -> list[str]:
    client = _make_client(config or LLMClientConfig())
    return [str(model.id) for model in client.models.list()]


def extract_graph_with_gpt5(
    text: str,
    params: Mapping[str, int] | None = None,
    config: LLMClientConfig | None = None,
) -> GraphSpec:
    config = config or LLMClientConfig()
    client = _make_client(config)
    prompt = _build_user_prompt(text, params or {})

    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
    except Exception as exc:  # OpenAI-compatible local servers raise SDK-specific errors.
        raise LLMGraphError(f"GPT-5 request failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise LLMGraphError("GPT-5 returned an empty response.")

    payload = _parse_json_object(content)
    spec = graph_spec_from_payload(payload)
    spec.source_text = text
    spec.warnings.insert(0, f"Graph extracted by {config.model} at {config.base_url}.")
    return spec


def refine_graph_with_gpt5(
    text: str,
    previous_spec: GraphSpec,
    issues: list[str],
    params: Mapping[str, int] | None = None,
    config: LLMClientConfig | None = None,
) -> GraphSpec:
    config = config or LLMClientConfig()
    client = _make_client(config)
    prompt = _build_refinement_prompt(text, previous_spec, issues, params or {})

    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
    except Exception as exc:
        raise LLMGraphError(f"GPT-5 refinement request failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise LLMGraphError("GPT-5 returned an empty refinement response.")

    payload = _parse_json_object(content)
    spec = graph_spec_from_payload(payload)
    spec.source_text = text
    spec.warnings.insert(0, f"Graph refined by {config.model} at {config.base_url}.")
    return spec


def relayout_graph_with_gpt5(
    text: str,
    previous_spec: GraphSpec,
    issues: list[str],
    image_path: str | Path | None = None,
    layout_metrics: Mapping[str, Any] | None = None,
    params: Mapping[str, int] | None = None,
    config: LLMClientConfig | None = None,
) -> GraphSpec:
    config = config or LLMClientConfig()
    client = _make_client(config)
    prompt = _build_macro_relayout_prompt(text, previous_spec, issues, layout_metrics or {}, params or {})
    content: Any = prompt
    if image_path:
        mime_type, encoded_image = _prepare_review_image(Path(image_path))
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"}},
        ]

    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": MACRO_RELAYOUT_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0,
        )
    except Exception as exc:
        raise LLMGraphError(f"GPT-5 macro relayout request failed: {exc}") from exc

    response_content = response.choices[0].message.content
    if not response_content:
        raise LLMGraphError("GPT-5 returned an empty macro relayout response.")

    payload = _parse_json_object(response_content)
    spec = graph_spec_from_payload(payload)
    spec.source_text = text
    spec.warnings.insert(0, f"Graph macro-relayout by {config.model} at {config.base_url}.")
    return spec


def review_graph_image_with_gpt5(
    text: str,
    spec: GraphSpec,
    image_path: str | Path,
    layout_metrics: Mapping[str, Any] | None = None,
    config: LLMClientConfig | None = None,
) -> VisualReview:
    config = config or LLMClientConfig()
    client = _make_client(config)
    mime_type, encoded_image = _prepare_review_image(Path(image_path))
    prompt = _build_visual_review_prompt(text, spec, layout_metrics or {})

    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": VISUAL_REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"},
                        },
                    ],
                },
            ],
            temperature=0,
        )
    except Exception as exc:
        raise LLMGraphError(f"GPT-5 visual review request failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise LLMGraphError("GPT-5 returned an empty visual review response.")

    payload = _parse_json_object(content)
    return visual_review_from_payload(payload, content)


def visual_review_from_payload(payload: Mapping[str, Any], raw_response: str = "") -> VisualReview:
    issues = payload.get("issues", [])
    suggestions = payload.get("suggestions", [])
    score = payload.get("score")
    try:
        normalized_score = float(score) if score is not None else None
    except (TypeError, ValueError):
        normalized_score = None
    return VisualReview(
        acceptable=bool(payload.get("acceptable", False)),
        score=normalized_score,
        issues=[str(issue) for issue in issues] if isinstance(issues, list) else [str(issues)],
        suggestions=[str(item) for item in suggestions] if isinstance(suggestions, list) else [str(suggestions)],
        raw_response=raw_response,
    )


def graph_spec_from_payload(payload: Mapping[str, Any]) -> GraphSpec:
    _validate_payload_shape(payload)
    nodes = [Node(id=str(node["id"]), label=str(node.get("label") or node["id"])) for node in payload["nodes"]]
    edges = [
        Edge(
            source=str(edge["source"]),
            target=str(edge["target"]),
            directed=bool(edge["directed"]),
            label=str(edge["label"]) if edge.get("label") else None,
        )
        for edge in payload["edges"]
    ]
    layout_hints = dict(payload.get("layout_hints", {}))
    layout_hints.setdefault("style", "explicit")
    positions = _normalize_positions(layout_hints.get("positions", {}))
    edge_routes = _normalize_edge_routes(layout_hints.get("edge_routes", {}))
    positions, edge_routes = _fit_graph_geometry_to_canvas(positions, edge_routes)
    layout_hints["positions"] = positions
    layout_hints["edge_routes"] = edge_routes
    layout_hints["edge_options"] = {
        str(key): _normalize_edge_option(str(value))
        for key, value in dict(layout_hints.get("edge_options", {})).items()
    }
    layout_hints["node_options"] = {
        str(key): _normalize_node_option(str(value))
        for key, value in dict(layout_hints.get("node_options", {})).items()
    }
    warnings = [str(warning) for warning in payload.get("warnings", [])]
    return GraphSpec(nodes=nodes, edges=edges, layout_hints=layout_hints, warnings=warnings)


def _make_client(config: LLMClientConfig):
    _add_local_dependency_path()
    try:
        from openai import OpenAI
    except ImportError as exc:
        return _RequestsOpenAIClient(config)
    return OpenAI(base_url=config.base_url, api_key=config.api_key)


class _RequestsOpenAIClient:
    def __init__(self, config: LLMClientConfig) -> None:
        self.models = _RequestsModels(config)
        self.chat = SimpleNamespace(completions=_RequestsChatCompletions(config))


class _RequestsModels:
    def __init__(self, config: LLMClientConfig) -> None:
        self.config = config

    def list(self) -> list[Any]:
        response = requests.get(
            f"{self.config.base_url.rstrip('/')}/models",
            headers=_request_headers(self.config),
            timeout=self.config.timeout,
        )
        _raise_for_status(response)
        payload = response.json()
        raw_models = payload.get("data", payload if isinstance(payload, list) else [])
        return [
            SimpleNamespace(id=str(item.get("id", item))) if isinstance(item, dict) else SimpleNamespace(id=str(item))
            for item in raw_models
        ]


class _RequestsChatCompletions:
    def __init__(self, config: LLMClientConfig) -> None:
        self.config = config

    def create(self, **payload: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers=_request_headers(self.config),
                    json=payload,
                    timeout=self.config.timeout,
                )
                _raise_for_status(response)
                break
            except (requests.RequestException, LLMGraphError) as exc:
                last_error = exc
                if attempt == 2 or not _is_transient_api_error(exc):
                    raise
                time.sleep(2.0 * (attempt + 1))
        else:
            raise LLMGraphError(f"GPT-5 API request failed after retries: {last_error}")
        data = response.json()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=_message_content(choice.get("message", {}).get("content", "")))
                )
                for choice in data.get("choices", [])
            ]
        )


def _request_headers(config: LLMClientConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


def _message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def _raise_for_status(response: requests.Response) -> None:
    if response.ok:
        return
    body = response.text.strip()
    if len(body) > 1200:
        body = body[:1200] + "..."
    raise LLMGraphError(f"GPT-5 API HTTP {response.status_code}: {body}")


def _is_transient_api_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    message = str(exc).lower()
    transient_terms = (
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "fetch failed",
        "upstream",
        "connection termination",
        "timed out",
        "temporarily",
        "无法连接",
    )
    return any(term in message for term in transient_terms)


def _add_local_dependency_path() -> None:
    root = Path(__file__).resolve().parent.parent
    for dependency_path in (root / ".vendor", root / ".deps"):
        if dependency_path.exists():
            deps = str(dependency_path)
            if deps not in sys.path:
                sys.path.insert(0, deps)


def _build_user_prompt(text: str, params: Mapping[str, int]) -> str:
    return "\n".join(
        [
            "params:",
            json.dumps(dict(params), ensure_ascii=False, sort_keys=True),
            "",
            "graph description:",
            text,
        ]
    )


def _build_refinement_prompt(
    text: str,
    previous_spec: GraphSpec,
    issues: list[str],
    params: Mapping[str, int],
) -> str:
    return "\n".join(
        [
            "The previous graph JSON failed validation or layout quality checks.",
            "Return a complete corrected JSON object. Preserve the intended graph semantics.",
            "If the issue is visual quality, first move node coordinates to create more space, then adjust edge_options.",
            "Preserve all intended nodes and edges unless the issue explicitly says a graph element is wrong.",
            "",
            "params:",
            json.dumps(dict(params), ensure_ascii=False, sort_keys=True),
            "",
            "original graph description:",
            text,
            "",
            "previous graph JSON:",
            json.dumps(previous_spec.to_dict(), ensure_ascii=False, indent=2),
            "",
            "issues to fix:",
            json.dumps(issues, ensure_ascii=False, indent=2),
        ]
    )


def _build_macro_relayout_prompt(
    text: str,
    previous_spec: GraphSpec,
    issues: list[str],
    layout_metrics: Mapping[str, Any],
    params: Mapping[str, int],
) -> str:
    return "\n".join(
        [
            "The previous rendered graph image was rejected by visual review.",
            "Generate a fresh, globally re-laid-out graph JSON.",
            "Preserve the node set and edge set unless the original graph description requires a correction.",
            "Do not solve this only by changing bends. Move coordinates substantially and use a larger drawing if needed.",
            "For any named nodes in the visual issues, move those nodes or their whole local group away from the offending edge corridor.",
            "When two similarly named bridge nodes form a pair, try group moves such as horizontal inward squeezing or vertical outward shifting before adding curvature.",
            "For over-routed edges, remove unnecessary waypoints and use a straight or small-bend route when node clearance allows it.",
            "For edge pairs that run too close for a long segment, assign separate compact corridors or simplify one of the two routes.",
            "Improve symmetry and order: align natural rows/columns, even out repeated groups, and use consistent waypoint levels when that does not create overlaps.",
            "Use explicit coordinates for every node.",
            "",
            "params:",
            json.dumps(dict(params), ensure_ascii=False, sort_keys=True),
            "",
            "original graph description:",
            text,
            "",
            "previous graph JSON:",
            json.dumps(previous_spec.to_dict(), ensure_ascii=False, indent=2),
            "",
            "computed geometry metrics:",
            json.dumps(dict(layout_metrics), ensure_ascii=False, indent=2),
            "",
            "visual and validation issues to fix:",
            json.dumps(issues, ensure_ascii=False, indent=2),
            "",
            "Return the complete corrected graph JSON now.",
        ]
    )


def _build_visual_review_prompt(
    text: str,
    spec: GraphSpec,
    layout_metrics: Mapping[str, Any],
) -> str:
    return "\n".join(
        [
            "Inspect the attached rendered graph figure.",
            "Decide whether the layout is visually acceptable or another iteration should modify node coordinates and edge routing.",
            "Focus on the actual image, not only the JSON.",
            "Set acceptable=false if any edge appears to overlap or pass too close to an unrelated node or label.",
            "",
            "original graph description:",
            text,
            "",
            "current graph JSON:",
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=2),
            "",
            "computed geometry metrics:",
            json.dumps(dict(layout_metrics), ensure_ascii=False, indent=2),
        ]
    )


def _prepare_review_image(image_path: Path) -> tuple[str, str]:
    max_payload_bytes = 180_000
    try:
        from PIL import Image
    except ImportError:
        data = image_path.read_bytes()
        if len(data) > max_payload_bytes:
            raise LLMGraphError("Rendered image is too large for visual review and Pillow is not installed.")
        return ("image/png", base64.b64encode(data).decode("ascii"))

    original = Image.open(image_path).convert("RGB")
    last_data = b""
    for max_side, quality in ((900, 70), (760, 64), (640, 58), (520, 52)):
        image = original.copy()
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        last_data = buffer.getvalue()
        if len(last_data) <= max_payload_bytes:
            return ("image/jpeg", base64.b64encode(last_data).decode("ascii"))

    if len(last_data) > max_payload_bytes:
        raise LLMGraphError("Rendered image remains too large for visual review after compression.")
    return ("image/jpeg", base64.b64encode(last_data).decode("ascii"))


def _parse_json_object(content: str) -> Mapping[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise LLMGraphError(f"GPT-5 did not return JSON: {content}")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMGraphError(f"GPT-5 returned invalid JSON: {content}") from exc
    if not isinstance(payload, dict):
        raise LLMGraphError("GPT-5 JSON response must be an object.")
    return payload


def _validate_payload_shape(payload: Mapping[str, Any]) -> None:
    for key in ("nodes", "edges", "layout_hints", "warnings"):
        if key not in payload:
            raise LLMGraphError(f"GPT-5 JSON is missing required key: {key}")
    if not isinstance(payload["nodes"], list):
        raise LLMGraphError("GPT-5 JSON field nodes must be a list.")
    if not isinstance(payload["edges"], list):
        raise LLMGraphError("GPT-5 JSON field edges must be a list.")
    if not isinstance(payload["layout_hints"], dict):
        raise LLMGraphError("GPT-5 JSON field layout_hints must be an object.")


def _normalize_positions(raw_positions: Any) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    if not isinstance(raw_positions, dict):
        return positions
    for node_id, raw_position in raw_positions.items():
        if not isinstance(raw_position, (list, tuple)) or len(raw_position) != 2:
            continue
        positions[str(node_id)] = (float(raw_position[0]), float(raw_position[1]))
    return positions


def _normalize_edge_routes(raw_routes: Any) -> dict[str, list[tuple[float, float]]]:
    routes: dict[str, list[tuple[float, float]]] = {}
    if not isinstance(raw_routes, dict):
        return routes
    for key, raw_route in raw_routes.items():
        waypoints = raw_route.get("waypoints", raw_route) if isinstance(raw_route, dict) else raw_route
        if not isinstance(waypoints, list):
            continue
        parsed: list[tuple[float, float]] = []
        for point in waypoints[:4]:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            try:
                parsed.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
        if parsed:
            routes[str(key)] = parsed
    return routes


def _normalize_tikz_option(option: str) -> str:
    option = re.sub(r'"\$([^"$]+)"', r'"$\1$"', option)
    option = re.sub(r"font\s*=\s*\\(?:Large|LARGE|huge|Huge)", "", option)
    option = re.sub(r"looseness\s*=\s*(\d+(?:\.\d+)?)", _cap_looseness, option)
    option = re.sub(r"bend\s+(left|right)\s*=\s*(-?\d+(?:\.\d+)?)", _cap_bend, option, flags=re.IGNORECASE)
    option = _separate_parallel_out_in(option)
    option = re.sub(r",\s*,+", ",", option).strip(" ,")
    return option


def _normalize_edge_option(option: str) -> str:
    option = _normalize_tikz_option(option)
    option = _strip_visual_edge_style(option)
    return re.sub(r",\s*,+", ",", option).strip(" ,")


def _normalize_node_option(option: str) -> str:
    option = _normalize_tikz_option(option)
    option = _strip_line_style(option)
    return re.sub(r",\s*,+", ",", option).strip(" ,")


def _strip_visual_edge_style(option: str) -> str:
    option = _strip_line_style(option)
    option = re.sub(r"(?:^|,)\s*(?:draw|color)\s*=\s*[^,]+", "", option, flags=re.IGNORECASE)
    option = re.sub(r"(?:^|,)\s*(?:black|gray|grey|red|blue|green|orange|purple|brown|cyan|magenta)\b", "", option, flags=re.IGNORECASE)
    option = re.sub(r"(?:^|,)\s*(?:dashed|densely dashed|loosely dashed|dotted|densely dotted|loosely dotted)\b", "", option, flags=re.IGNORECASE)
    option = re.sub(r"(?:^|,)\s*opacity\s*=\s*[^,]+", "", option, flags=re.IGNORECASE)
    return option


def _strip_line_style(option: str) -> str:
    option = re.sub(r"(?:^|,)\s*(?:ultra thin|very thin|thin|semithick|thick|very thick|ultra thick)\b", "", option, flags=re.IGNORECASE)
    option = re.sub(r"(?:^|,)\s*line\s+width\s*=\s*[^,]+", "", option, flags=re.IGNORECASE)
    return option


def _fit_graph_geometry_to_canvas(
    positions: dict[str, tuple[float, float]],
    edge_routes: dict[str, list[tuple[float, float]]],
) -> tuple[dict[str, tuple[float, float]], dict[str, list[tuple[float, float]]]]:
    if not positions:
        return positions, edge_routes
    all_points = list(positions.values()) + [point for route in edge_routes.values() for point in route]
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    max_abs = max(max(abs(x) for x in xs), max(abs(y) for y in ys))
    if span <= 24.0 and max_abs <= 18.0:
        return positions, edge_routes
    scale = min(1.0, 18.0 / span, 16.0 / max_abs if max_abs else 1.0)
    fitted_positions = {
        node_id: ((x - center_x) * scale, (y - center_y) * scale)
        for node_id, (x, y) in positions.items()
    }
    fitted_routes = {
        key: [((x - center_x) * scale, (y - center_y) * scale) for x, y in route]
        for key, route in edge_routes.items()
    }
    return fitted_positions, fitted_routes


def _cap_looseness(match: re.Match[str]) -> str:
    value = min(float(match.group(1)), 1.3)
    return f"looseness={value:g}"


def _cap_bend(match: re.Match[str]) -> str:
    direction = match.group(1).lower()
    value = float(match.group(2))
    capped = max(-45.0, min(45.0, value))
    return f"bend {direction}={capped:g}"


def _separate_parallel_out_in(option: str) -> str:
    out_match = re.search(r"out\s*=\s*(-?\d+(?:\.\d+)?)", option)
    in_match = re.search(r"in\s*=\s*(-?\d+(?:\.\d+)?)", option)
    if not out_match or not in_match:
        return option
    out_angle = float(out_match.group(1)) % 360
    in_angle = float(in_match.group(1)) % 360
    difference = abs((out_angle - in_angle + 180) % 360 - 180)
    if difference >= 25:
        return option
    safer_in = (out_angle + 180) % 360
    return re.sub(r"in\s*=\s*-?\d+(?:\.\d+)?", f"in={safer_in:g}", option, count=1)
