"""End-to-end workflow orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .llm import (
    LLMClientConfig,
    LLMGraphError,
    VisualReview,
    extract_graph_with_gpt5,
    refine_graph_with_gpt5,
    relayout_graph_with_gpt5,
    review_graph_image_with_gpt5,
)
from .layout import assign_positions
from .layout_adjust import nudge_node_positions
from .preprocess import preprocess_text
from .renderer import RenderResult, render_latex
from .schema import GraphSpec
from .tikz import generate_latex_document
from .validator import (
    ValidationReport,
    layout_quality_metrics,
    repair_graph,
    validate_generated_code,
    validate_graph,
    validate_layout_quality,
)


@dataclass
class WorkflowResult:
    spec: GraphSpec
    tikz_code: str
    graph_report: ValidationReport
    code_report: ValidationReport
    layout_report: ValidationReport = field(default_factory=ValidationReport)
    layout_metrics: dict[str, float | None] = field(default_factory=dict)
    visual_review: VisualReview | None = None
    render_result: RenderResult | None = None
    iterations: int = 0
    success: bool = False
    history: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "iterations": self.iterations,
            "graph": self.spec.to_dict(),
            "graph_validation": self.graph_report.to_dict(),
            "layout_validation": self.layout_report.to_dict(),
            "layout_metrics": self.layout_metrics,
            "visual_review": self.visual_review.to_dict() if self.visual_review else None,
            "code_validation": self.code_report.to_dict(),
            "render": self.render_result.to_dict() if self.render_result else None,
            "history": self.history,
        }


class TextToGraphWorkflow:
    """LLM-only pipeline from text to graph figure."""

    def __init__(
        self,
        max_iterations: int = 2,
        llm_config: LLMClientConfig | None = None,
    ) -> None:
        self.max_iterations = max_iterations
        self.llm_config = llm_config or LLMClientConfig()

    def run(
        self,
        text: str,
        output_dir: str | Path | None = None,
        job_name: str = "graph",
        render: bool = True,
        params: Mapping[str, int] | None = None,
    ) -> WorkflowResult:
        prepared_text = preprocess_text(text)
        spec = extract_graph_with_gpt5(prepared_text, params=params, config=self.llm_config)
        history: list[dict[str, object]] = []
        last_result: WorkflowResult | None = None

        for iteration in range(self.max_iterations + 1):
            spec = repair_graph(spec)
            graph_report = validate_graph(spec)
            if graph_report.ok:
                spec = nudge_node_positions(spec)
            layout_report = validate_layout_quality(spec)
            layout_metrics = layout_quality_metrics(spec)
            tikz_code = generate_latex_document(spec)
            code_report = validate_generated_code(tikz_code, spec)
            render_result = None
            visual_review = None

            can_render = render and graph_report.ok and code_report.ok
            if can_render:
                render_dir = Path(output_dir or "outputs") / f"iteration_{iteration}"
                render_result = render_latex(tikz_code, render_dir, job_name=job_name)
                if render_result.success:
                    if render_result.png_path:
                        visual_review = review_graph_image_with_gpt5(
                            prepared_text,
                            spec,
                            render_result.png_path,
                            layout_metrics=layout_metrics,
                            config=self.llm_config,
                        )
                    else:
                        visual_review = VisualReview(
                            acceptable=False,
                            issues=["Rendered PDF did not produce a PNG image for GPT visual review."],
                            suggestions=["Enable PNG rendering so the visual feedback loop can inspect the figure."],
                        )

            visual_ok = (not render) or bool(visual_review and visual_review.ok)
            visual_errors = _visual_errors(visual_review)
            success = (
                graph_report.ok
                and layout_report.ok
                and code_report.ok
                and (not render or bool(render_result and render_result.success))
                and visual_ok
            )
            history.append(
                {
                    "iteration": iteration,
                    "graph_ok": graph_report.ok,
                    "layout_ok": layout_report.ok,
                    "code_ok": code_report.ok,
                    "render_ok": bool(render_result and render_result.success) if render else None,
                    "visual_ok": visual_ok if render else None,
                    "visual_score": visual_review.score if visual_review else None,
                    "visual_issues": visual_review.issues if visual_review else [],
                    "visual_suggestions": visual_review.suggestions if visual_review else [],
                    "layout_metrics": layout_metrics,
                    "next_refinement": _next_refinement_mode(
                        success, iteration, self.max_iterations, render_result, visual_review
                    ),
                    "errors": graph_report.errors
                    + layout_report.errors
                    + code_report.errors
                    + visual_errors
                    + ([render_result.error] if render_result and render_result.error else []),
                }
            )
            last_result = WorkflowResult(
                spec=spec,
                tikz_code=tikz_code,
                graph_report=graph_report,
                layout_report=layout_report,
                layout_metrics=layout_metrics,
                code_report=code_report,
                visual_review=visual_review,
                render_result=render_result,
                iterations=iteration,
                success=success,
                history=history,
            )
            if success:
                break
            if iteration < self.max_iterations:
                spec = self._refine(
                    prepared_text,
                    spec,
                    graph_report,
                    layout_report,
                    code_report,
                    render_result,
                    visual_review,
                    layout_metrics,
                    history,
                    params,
                )

        assert last_result is not None
        if output_dir:
            self._write_metadata(last_result, Path(output_dir), job_name)
        return last_result

    def _refine(
        self,
        text: str,
        spec: GraphSpec,
        graph_report: ValidationReport,
        layout_report: ValidationReport,
        code_report: ValidationReport,
        render_result: RenderResult | None,
        visual_review: VisualReview | None,
        layout_metrics: dict[str, float | None],
        history: list[dict[str, object]],
        params: Mapping[str, int] | None,
    ) -> GraphSpec:
        issues = list(graph_report.errors) + list(layout_report.errors) + list(code_report.errors)
        if render_result and not render_result.success:
            issues.append(f"LaTeX rendering failed: {render_result.error}")
        issues.extend(_visual_errors(visual_review))
        if visual_review and visual_review.suggestions:
            issues.extend(f"Visual review suggestion: {suggestion}" for suggestion in visual_review.suggestions)
        score_history = [step.get("visual_score") for step in history if step.get("visual_score") is not None]
        if score_history:
            issues.append(f"Visual score history across rendered iterations: {score_history}.")
        if len(score_history) >= 2 and float(score_history[-1]) <= float(max(score_history[:-1])) + 0.5:
            issues.append(
                "The visual score has stagnated. Abandon the current routing strategy and choose a structurally "
                "different layout with cleaner local spacing, compact separation, and different long-edge corridors."
            )

        if visual_review and not visual_review.ok:
            image_path = render_result.png_path if render_result and render_result.png_path else None
            candidate = self._macro_relayout(text, spec, issues, image_path, layout_metrics, params)
            motion = _coordinate_motion(spec, candidate)
            candidate.warnings.append(_motion_warning(motion))
            if _insufficient_macro_motion(motion):
                retry_issues = issues + [
                    "The macro relayout still moved node coordinates too little. Rebuild the layout with meaningful "
                    "cluster or local-gadget motion, but do not expand the whole drawing unless clearance requires it.",
                    _motion_warning(motion),
                ]
                candidate = self._macro_relayout(text, spec, retry_issues, image_path, layout_metrics, params)
                candidate.warnings.append(_motion_warning(_coordinate_motion(spec, candidate)))
            return candidate

        return refine_graph_with_gpt5(
            text,
            previous_spec=spec,
            issues=issues,
            params=params,
            config=self.llm_config,
        )

    def _macro_relayout(
        self,
        text: str,
        spec: GraphSpec,
        issues: list[str],
        image_path: Path | None,
        layout_metrics: dict[str, float | None],
        params: Mapping[str, int] | None,
    ) -> GraphSpec:
        try:
            return relayout_graph_with_gpt5(
                text,
                previous_spec=spec,
                issues=issues,
                image_path=image_path,
                layout_metrics=layout_metrics,
                params=params,
                config=self.llm_config,
            )
        except LLMGraphError as exc:
            if image_path is None:
                raise
            fallback_issues = issues + [
                f"The image-based macro relayout API call failed: {exc}",
                "Retry as a text-only macro relayout using the visual-review issues and previous JSON.",
            ]
            candidate = relayout_graph_with_gpt5(
                text,
                previous_spec=spec,
                issues=fallback_issues,
                image_path=None,
                layout_metrics=layout_metrics,
                params=params,
                config=self.llm_config,
            )
            candidate.warnings.append("Image-based macro relayout failed; used text-only macro relayout fallback.")
            return candidate

    def _write_metadata(self, result: WorkflowResult, output_dir: Path, job_name: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = output_dir / f"{job_name}.json"
        metadata_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


def _visual_errors(visual_review: VisualReview | None) -> list[str]:
    if not visual_review or visual_review.ok:
        return []
    prefix = "Visual review"
    score = f" score={visual_review.score:.1f}" if visual_review.score is not None else ""
    if not visual_review.issues:
        return [f"{prefix}{score}: figure was rejected but no specific issue was returned."]
    return [f"{prefix}{score}: {issue}" for issue in visual_review.issues]


def _next_refinement_mode(
    success: bool,
    iteration: int,
    max_iterations: int,
    render_result: RenderResult | None,
    visual_review: VisualReview | None,
) -> str | None:
    if success:
        return None
    if iteration >= max_iterations:
        return None
    if visual_review and not visual_review.ok:
        return "visual_macro_relayout"
    if render_result and not render_result.success:
        return "text_refinement"
    return "text_refinement"


def _coordinate_motion(before: GraphSpec, after: GraphSpec) -> dict[str, float | int]:
    before_positions = assign_positions(before)
    after_positions = assign_positions(after)
    shared = [node_id for node_id in before.node_ids() if node_id in after_positions and node_id in before_positions]
    if not shared:
        return {"shared_nodes": 0, "average": 0.0, "maximum": 0.0, "moved_fraction": 0.0}

    distances = []
    for node_id in shared:
        before_point = before_positions[node_id]
        after_point = after_positions[node_id]
        dx = after_point[0] - before_point[0]
        dy = after_point[1] - before_point[1]
        distances.append((dx * dx + dy * dy) ** 0.5)

    return {
        "shared_nodes": len(shared),
        "average": sum(distances) / len(distances),
        "maximum": max(distances),
        "moved_fraction": sum(1 for distance in distances if distance >= 0.9) / len(distances),
    }


def _insufficient_macro_motion(motion: dict[str, float | int]) -> bool:
    return float(motion.get("average", 0.0)) < 0.75 or float(motion.get("moved_fraction", 0.0)) < 0.35


def _motion_warning(motion: dict[str, float | int]) -> str:
    return (
        "Macro relayout coordinate motion: "
        f"average={float(motion.get('average', 0.0)):.2f}, "
        f"maximum={float(motion.get('maximum', 0.0)):.2f}, "
        f"moved_fraction={float(motion.get('moved_fraction', 0.0)):.2f}."
    )
