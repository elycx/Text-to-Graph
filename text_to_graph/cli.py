"""Command-line interface for the Text-to-Graph workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluate import evaluate_benchmark
from .llm import DEFAULT_API_KEY, DEFAULT_BASE_URL, DEFAULT_MODEL, LLMClientConfig, LLMGraphError, check_health, list_models
from .workflow import TextToGraphWorkflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate graph figures from natural-language descriptions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate a graph figure.")
    generate_parser.add_argument("text", nargs="?", help="Graph description. If omitted, stdin is used.")
    generate_parser.add_argument("--text-file", type=Path, help="Read the graph description from a text file.")
    generate_parser.add_argument("--output", type=Path, default=Path("outputs/demo"), help="Output directory.")
    generate_parser.add_argument("--name", default="graph", help="Output file base name.")
    generate_parser.add_argument("--no-render", action="store_true", help="Skip LaTeX rendering.")
    generate_parser.add_argument("--param", action="append", default=[], help="Set an integer parameter, for example m=4.")
    generate_parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    generate_parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key for the local OpenAI-compatible server.")
    generate_parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name.")
    generate_parser.add_argument("--timeout", type=int, default=300, help="Per GPT API request timeout in seconds.")
    generate_parser.add_argument("--max-iterations", type=int, default=2, help="Maximum LLM refinement rounds.")

    eval_parser = subparsers.add_parser("evaluate", help="Evaluate a benchmark JSON file.")
    eval_parser.add_argument("--benchmark", type=Path, default=Path("examples/benchmark.json"), help="Benchmark path.")
    eval_parser.add_argument("--output", type=Path, default=Path("outputs/eval"), help="Output directory.")
    eval_parser.add_argument("--no-render", action="store_true", help="Skip LaTeX rendering.")
    eval_parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    eval_parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key for the local OpenAI-compatible server.")
    eval_parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name.")
    eval_parser.add_argument("--timeout", type=int, default=300, help="Per GPT API request timeout in seconds.")

    health_parser = subparsers.add_parser("health", help="Check the local GPT-5 API health endpoint.")
    health_parser.add_argument("--health-url", default="http://localhost:1455/health", help="Health endpoint URL.")

    models_parser = subparsers.add_parser("models", help="List models from the local GPT-5 API.")
    models_parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    models_parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key for the local OpenAI-compatible server.")

    args = parser.parse_args(argv)
    if args.command == "generate":
        return _generate(args)
    if args.command == "evaluate":
        return _evaluate(args)
    if args.command == "health":
        return _health(args)
    if args.command == "models":
        return _models(args)
    return 2


def _generate(args: argparse.Namespace) -> int:
    text = _read_text(args)
    workflow = TextToGraphWorkflow(
        max_iterations=args.max_iterations,
        llm_config=LLMClientConfig(base_url=args.base_url, api_key=args.api_key, model=args.model, timeout=args.timeout),
    )
    try:
        result = workflow.run(
            text,
            output_dir=args.output,
            job_name=args.name,
            render=not args.no_render,
            params=_parse_params(args.param),
        )
    except LLMGraphError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def _evaluate(args: argparse.Namespace) -> int:
    try:
        payload = evaluate_benchmark(
            args.benchmark,
            output_dir=args.output,
            render=not args.no_render,
            llm_config=LLMClientConfig(base_url=args.base_url, api_key=args.api_key, model=args.model, timeout=args.timeout),
        )
    except LLMGraphError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(payload["summary"], indent=2))
    return 0


def _health(args: argparse.Namespace) -> int:
    try:
        payload = check_health(LLMClientConfig(health_url=args.health_url))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _models(args: argparse.Namespace) -> int:
    try:
        models = list_models(LLMClientConfig(base_url=args.base_url, api_key=args.api_key))
    except LLMGraphError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"models": models}, ensure_ascii=False, indent=2))
    return 0


def _read_text(args: argparse.Namespace) -> str:
    if args.text_file:
        return args.text_file.read_text(encoding="utf-8")
    if args.text:
        return args.text
    return sys.stdin.read()


def _parse_params(items: list[str]) -> dict[str, int]:
    params: dict[str, int] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --param value {item!r}; expected name=value.")
        name, value = item.split("=", 1)
        name = name.strip()
        try:
            params[name] = int(value)
        except ValueError as exc:
            raise SystemExit(f"Invalid integer for --param {name}: {value!r}.") from exc
    return params


if __name__ == "__main__":
    raise SystemExit(main())
