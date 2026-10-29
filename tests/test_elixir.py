from crapper.discover import is_test_file, iter_source_files, language_of
from crapper.languages.elixir import functions_in_source


def _rows(source, path="lib/demo/board.ex"):
    return [
        (fn.namespace, fn.name, fn.complexity, fn.start_line, fn.end_line)
        for fn in functions_in_source(source, path, "/proj")
    ]


def test_def_defp_defmacro_and_keyword_bodies():
    source = """defmodule Demo.Board do
  def place(x), do: x

  defp hide(x) do
    x
  end

  defmacro build(ast), do: ast

  defmacrop secret(ast), do: ast

  def size, do: 0
end
"""
    assert _rows(source) == [
        ("Demo.Board", "place/1", 1, 2, 2),
        ("Demo.Board", "hide/1", 1, 4, 6),
        ("Demo.Board", "build/1", 1, 8, 8),
        ("Demo.Board", "secret/1", 1, 10, 10),
        ("Demo.Board", "size/0", 1, 12, 12),
    ]


def test_branching_forms_each_add_decisions():
    source = """defmodule Demo do
  def choose(x, y) do
    if x and y, do: 1
    unless x || y, do: 2
    case x do
      1 -> :one
      n when n > 1 -> :many
      _ -> :none
    end
    cond do
      x -> 1
      true -> 2
    end
  end
end
"""
    # 1 + if + and + unless + || + 3 case clauses + when + 2 cond clauses
    assert _rows(source)[0][:3] == ("Demo", "choose/2", 11)


def test_with_try_receive_for_and_fn():
    source = """defmodule Demo do
  def flow(a, b, xs) do
    with {:ok, x} <- a, y = 2, {:ok, z} <- b do
      x + y + z
    else
      :error -> 0
    end
    try do
      1
    rescue
      e in RuntimeError -> e
    catch
      :exit, _ -> 1
    after
      2
    end
    receive do
      :a -> 1
    after
      10 -> 2
    end
    for x <- xs, x > 1, into: [], do: x
    fn 1 -> 2; _ -> 3 end
    fn x -> x end
  end
end
"""
    # 1 + with(2 generators + 1 else clause) + try(rescue + catch)
    #   + receive(clause + after clause) + for(generator + filter)
    #   + two-clause fn (2); the one-clause fn adds nothing
    assert _rows(source)[0][:3] == ("Demo", "flow/3", 12)


def test_multi_clause_function_is_one_entry_with_one_decision_per_clause():
    source = """defmodule Demo do
  def fact(0), do: 1
  def fact(n) when n > 0 do
    n * fact(n - 1)
  end

  def fact(a, b), do: a + b
end
"""
    assert _rows(source) == [
        # 1 + 2 clauses + when
        ("Demo", "fact/1", 4, 2, 5),
        ("Demo", "fact/2", 1, 7, 7),
    ]


def test_default_arguments_head_and_guard_on_single_clause():
    source = """defmodule Demo do
  def run(a, opts \\\\ [])
  def run(a, opts) when is_list(opts), do: {a, opts}
end
"""
    # the bodiless head is not a clause; the guard is a decision
    assert _rows(source) == [("Demo", "run/2", 2, 2, 3)]


def test_nested_modules_and_protocol_implementations():
    source = """defmodule Outer do
  def top, do: 1

  defmodule Inner do
    def deep(x), do: x
  end

  defimpl String.Chars do
    def to_string(_), do: "outer"
  end
end

defimpl Inspect, for: Demo.User do
  def inspect(u, _opts), do: u.name
end

defprotocol Sizeable do
  def size(data)
end
"""
    assert [row[:3] for row in _rows(source)] == [
        ("Outer", "top/0", 1),
        ("Outer.Inner", "deep/1", 1),
        ("String.Chars.Outer", "to_string/1", 1),
        ("Inspect.Demo.User", "inspect/2", 1),
    ]


def test_defs_inside_a_macro_quote_stay_with_the_macro():
    source = """defmodule Demo.Using do
  defmacro __using__(_opts) do
    quote do
      def injected(x), do: if(x, do: 1, else: 2)
    end
  end
end
"""
    assert [row[:3] for row in _rows(source)] == [("Demo.Using", "__using__/1", 2)]


def test_unquoted_and_operator_names_are_skipped():
    source = """defmodule Demo do
  def unquote(name)(x), do: x
  def a + b, do: a
  def kept(x), do: x
end
"""
    assert [row[:3] for row in _rows(source)] == [("Demo", "kept/1", 1)]


def test_strings_comments_and_pipes_do_not_add_decisions():
    source = '''defmodule Demo do
  @doc "if and or case"
  def literal(x) do
    # if x and y
    text = "if x and y #{x}"
    x |> to_string() |> Kernel.<>(text)
  end
end
'''
    assert [row[:3] for row in _rows(source)] == [("Demo", "literal/1", 1)]


def test_code_outside_a_module_uses_the_file_path():
    source = "def loose(x), do: x\n"
    assert [row[:3] for row in _rows(source, "lib/demo/loose.ex")] == [
        ("demo.loose", "loose/1", 1)
    ]


def test_functions_carry_language_path_and_byte_span():
    source = "defmodule Demo do\n  def a, do: 1\nend\n"
    (fn,) = functions_in_source(source, "lib/demo.ex", "/proj")
    assert fn.language == "elixir"
    assert fn.path == "lib/demo.ex"
    assert source.encode()[fn.start_byte : fn.end_byte] == b"def a, do: 1"


def test_ex_files_are_elixir_and_scripts_build_output_and_deps_are_not(tmp_path):
    assert language_of("lib/demo/board.ex") == "elixir"
    assert language_of("mix.exs") is None
    assert language_of("test/demo_test.exs") is None
    assert is_test_file("test/support/factory.ex") is True
    source = tmp_path / "lib" / "demo.ex"
    source.parent.mkdir()
    source.write_text("defmodule Demo do\n  def a, do: 1\nend\n", encoding="utf-8")
    for skipped in ("deps/jason/lib/jason.ex", "_build/test/lib/x.ex", ".elixir_ls/x.ex"):
        path = tmp_path / skipped
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("defmodule X do\nend\n", encoding="utf-8")
    assert iter_source_files([tmp_path]) == [source.resolve()]


def test_help_lists_elixir_extension_coverage_and_snapshot_keys():
    from crapper.cli import HELP

    assert "Elixir (.ex)" in HELP
    assert "Elixir       mix coveralls.lcov" in HELP
    assert "Elixir       the module, and name/arity" in HELP
