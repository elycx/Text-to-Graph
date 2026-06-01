"""Benchmark evaluation for the Text-to-Graph workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import LLMClientConfig
from .validator import compare_graphs
from .workflow import TextToGraphWorkflow


def load_benchmark(path: str | Path) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_benchmark(
    benchmark_path: str | Path,
    output_dir: str | Path | None = None,
    render: bool = True,
    llm_config: LLMClientConfig | None = None,
) -> dict[str, Any]:
    cases = load_benchmark(benchmark_path)
    workflow = TextToGraphWorkflow(llm_config=llm_config)
    results: list[dict[str, Any]] = []

    for case in cases:
        case_id = case["id"]
        case_output = Path(output_dir) / case_id if output_dir else None
        workflow_result = workflow.run(
            case["description"],
            output_dir=case_output,
            job_name=case_id,
            render=render,
            params=case.get("params"),
        )
        comparison = compare_graphs(workflow_result.spec, case["expected"])
        results.append(
            {
                "id": case_id,
                "description": case["description"],
                "success": workflow_result.success,
                "compile_success": bool(workflow_result.render_result and workflow_result.render_result.success)
                if render
                else None,
                "warnings": workflow_result.spec.warnings,
                "comparison": comparison,
            }
        )

    summary = _summarize(results, render)
    payload = {"summary": summary, "cases": results}
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(output_dir, "evaluation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _summarize(results: list[dict[str, Any]], render: bool) -> dict[str, float]:
    n = len(results)
    if n == 0:
        return {}
    return {
        "node_accuracy": _avg(results, "node_accuracy"),
        "edge_accuracy": _avg(results, "edge_accuracy"),
        "direction_accuracy": _avg(results, "direction_accuracy"),
        "node_exact_rate": sum(1 for item in results if item["comparison"]["node_exact"]) / n,
        "edge_exact_rate": sum(1 for item in results if item["comparison"]["edge_exact"]) / n,
        "graph_exact_rate": sum(1 for item in results if item["comparison"]["graph_exact"]) / n,
        "workflow_success_rate": sum(1 for item in results if item["success"]) / n,
        "compilation_success_rate": sum(1 for item in results if item["compile_success"]) / n if render else 0.0,
    }


def _avg(results: list[dict[str, Any]], metric: str) -> float:
    return sum(float(item["comparison"][metric]) for item in results) / len(results)
