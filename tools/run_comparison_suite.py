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

from text_to_graph.llm import (
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMClientConfig,
    LLMGraphError,
    VisualReview,
    extract_graph_with_gpt5,
    review_graph_image_with_gpt5,
)
from text_to_graph.preprocess import preprocess_text
from text_to_graph.renderer import render_latex
from text_to_graph.tikz import generate_latex_document
from text_to_graph.validator import (
    ValidationReport,
    layout_quality_metrics,
    repair_graph,
    validate_generated_code,
    validate_graph,
    validate_layout_quality,
)
from text_to_graph.workflow import TextToGraphWorkflow


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare full workflow against single-prompt LLM output.")
    parser.add_argument("--prompts", type=Path, default=Path("examples/prompt_suite.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/comparison_suite"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/comparison_suite"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=600)
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

    config = LLMClientConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
    )
    workflow = TextToGraphWorkflow(max_iterations=args.max_iterations, llm_config=config)
    run_output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for case in prompts:
        case_id = case["id"]
        case_dir = run_output / case_id
        full_dir = case_dir / "workflow"
        single_dir = case_dir / "single_prompt"
        case_dir.mkdir(parents=True, exist_ok=True)

        try:
            full_result = workflow.run(case["prompt"], output_dir=full_dir, job_name=case_id, render=True)
            full_payload: dict[str, Any] = full_result.to_dict()
        except LLMGraphError as exc:
            full_payload = {"success": False, "error": str(exc), "history": []}

        try:
            single_payload = run_single_prompt_llm(case["prompt"], single_dir, case_id, config)
        except LLMGraphError as exc:
            single_payload = {"success": False, "error": str(exc), "history": []}

        item = {
            "id": case_id,
            "title": case["title"],
            "prompt": case["prompt"],
            "case_dir": str(case_dir),
            "workflow": full_payload,
            "single_prompt": single_payload,
        }
        results.append(item)
        (case_dir / f"{case_id}_comparison.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    payload = {
        "created_at": run_started.isoformat(timespec="seconds"),
        "model": args.model,
        "base_url": args.base_url,
        "output_dir": str(run_output),
        "results": results,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "comparison_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown_report(args.report_dir / "comparison_report.md", payload, args.report_dir)
    write_html_report(args.report_dir / "comparison_report.html", payload, args.report_dir)
    print(json.dumps(summary(payload), ensure_ascii=False, indent=2))
    return 0


def run_single_prompt_llm(
    text: str,
    output_dir: Path,
    job_name: str,
    config: LLMClientConfig,
) -> dict[str, Any]:
    prepared_text = preprocess_text(text)
    spec = repair_graph(extract_graph_with_gpt5(prepared_text, config=config))
    graph_report = validate_graph(spec)
    layout_report = validate_layout_quality(spec)
    layout_metrics = layout_quality_metrics(spec)
    tikz_code = generate_latex_document(spec)
    code_report = validate_generated_code(tikz_code, spec)
    render_result = None
    visual_review = None

    if graph_report.ok and code_report.ok:
        render_result = render_latex(tikz_code, output_dir, job_name=job_name)
        if render_result.success and render_result.png_path:
            visual_review = review_graph_image_with_gpt5(
                prepared_text,
                spec,
                render_result.png_path,
                layout_metrics=layout_metrics,
                config=config,
            )
        elif render_result.success:
            visual_review = VisualReview(
                acceptable=False,
                issues=["Rendered PDF did not produce a PNG image for visual review."],
                suggestions=["Enable PNG rendering before comparing visual scores."],
            )

    visual_ok = bool(visual_review and visual_review.ok)
    success = (
        graph_report.ok
        and layout_report.ok
        and code_report.ok
        and bool(render_result and render_result.success)
        and visual_ok
    )
    errors = graph_report.errors + layout_report.errors + code_report.errors
    if render_result and render_result.error:
        errors.append(render_result.error)
    if visual_review and not visual_review.ok:
        errors.extend(f"Visual review: {issue}" for issue in visual_review.issues)

    history = [
        {
            "iteration": 0,
            "graph_ok": graph_report.ok,
            "layout_ok": layout_report.ok,
            "code_ok": code_report.ok,
            "render_ok": bool(render_result and render_result.success),
            "visual_ok": visual_ok,
            "visual_score": visual_review.score if visual_review else None,
            "visual_issues": visual_review.issues if visual_review else [],
            "visual_suggestions": visual_review.suggestions if visual_review else [],
            "layout_metrics": layout_metrics,
            "errors": errors,
        }
    ]
    return {
        "success": success,
        "iterations": 0,
        "graph": spec.to_dict(),
        "graph_validation": graph_report.to_dict(),
        "layout_validation": layout_report.to_dict(),
        "layout_metrics": layout_metrics,
        "visual_review": visual_review.to_dict() if visual_review else None,
        "code_validation": code_report.to_dict(),
        "render": render_result.to_dict() if render_result else None,
        "history": history,
    }


def summary(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload["results"]
    workflow_success = sum(1 for item in results if item.get("workflow", {}).get("success"))
    single_success = sum(1 for item in results if item.get("single_prompt", {}).get("success"))
    return {
        "model": payload["model"],
        "total": len(results),
        "workflow_successes": workflow_success,
        "single_prompt_successes": single_success,
        "output_dir": payload["output_dir"],
        "report": "comparison_report.html",
    }


def write_markdown_report(path: Path, payload: dict[str, Any], report_dir: Path) -> None:
    lines = [
        "# Text-to-Graph Workflow Comparison Report",
        "",
        f"- Created: {payload['created_at']}",
        f"- Model: `{payload['model']}`",
        f"- Base URL: `{payload['base_url']}`",
        f"- Output: `{payload['output_dir']}`",
        "",
        "This report compares the full iterative workflow against a single-prompt LLM baseline.",
        "",
        "## Score Table",
        "",
        "| Case | Workflow Score | Single-Prompt Score | Delta | Workflow Success | Single Success | Workflow Iters | Workflow Area/Node | Single Area/Node |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["results"]:
        workflow = item.get("workflow", {})
        single = item.get("single_prompt", {})
        workflow_score = _score(workflow)
        single_score = _score(single)
        delta = None if workflow_score is None or single_score is None else workflow_score - single_score
        lines.append(
            f"| {item['title']} | {_metric(workflow_score)} | {_metric(single_score)} | {_metric(delta)} | "
            f"{workflow.get('success')} | {single.get('success')} | {len(workflow.get('history', []))} | "
            f"{_metric(_metrics(workflow).get('canvas_area_per_node'))} | "
            f"{_metric(_metrics(single).get('canvas_area_per_node'))} |"
        )

    for item in payload["results"]:
        lines.extend(_markdown_case(item, report_dir))
    path.write_text("\n".join(lines), encoding="utf-8")


def _markdown_case(item: dict[str, Any], report_dir: Path) -> list[str]:
    workflow = item.get("workflow", {})
    single = item.get("single_prompt", {})
    lines = [
        "",
        f"## {item['title']}",
        "",
        f"Prompt: {item['prompt']}",
        "",
        f"- Workflow score: `{_metric(_score(workflow))}`; success: `{workflow.get('success')}`",
        f"- Single-prompt score: `{_metric(_score(single))}`; success: `{single.get('success')}`",
        "",
    ]
    for label, payload in (("Workflow", workflow), ("Single Prompt", single)):
        png = _png_path(payload)
        if png and png.exists():
            lines.append(f"### {label}")
            lines.append("")
            lines.append(f"![{item['id']} {label}]({_relpath(png, report_dir)})")
            lines.append("")
    return lines


def write_html_report(path: Path, payload: dict[str, Any], report_dir: Path) -> None:
    blocks = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Workflow Comparison</title>",
        "<style>body{font-family:Arial,sans-serif;max-width:1220px;margin:32px auto;line-height:1.45;color:#1f2933}"
        "h1,h2{color:#111827}.meta{color:#52616b}.case{border-top:1px solid #ddd;padding-top:24px;margin-top:28px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px 8px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}"
        ".panel{background:#f7f9fb;border-radius:8px;padding:12px}.panel img{max-width:100%;height:auto;border:1px solid #ddd;background:white}"
        "code{background:#eef2f7;padding:2px 4px;border-radius:4px}</style></head><body>",
        "<h1>Text-to-Graph Workflow Comparison Report</h1>",
        f"<p class='meta'>Created: {payload['created_at']} | Model: <code>{payload['model']}</code> | Base URL: <code>{payload['base_url']}</code></p>",
        f"<p class='meta'>Output: <code>{_escape(payload['output_dir'])}</code></p>",
        "<p>This report compares the full iterative workflow against a single-prompt LLM baseline.</p>",
        "<h2>Score Table</h2>",
        "<table><tr><th>Case</th><th>Workflow Score</th><th>Single Score</th><th>Delta</th><th>Workflow Success</th><th>Single Success</th><th>Workflow Iters</th><th>Workflow Area/Node</th><th>Single Area/Node</th></tr>",
    ]
    for item in payload["results"]:
        workflow = item.get("workflow", {})
        single = item.get("single_prompt", {})
        workflow_score = _score(workflow)
        single_score = _score(single)
        delta = None if workflow_score is None or single_score is None else workflow_score - single_score
        blocks.append(
            f"<tr><td>{_escape(item['title'])}</td><td>{_metric(workflow_score)}</td><td>{_metric(single_score)}</td><td>{_metric(delta)}</td>"
            f"<td>{workflow.get('success')}</td><td>{single.get('success')}</td><td>{len(workflow.get('history', []))}</td>"
            f"<td>{_metric(_metrics(workflow).get('canvas_area_per_node'))}</td>"
            f"<td>{_metric(_metrics(single).get('canvas_area_per_node'))}</td></tr>"
        )
    blocks.append("</table>")
    for item in payload["results"]:
        blocks.extend(_html_case(item, report_dir))
    blocks.append("</body></html>")
    path.write_text("\n".join(blocks), encoding="utf-8")


def _html_case(item: dict[str, Any], report_dir: Path) -> list[str]:
    workflow = item.get("workflow", {})
    single = item.get("single_prompt", {})
    blocks = [
        "<section class='case'>",
        f"<h2>{_escape(item['title'])}</h2>",
        f"<p><strong>Prompt:</strong> {_escape(item['prompt'])}</p>",
        "<div class='pair'>",
    ]
    for label, payload in (("Workflow", workflow), ("Single Prompt", single)):
        png = _png_path(payload)
        blocks.append("<div class='panel'>")
        blocks.append(
            f"<h3>{label}</h3><p>score: <code>{_metric(_score(payload))}</code>; success: <code>{payload.get('success')}</code>; "
            f"area/node: <code>{_metric(_metrics(payload).get('canvas_area_per_node'))}</code></p>"
        )
        if png and png.exists():
            blocks.append(f"<img src='{_escape(_relpath(png, report_dir))}' alt='{_escape(item['id'] + ' ' + label)}'>")
        else:
            blocks.append("<p><em>No final rendered image.</em></p>")
        blocks.append("</div>")
    blocks.append("</div></section>")
    return blocks


def _score(payload: dict[str, Any]) -> float | None:
    visual = payload.get("visual_review") or {}
    score = visual.get("score")
    try:
        return float(score) if score is not None else None
    except (TypeError, ValueError):
        return None


def _metrics(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("layout_metrics")
    return metrics if isinstance(metrics, dict) else {}


def _png_path(payload: dict[str, Any]) -> Path | None:
    render = payload.get("render") or {}
    png = render.get("png_path")
    return Path(png) if png else None


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


if __name__ == "__main__":
    raise SystemExit(main())
