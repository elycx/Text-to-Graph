"""LaTeX rendering utilities."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RenderResult:
    success: bool
    tex_path: Path
    pdf_path: Path | None = None
    png_path: Path | None = None
    log: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "success": self.success,
            "tex_path": str(self.tex_path),
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "png_path": str(self.png_path) if self.png_path else None,
            "error": self.error,
        }


def render_latex(latex_code: str, output_dir: str | Path, job_name: str = "graph", make_png: bool = True) -> RenderResult:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    tex_path = output_path / f"{job_name}.tex"
    tex_path.write_text(latex_code, encoding="utf-8")

    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        return RenderResult(False, tex_path, error="pdflatex was not found on PATH.")

    command = [
        pdflatex,
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-jobname={job_name}",
        tex_path.name,
    ]
    completed = subprocess.run(
        command,
        cwd=output_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    log = completed.stdout
    pdf_path = output_path / f"{job_name}.pdf"
    if completed.returncode != 0 or not pdf_path.exists():
        return RenderResult(False, tex_path, log=log, error=_last_error_line(log))

    png_path = _render_png(pdf_path, output_path / job_name) if make_png else None
    return RenderResult(True, tex_path, pdf_path=pdf_path, png_path=png_path, log=log)


def _render_png(pdf_path: Path, output_prefix: Path) -> Path | None:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    completed = subprocess.run(
        [pdftoppm, "-png", "-singlefile", str(pdf_path), str(output_prefix)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    png_path = output_prefix.with_suffix(".png")
    if completed.returncode == 0 and png_path.exists():
        return png_path
    return None


def _last_error_line(log: str) -> str:
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("!") or "error" in line.lower():
            return line
    return lines[-1] if lines else "LaTeX compilation failed."

