import unittest

from text_to_graph.llm import graph_spec_from_payload
from text_to_graph.layout_adjust import nudge_node_positions
from text_to_graph.preprocess import preprocess_text
from text_to_graph.tikz import generate_latex_document
from text_to_graph.validator import layout_quality_metrics, validate_layout_quality


class LLMPayloadTests(unittest.TestCase):
    def test_preprocess_keeps_semantics_generic(self):
        self.assertEqual(preprocess_text("A\u2192B\n\n\nC"), "A -> B\n\nC")

    def test_payload_to_graph_spec(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "u_0", "label": "$u_{0}$"},
                    {"id": "p", "label": "$p$"},
                ],
                "edges": [{"source": "u_0", "target": "p", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"u_0": [0, 0], "p": [0, 2]},
                    "edge_options": {},
                },
                "warnings": [],
            }
        )
        self.assertEqual(spec.node_ids(), ["u_0", "p"])
        self.assertEqual(spec.edges[0].key(), ("undirected", "p", "u_0"))
        self.assertEqual(spec.layout_hints["positions"]["p"], (0.0, 2.0))

    def test_tikz_accepts_llm_math_labels(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "v_1", "label": "$v_{1}$"},
                    {"id": "q", "label": "$q$"},
                ],
                "edges": [{"source": "v_1", "target": "q", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"v_1": [-1, 0], "q": [1, 0]},
                    "edge_options": {},
                },
                "warnings": [],
            }
        )
        code = generate_latex_document(spec)
        self.assertIn("$v_{1}$", code)
        self.assertIn("$q$", code)

    def test_layout_quality_catches_edge_through_node(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                    {"id": "C", "label": "C"},
                ],
                "edges": [{"source": "A", "target": "C", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [1.5, 0], "C": [3, 0]},
                    "edge_options": {},
                },
                "warnings": [],
            }
        )
        report = validate_layout_quality(spec)
        self.assertFalse(report.ok)
        self.assertIn("passes too close", report.errors[0])

    def test_self_loop_is_rendered(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [{"id": "q0", "label": "$q_0$"}],
                "edges": [{"source": "q0", "target": "q0", "directed": True, "label": "0"}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"q0": [0, 0]},
                    "edge_options": {},
                    "node_options": {"q0": "double"},
                },
                "warnings": [],
            }
        )
        code = generate_latex_document(spec)
        self.assertIn("loop above", code)
        self.assertIn("{0}", code)
        self.assertIn("graphnode,double", code)

    def test_edge_option_math_quote_is_normalized(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "q0", "label": "$q_0$"},
                    {"id": "q1", "label": "$q_1$"},
                ],
                "edges": [{"source": "q0", "target": "q1", "directed": True}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"q0": [0, 0], "q1": [2, 0]},
                    "edge_options": {"directed:q0:q1": "thin, draw=gray, bend left=15, \"$1\""},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        self.assertEqual(spec.layout_hints["edge_options"]["directed:q0:q1"], 'bend left=15, "$1$"')

    def test_edge_route_is_rendered_and_validated(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                    {"id": "C", "label": "C"},
                ],
                "edges": [{"source": "A", "target": "C", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [1.5, 0], "C": [3, 0]},
                    "edge_options": {},
                    "edge_routes": {"undirected:A:C": [[0, 1.5], [3, 1.5]]},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        report = validate_layout_quality(spec)
        self.assertTrue(report.ok)
        code = generate_latex_document(spec)
        self.assertIn("rounded corners=5pt", code)
        self.assertIn("-- (0.00,1.50) -- (3.00,1.50)", code)

    def test_layout_quality_catches_unnecessary_detour(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                ],
                "edges": [{"source": "A", "target": "B", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [2, 0]},
                    "edge_options": {},
                    "edge_routes": {"undirected:A:B": [[0, 4], [2, 4]]},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        report = validate_layout_quality(spec)
        self.assertFalse(report.ok)
        self.assertTrue(any("over-routed" in error for error in report.errors))

    def test_layout_quality_catches_long_parallel_edges(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                    {"id": "C", "label": "C"},
                    {"id": "D", "label": "D"},
                ],
                "edges": [
                    {"source": "A", "target": "B", "directed": False},
                    {"source": "C", "target": "D", "directed": False},
                ],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [4, 0], "C": [0, 0.25], "D": [4, 0.25]},
                    "edge_options": {},
                    "edge_routes": {},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        report = validate_layout_quality(spec)
        self.assertFalse(report.ok)
        self.assertTrue(any("run too close together" in error for error in report.errors))

    def test_nudging_moves_node_away_from_edge(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                    {"id": "C", "label": "C"},
                ],
                "edges": [{"source": "A", "target": "C", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [1.5, 0], "C": [3, 0]},
                    "edge_options": {},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        nudged = nudge_node_positions(spec)
        self.assertNotEqual(nudged.layout_hints["positions"]["B"][1], 0)

    def test_nudging_reduces_avoidable_edge_crossings(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "P0", "label": "$p_0$"},
                    {"id": "P1", "label": "$p_1$"},
                    {"id": "B", "label": "B"},
                    {"id": "C", "label": "C"},
                    {"id": "D", "label": "D"},
                ],
                "edges": [
                    {"source": "A", "target": "P0", "directed": False},
                    {"source": "P1", "target": "B", "directed": False},
                    {"source": "C", "target": "D", "directed": False},
                ],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {
                        "A": [0, -1],
                        "P0": [1.8, 0.25],
                        "P1": [3.2, 0.25],
                        "B": [5, -1],
                        "C": [-0.4, 0],
                        "D": [5.4, 0],
                    },
                    "edge_options": {},
                    "edge_routes": {},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        before_crossings = layout_quality_metrics(spec)["edge_crossings"]
        nudged = nudge_node_positions(spec)
        after_crossings = layout_quality_metrics(nudged)["edge_crossings"]
        self.assertGreaterEqual(before_crossings, 2)
        self.assertLess(after_crossings, before_crossings)

    def test_nudging_aligns_nearly_ordered_rows(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "P0", "label": "$p_0$"},
                    {"id": "P1", "label": "$p_1$"},
                    {"id": "P2", "label": "$p_2$"},
                    {"id": "P3", "label": "$p_3$"},
                ],
                "edges": [
                    {"source": "P0", "target": "P1", "directed": False},
                    {"source": "P1", "target": "P2", "directed": False},
                    {"source": "P2", "target": "P3", "directed": False},
                ],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {
                        "P0": [0.03, 0.10],
                        "P1": [2.02, -0.08],
                        "P2": [4.08, 0.11],
                        "P3": [6.01, -0.06],
                    },
                    "edge_options": {},
                    "edge_routes": {},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        before_positions = spec.layout_hints["positions"]
        before_spread = max(y for _, y in before_positions.values()) - min(y for _, y in before_positions.values())
        nudged = nudge_node_positions(spec)
        after_positions = nudged.layout_hints["positions"]
        after_spread = max(y for _, y in after_positions.values()) - min(y for _, y in after_positions.values())
        self.assertLess(after_spread, before_spread)

    def test_nudging_snaps_route_waypoints_to_orderly_levels(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                    {"id": "C", "label": "C"},
                ],
                "edges": [{"source": "A", "target": "B", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [4, 0], "C": [2, 0]},
                    "edge_options": {},
                    "edge_routes": {"undirected:A:B": [[0.12, 1.04], [3.88, 1.07]]},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        nudged = nudge_node_positions(spec)
        route = nudged.layout_hints["edge_routes"]["undirected:A:B"]
        self.assertEqual([point[1] for point in route], [1.0, 1.0])
        self.assertLess(abs(route[0][0] - 0.12) + abs(route[1][0] - 3.88), 0.01)

    def test_nudging_removes_unnecessary_route_waypoints(self):
        spec = graph_spec_from_payload(
            {
                "nodes": [
                    {"id": "A", "label": "A"},
                    {"id": "B", "label": "B"},
                ],
                "edges": [{"source": "A", "target": "B", "directed": False}],
                "layout_hints": {
                    "style": "explicit",
                    "positions": {"A": [0, 0], "B": [4, 0]},
                    "edge_options": {},
                    "edge_routes": {"undirected:A:B": [[1.0, 0.2], [3.0, 0.2]]},
                    "node_options": {},
                },
                "warnings": [],
            }
        )
        nudged = nudge_node_positions(spec)
        self.assertEqual(nudged.layout_hints.get("edge_routes", {}).get("undirected:A:B"), [])


if __name__ == "__main__":
    unittest.main()
