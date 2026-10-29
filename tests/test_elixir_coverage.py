import shutil
from pathlib import Path

import pytest

from crapper import elixir_coverage
from crapper.analyze import analyze_files
from crapper.coverage import load_bundle
from crapper.elixir_coverage import CONVERTER, cover_elixir, elixir_projects, uses_excoveralls
from crapper.runners import _clean_clojure, run_coverage


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


_MIX = "defmodule Demo.MixProject do\n  use Mix.Project\nend\n"
_MIX_XC = _MIX + "# test_coverage: [tool: ExCoveralls]\n"


def _fake_shell(calls, effects=None, code=0):
    effects = effects or {}

    def run(command, cwd):
        calls.append((list(command), Path(cwd)))
        for marker, effect in effects.items():
            if marker in command:
                effect(Path(cwd))
        return code

    return run


def test_each_source_belongs_to_its_nearest_mix_project(tmp_path):
    _write(tmp_path, "mix.exs", _MIX)
    _write(tmp_path, "apps/web/mix.exs", _MIX)
    core = _write(tmp_path, "lib/core.ex", "")
    web = _write(tmp_path, "apps/web/lib/web.ex", "")
    loose = _write(tmp_path, "notes/x.py", "")
    assert elixir_projects(tmp_path, [core, web, loose]) == [
        tmp_path.resolve(),
        (tmp_path / "apps" / "web").resolve(),
    ]


def test_excoveralls_is_detected_from_mix_exs(tmp_path):
    _write(tmp_path, "mix.exs", _MIX)
    assert uses_excoveralls(tmp_path) is False
    _write(tmp_path, "mix.exs", _MIX_XC)
    assert uses_excoveralls(tmp_path) is True


def test_built_in_cover_exports_then_converts_to_lcov(tmp_path, monkeypatch):
    _write(tmp_path, "mix.exs", _MIX)
    source = _write(tmp_path, "lib/demo.ex", "")
    stale = _write(tmp_path, "cover/crapper.coverdata", "old")
    calls = []

    def export(cwd):
        assert not stale.exists()
        _write(cwd, "cover/crapper.coverdata", "new")

    monkeypatch.setattr(
        elixir_coverage, "run_shell", _fake_shell(calls, {"--export-coverage": export})
    )
    cover_elixir(tmp_path, [source])
    report = tmp_path / "target" / "coverage" / "elixir" / "lcov.info"
    assert calls == [
        (["mix", "test", "--cover", "--export-coverage", "crapper"], tmp_path.resolve()),
        (
            [
                "env", "MIX_ENV=test", "mix", "run", "--no-start", "--no-compile",
                str(CONVERTER), str(stale), str(report),
            ],
            tmp_path.resolve(),
        ),
    ]
    assert CONVERTER.is_file()
    assert report.parent.is_dir()


def test_failed_tests_still_convert_what_was_exported(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "mix.exs", _MIX)
    source = _write(tmp_path, "lib/demo.ex", "")
    calls = []
    effects = {"--export-coverage": lambda cwd: _write(cwd, "cover/crapper.coverdata", "x")}
    monkeypatch.setattr(elixir_coverage, "run_shell", _fake_shell(calls, effects, code=2))
    cover_elixir(tmp_path, [source])
    assert len(calls) == 2
    assert "Elixir tests exited 2" in capsys.readouterr().err


def test_nothing_exported_scores_zero(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "mix.exs", _MIX)
    source = _write(tmp_path, "lib/demo.ex", "")
    calls = []
    monkeypatch.setattr(elixir_coverage, "run_shell", _fake_shell(calls, code=1))
    cover_elixir(tmp_path, [source])
    assert len(calls) == 1
    assert "Elixir coverage will score 0%" in capsys.readouterr().err


def test_excoveralls_writes_lcov_that_is_moved_into_target(tmp_path, monkeypatch):
    _write(tmp_path, "mix.exs", _MIX_XC)
    source = _write(tmp_path, "lib/demo.ex", "")
    _write(tmp_path, "cover/lcov.info", "stale")
    calls = []

    def lcov(cwd):
        assert not (cwd / "cover" / "lcov.info").exists()
        _write(cwd, "cover/lcov.info", "SF:lib/demo.ex\nDA:1,1\nend_of_record\n")

    monkeypatch.setattr(elixir_coverage, "run_shell", _fake_shell(calls, {"coveralls.lcov": lcov}))
    cover_elixir(tmp_path, [source])
    assert calls == [(["mix", "coveralls.lcov"], tmp_path.resolve())]
    report = tmp_path / "target" / "coverage" / "elixir" / "lcov.info"
    assert report.read_text(encoding="utf-8").startswith("SF:lib/demo.ex")


def test_excoveralls_without_a_report_scores_zero(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "mix.exs", _MIX_XC)
    source = _write(tmp_path, "lib/demo.ex", "")
    monkeypatch.setattr(elixir_coverage, "run_shell", _fake_shell([], code=1))
    cover_elixir(tmp_path, [source])
    assert "Elixir coverage will score 0%" in capsys.readouterr().err


def test_no_mix_exs_skips_elixir(tmp_path, monkeypatch, capsys):
    source = _write(tmp_path, "lib/demo.ex", "")
    calls = []
    monkeypatch.setattr(elixir_coverage, "run_shell", _fake_shell(calls))
    cover_elixir(tmp_path, [source])
    assert calls == []
    assert "No mix.exs; skipping Elixir coverage." in capsys.readouterr().err


def test_run_coverage_dispatches_elixir(tmp_path, monkeypatch):
    source = _write(tmp_path, "lib/demo.ex", "")
    seen = []
    monkeypatch.setattr("crapper.elixir_coverage.cover_elixir", lambda root, files: seen.append(files))
    assert run_coverage(tmp_path, [source], None) == 0
    assert seen == [[source]]


def test_clojure_cleanup_keeps_the_elixir_report(tmp_path):
    report = _write(tmp_path, "target/coverage/elixir/lcov.info", "SF:x\n")
    _write(tmp_path, "target/coverage/elixir/index.html", "forms covered")
    _clean_clojure(tmp_path)
    assert report.is_file()
    assert (tmp_path / "target" / "coverage" / "elixir" / "index.html").is_file()


@pytest.mark.skipif(shutil.which("mix") is None, reason="Elixir is not installed")
def test_converter_scores_a_real_mix_project(tmp_path, capfd):
    _write(
        tmp_path,
        "mix.exs",
        """defmodule Sample.MixProject do
  use Mix.Project
  def project, do: [app: :sample, version: "0.1.0", deps: []]
end
""",
    )
    source = _write(
        tmp_path,
        "lib/sample.ex",
        """defmodule Sample do
  def classify(0), do: :zero
  def classify(n) when n > 0, do: :pos
  def classify(_), do: :neg

  def never(x) do
    if x, do: 1, else: 2
  end
end
""",
    )
    _write(
        tmp_path,
        "test/sample_test.exs",
        """defmodule SampleTest do
  use ExUnit.Case
  test "classify" do
    assert Sample.classify(0) == :zero
    assert Sample.classify(3) == :pos
  end
end
""",
    )
    _write(tmp_path, "test/test_helper.exs", "ExUnit.start()\n")
    cover_elixir(tmp_path, [source])
    assert "imported" not in capfd.readouterr().out
    entries = analyze_files([source], tmp_path, load_bundle(tmp_path))
    scores = {entry.name: (entry.complexity, round(entry.coverage, 1)) for entry in entries}
    assert scores == {"classify/1": (5, 66.7), "never/1": (2, 0.0)}


def test_interleaved_clauses_score_only_their_own_lines(tmp_path):
    from crapper.coverage import CoverageBundle, parse_lcov

    source = _write(
        tmp_path,
        "lib/live.ex",
        """defmodule Demo.Live do
  def handle_info(:a, s), do: s
  def handle_info(:b, s), do: s
  def handle_info(:a, s), do: s
end
""",
    )
    # :a runs on lines 2 and 4; :b's line 3 never runs.
    lcov = "SF:lib/live.ex\nDA:2,1\nDA:3,0\nDA:4,1\nBRDA:3,0,0,0\nBRDA:3,0,1,0\nend_of_record\n"
    bundle = CoverageBundle(lcov=parse_lcov(lcov))
    entries = analyze_files([source], tmp_path, bundle)
    assert {entry.name: entry.coverage for entry in entries} == {
        "handle_info/2 :a": 100.0,
        "handle_info/2 :b": 0.0,
    }
