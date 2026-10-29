"""Run Elixir coverage and leave LCOV where the loader reads it.

Each `.ex` file belongs to its nearest `mix.exs`, so an umbrella app runs in
its own directory. A project whose `mix.exs` names ExCoveralls runs
`mix coveralls.lcov`, and its `cover/lcov.info` is moved into
`target/coverage/elixir`. Any other project uses Elixir's built-in cover:
`mix test --cover --export-coverage crapper`, then `elixir_lcov.exs` turns
`cover/crapper.coverdata` into LCOV line hits.

A failed test run still converts whatever was exported. A project that
exported nothing scores 0%.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from crapper.runners import _coverage_report, _modules_with, _warn, run_shell

CONVERTER = Path(__file__).with_name("elixir_lcov.exs")
_EXPORT = "crapper"


def elixir_projects(root: Path, files: list[Path]) -> list[Path]:
    root = root.resolve()
    return [project.resolve() for project in _modules_with(files, ".ex", "mix.exs", root)]


def uses_excoveralls(project: Path) -> bool:
    mix = project / "mix.exs"
    return "ExCoveralls" in mix.read_text(encoding="utf-8", errors="replace")


def _fresh(path: Path) -> Path:
    path.unlink(missing_ok=True)
    return path


def _cover_with_excoveralls(project: Path, report: Path) -> bool:
    produced = _fresh(project / "cover" / "lcov.info")
    code = run_shell(["mix", "coveralls.lcov"], project)
    if not produced.is_file():
        return False
    shutil.move(str(produced), str(report))
    if code != 0:
        _warn(f"Elixir tests exited {code} in {project}. Coverage was still recorded.")
    return True


def _cover_built_in(project: Path, report: Path) -> bool:
    data = _fresh(project / "cover" / f"{_EXPORT}.coverdata")
    code = run_shell(["mix", "test", "--cover", "--export-coverage", _EXPORT], project)
    if not data.is_file():
        return False
    if code != 0:
        _warn(f"Elixir tests exited {code} in {project}. Coverage was still recorded.")
    convert = [
        "env", "MIX_ENV=test", "mix", "run", "--no-start", "--no-compile",
        str(CONVERTER), str(data), str(report),
    ]
    return run_shell(convert, project) == 0


def cover_elixir(root: Path, files: list[Path]) -> None:
    projects = elixir_projects(root, files)
    if not projects:
        _warn("No mix.exs; skipping Elixir coverage.")
        return
    for project in projects:
        report = _fresh(_coverage_report(root, project, "elixir"))
        report.parent.mkdir(parents=True, exist_ok=True)
        if uses_excoveralls(project):
            done = _cover_with_excoveralls(project, report)
        else:
            done = _cover_built_in(project, report)
        if not done:
            _warn(f"No Elixir coverage in {project}. Elixir coverage will score 0%.")
