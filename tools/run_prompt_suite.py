from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from text_to_graph.llm import LLMClientConfig, LLMGraphError
from text_to_graph.workflow import TextToGraphWorkflow


def main() -> int:
    parser = argparse.ArgumentParser(description="Run challenging graph prompt suite and generate a report.")
    parser.add_argument("--prompts", type=Path, default=Path("examples/prompt_suite.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/prompt_suite"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports"))
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--base-url", default="http://localhost:1455/v1")
    parser.add_argument("--api-key", default="sk-")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300, help="Per GPT API request timeout in seconds.")
    parser.add_argument("--case", action="append", default=[], help="Run only the selected case id; may be repeated.")
    args = parser.parse_args()

    run_started = datetime.now()
    run_output = args.output / run_started.strftime("run_%Y%m%d_%H%M%S")
    prompts = json.loads(args.prompts.read_text(encoding="utf-8"))
    if args.case:
        selected = set(args.case)
        prompts = [case for case in prompts if case["id"] in selected]
        missing = selected - {case["id"] for case in prompts}
        if missing:
            raise SystemExit(f"Unknown case id(s): {', '.join(sorted(missing))}")
    workflow = TextToGraphWorkflow(
        max_iterations=args.max_iterations,
        llm_config=LLMClientConfig(
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=args.timeout,
        ),
    )

    run_output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for case in prompts:
        case_id = case["id"]
        case_dir = run_output / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = workflow.run(case["prompt"], output_dir=case_dir, job_name=case_id, render=True)
            result_payload: dict[str, Any] = result.to_dict()
        except LLMGraphError as exc:
            result_payload = {"success": False, "error": str(exc), "history": []}
        result_payload["id"] = case_id
        result_payload["title"] = case["title"]
        result_payload["prompt"] = case["prompt"]
        result_payload["case_dir"] = str(case_dir)
        results.append(result_payload)
        (case_dir / f"{case_id}_summary.json").write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    report_payload = {
        "created_at": run_started.isoformat(timespec="seconds"),
        "model": args.model,
        "base_url": args.base_url,
        "output_dir": str(run_output),
        "results": results,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "prompt_suite_results.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown_report(args.report_dir / "prompt_suite_report.md", report_payload, args.report_dir)
    _write_html_report(args.report_dir / "prompt_suite_report.html", report_payload, args.report_dir)
    print(json.dumps(_summary(report_payload), ensure_ascii=False, indent=2))
    return 0


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload["results"]
    return {
        "model": payload["model"],
        "total": len(results),
        "successes": sum(1 for item in results if item.get("success")),
        "failures": [item["id"] for item in results if not item.get("success")],
        "output_dir": payload["output_dir"],
        "report": "reports/prompt_suite_report.html",
    }


def _write_markdown_report(path: Path, payload: dict[str, Any], report_dir: Path) -> None:
    lines = [
        "# Text-to-Graph Prompt Suite Report",
        "",
        f"- Created: {payload['created_at']}",
        f"- Model: `{payload['model']}`",
        f"- Base URL: `{payload['base_url']}`",
        "",
        "This report records each challenging prompt, the generated graph structure summary, validation history, and every rendered iteration image.",
        "",
        "## Summary",
        "",
        f"- Output: `{payload['output_dir']}`",
        "",
        "| Case | Success | Iterations | Visual Score | Nodes | Edges | Min Node Dist | Min Edge-Node | Min Edge-Edge | Max Parallel | Crossings | Errors Seen |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["results"]:
        graph = item.get("graph", {})
        history = item.get("history", [])
        metrics = item.get("layout_metrics", {})
        visual = item.get("visual_review") or {}
        errors_seen = sum(len(step.get("errors", [])) for step in history)
        lines.append(
            f"| {item['title']} | {item.get('success')} | {len(history)} | "
            f"{_metric(visual.get('score'))} | {len(graph.get('nodes', []))} | {len(graph.get('edges', []))} | "
            f"{_metric(metrics.get('min_node_distance'))} | {_metric(metrics.get('min_edge_node_clearance'))} | "
            f"{_metric(metrics.get('min_edge_edge_clearance'))} | {_metric(metrics.get('max_close_parallel_length'))} | "
            f"{_metric(metrics.get('edge_crossings'))} | "
            f"{errors_seen} |"
        )
    lines.append("")
    for item in payload["results"]:
        lines.extend(_markdown_case(item, report_dir))
    path.write_text("\n".join(lines), encoding="utf-8")


def _markdown_case(item: dict[str, Any], report_dir: Path) -> list[str]:
    lines = [
        f"## {item['title']}",
        "",
        f"Prompt: {item['prompt']}",
        "",
        f"Success: `{item.get('success')}`",
    ]
    graph = item.get("graph", {})
    if graph:
        lines.append(f"Nodes: `{len(graph.get('nodes', []))}`; Edges: `{len(graph.get('edges', []))}`")
    if item.get("error"):
        lines.append(f"Error: `{item['error']}`")
    warnings = graph.get("warnings", []) if graph else []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    lines.append("Iterations:")
    for step in item.get("history", []):
        iteration = step["iteration"]
        png = Path(item["case_dir"]) / f"iteration_{iteration}" / f"{item['id']}.png"
        rel_png = _relpath(png, report_dir)
        lines.append("")
        lines.append(f"### Iteration {iteration}")
        lines.append("")
        lines.append(
            f"- graph_ok: `{step.get('graph_ok')}`; layout_ok: `{step.get('layout_ok')}`; "
            f"code_ok: `{step.get('code_ok')}`; render_ok: `{step.get('render_ok')}`; "
            f"visual_ok: `{step.get('visual_ok')}`; visual_score: `{_metric(step.get('visual_score'))}`; "
            f"next_refinement: `{step.get('next_refinement')}`; {_metrics_text(step.get('layout_metrics', {}))}"
        )
        if step.get("visual_issues"):
            lines.append("- Visual issues:")
            lines.extend(f"  - {issue}" for issue in step["visual_issues"])
        if step.get("visual_suggestions"):
            lines.append("- Visual suggestions:")
            lines.extend(f"  - {suggestion}" for suggestion in step["visual_suggestions"])
        if step.get("errors"):
            lines.append("- Errors:")
            lines.extend(f"  - {error}" for error in step["errors"])
        if png.exists():
            lines.append("")
            lines.append(f"![{item['id']} iteration {iteration}]({rel_png})")
        else:
            lines.append("")
            lines.append("_No rendered image for this iteration._")
    lines.append("")
    return lines


def _write_html_report(path: Path, payload: dict[str, Any], report_dir: Path) -> None:
    blocks = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>Text-to-Graph Prompt Suite</title>",
        "<style>body{font-family:Arial,sans-serif;max-width:1100px;margin:32px auto;line-height:1.45;color:#1f2933}"
        "h1,h2{color:#111827}.case{border-top:1px solid #ddd;padding-top:24px;margin-top:28px}"
        ".meta{color:#52616b}.iter{margin:18px 0;padding:12px 16px;background:#f7f9fb;border-radius:8px}"
        "img{max-width:720px;width:100%;height:auto;border:1px solid #ddd;background:white}"
        "code{background:#eef2f7;padding:2px 4px;border-radius:4px}</style></head><body>",
        "<h1>Text-to-Graph Prompt Suite Report</h1>",
        f"<p class='meta'>Created: {payload['created_at']} | Model: <code>{payload['model']}</code> | Base URL: <code>{payload['base_url']}</code></p>",
        f"<p class='meta'>Output: <code>{_escape(payload['output_dir'])}</code></p>",
        "<p>This report records each challenging prompt, validation history, and every rendered iteration image.</p>",
        "<h2>Summary</h2>",
        "<table border='1' cellspacing='0' cellpadding='6'><tr><th>Case</th><th>Success</th><th>Iterations</th><th>Visual Score</th><th>Nodes</th><th>Edges</th><th>Min Node Dist</th><th>Min Edge-Node</th><th>Min Edge-Edge</th><th>Max Parallel</th><th>Crossings</th><th>Errors Seen</th></tr>",
    ]
    for item in payload["results"]:
        graph = item.get("graph", {})
        history = item.get("history", [])
        metrics = item.get("layout_metrics", {})
        visual = item.get("visual_review") or {}
        errors_seen = sum(len(step.get("errors", [])) for step in history)
        blocks.append(
            f"<tr><td>{_escape(item['title'])}</td><td>{item.get('success')}</td><td>{len(history)}</td>"
            f"<td>{_metric(visual.get('score'))}</td><td>{len(graph.get('nodes', []))}</td><td>{len(graph.get('edges', []))}</td>"
            f"<td>{_metric(metrics.get('min_node_distance'))}</td><td>{_metric(metrics.get('min_edge_node_clearance'))}</td>"
            f"<td>{_metric(metrics.get('min_edge_edge_clearance'))}</td><td>{_metric(metrics.get('max_close_parallel_length'))}</td>"
            f"<td>{_metric(metrics.get('edge_crossings'))}</td>"
            f"<td>{errors_seen}</td></tr>"
        )
    blocks.append("</table>")
    for item in payload["results"]:
        blocks.extend(_html_case(item, report_dir))
    blocks.append("</body></html>")
    path.write_text("\n".join(blocks), encoding="utf-8")


def _html_case(item: dict[str, Any], report_dir: Path) -> list[str]:
    graph = item.get("graph", {})
    blocks = [
        "<section class='case'>",
        f"<h2>{_escape(item['title'])}</h2>",
        f"<p><strong>Prompt:</strong> {_escape(item['prompt'])}</p>",
        f"<p><strong>Success:</strong> <code>{item.get('success')}</code></p>",
    ]
    if graph:
        blocks.append(
            f"<p><strong>Structure:</strong> {len(graph.get('nodes', []))} nodes, {len(graph.get('edges', []))} edges.</p>"
        )
    if item.get("error"):
        blocks.append(f"<p><strong>Error:</strong> <code>{_escape(item['error'])}</code></p>")
    warnings = graph.get("warnings", []) if graph else []
    if warnings:
        blocks.append("<p><strong>Warnings:</strong></p><ul>")
        blocks.extend(f"<li>{_escape(warning)}</li>" for warning in warnings)
        blocks.append("</ul>")
    for step in item.get("history", []):
        iteration = step["iteration"]
        png = Path(item["case_dir"]) / f"iteration_{iteration}" / f"{item['id']}.png"
        rel_png = _relpath(png, report_dir)
        blocks.append("<div class='iter'>")
        blocks.append(f"<h3>Iteration {iteration}</h3>")
        blocks.append(
            f"<p>graph_ok: <code>{step.get('graph_ok')}</code>; layout_ok: <code>{step.get('layout_ok')}</code>; "
            f"code_ok: <code>{step.get('code_ok')}</code>; render_ok: <code>{step.get('render_ok')}</code>; "
            f"visual_ok: <code>{step.get('visual_ok')}</code>; visual_score: <code>{_metric(step.get('visual_score'))}</code>; "
            f"next_refinement: <code>{step.get('next_refinement')}</code>; "
            f"{_escape(_metrics_text(step.get('layout_metrics', {})))}</p>"
        )
        if step.get("visual_issues"):
            blocks.append("<p><strong>Visual issues:</strong></p><ul>")
            blocks.extend(f"<li>{_escape(issue)}</li>" for issue in step["visual_issues"])
            blocks.append("</ul>")
        if step.get("visual_suggestions"):
            blocks.append("<p><strong>Visual suggestions:</strong></p><ul>")
            blocks.extend(f"<li>{_escape(suggestion)}</li>" for suggestion in step["visual_suggestions"])
            blocks.append("</ul>")
        if step.get("errors"):
            blocks.append("<ul>")
            blocks.extend(f"<li>{_escape(error)}</li>" for error in step["errors"])
            blocks.append("</ul>")
        if png.exists():
            blocks.append(f"<img src='{_escape(rel_png)}' alt='{_escape(item['id'])} iteration {iteration}'>")
        else:
            blocks.append("<p><em>No rendered image for this iteration.</em></p>")
        blocks.append("</div>")
    blocks.append("</section>")
    return blocks


def _relpath(path: Path, start: Path) -> str:
    return os.path.relpath(path.resolve(), start.resolve()).replace("\\", "/")


def _escape(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _metric(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _metrics_text(metrics: Any) -> str:
    if not isinstance(metrics, dict):
        metrics = {}
    return (
        f"min_node_dist: `{_metric(metrics.get('min_node_distance'))}`; "
        f"min_edge_node_clearance: `{_metric(metrics.get('min_edge_node_clearance'))}`; "
        f"min_edge_edge_clearance: `{_metric(metrics.get('min_edge_edge_clearance'))}`; "
        f"max_parallel: `{_metric(metrics.get('max_close_parallel_length'))}`; "
        f"crossings: `{_metric(metrics.get('edge_crossings'))}`"
    )


if __name__ == "__main__":
    raise SystemExit(main())
