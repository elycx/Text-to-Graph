"""Core data structures for the Text-to-Graph workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def clean_identifier(value: str) -> str:
    """Return a compact graph identifier while preserving the user's label."""
    return str(value).strip().strip("\"'`.,;:()[]{}")


@dataclass(frozen=True)
class Node:
    """A graph node."""

    id: str
    label: str | None = None

    @property
    def display_label(self) -> str:
        return self.label or self.id

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.display_label}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | str) -> "Node":
        if isinstance(data, str):
            node_id = clean_identifier(data)
            return cls(id=node_id, label=node_id)
        node_id = clean_identifier(data["id"])
        return cls(id=node_id, label=data.get("label") or node_id)


@dataclass(frozen=True)
class Edge:
    """A graph edge."""

    source: str
    target: str
    directed: bool = False
    label: str | None = None

    def key(self) -> tuple[str, str, str]:
        if self.directed:
            return ("directed", self.source, self.target)
        left, right = sorted((self.source, self.target))
        return ("undirected", left, right)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source": self.source,
            "target": self.target,
            "directed": self.directed,
        }
        if self.label:
            data["label"] = self.label
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Edge":
        return cls(
            source=clean_identifier(data["source"]),
            target=clean_identifier(data["target"]),
            directed=bool(data.get("directed", False)),
            label=data.get("label"),
        )


@dataclass
class GraphSpec:
    """Structured intermediate representation used by the full pipeline."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    layout_hints: dict[str, Any] = field(default_factory=lambda: {"style": "circular"})
    warnings: list[str] = field(default_factory=list)
    source_text: str = ""

    def node_ids(self) -> list[str]:
        return [node.id for node in self.nodes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "layout_hints": self.layout_hints,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphSpec":
        return cls(
            nodes=[Node.from_dict(node) for node in data.get("nodes", [])],
            edges=[Edge.from_dict(edge) for edge in data.get("edges", [])],
            layout_hints=data.get("layout_hints", {"style": "circular"}),
            warnings=list(data.get("warnings", [])),
            source_text=data.get("source_text", ""),
        )

