from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
import sys


class RenderError(RuntimeError):
    pass


def _find_converter_script() -> Path:
    # Preferred for local workspace testing: use the workflow-aligned converter at workspace root
    # (e.g. E:\Code\convert_toml_to_readme.py) when present.
    repo_root = Path(__file__).resolve().parents[2]
    # Be robust to different directory depths (e.g. container path like /app)
    # by walking upwards and checking for a sibling converter.
    for base in (repo_root, *repo_root.parents):
        cand = (base / "convert_toml_to_readme.py").resolve()
        if cand.exists() and cand.is_file():
            return cand
        cand = (base.parent / "convert_toml_to_readme.py").resolve()
        if cand.exists() and cand.is_file():
            return cand

    # Preferred bundled converter for container/runtime: final schema only.
    bundled_final = repo_root / "scripts" / "convert_toml_to_readme_final.py"
    if bundled_final.exists() and bundled_final.is_file():
        return bundled_final

    # Fallback: bundled script in this repo.
    bundled = repo_root / "scripts" / "convert_toml_to_readme.py"
    if bundled.exists():
        return bundled

    raise RenderError(
        "converter script not found: scripts/convert_toml_to_readme_final.py or scripts/convert_toml_to_readme.py"
    )


def render_readme_from_toml(toml_text: str) -> str:
    converter = _find_converter_script()

    with tempfile.TemporaryDirectory(prefix="hoa-prserver-") as tmp:
        tmp_path = Path(tmp)
        toml_path = tmp_path / "readme.toml"
        readme_path = tmp_path / "README.md"

        toml_path.write_text(toml_text, encoding="utf-8", newline="\n")

        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")

        proc = subprocess.run(
            [
                sys.executable,
                "-u",
                str(converter),
                "--input",
                str(toml_path),
                "--overwrite",
            ],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RenderError(
                "render failed\n"
                f"stdout:\n{proc.stdout}\n\n"
                f"stderr:\n{proc.stderr}\n"
            )

        if not readme_path.exists():
            raise RenderError("render succeeded but README.md not produced")

        return readme_path.read_text(encoding="utf-8")
