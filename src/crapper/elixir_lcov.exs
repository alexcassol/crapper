# Convert an exported Erlang :cover file into LCOV line hits.
#
#   MIX_ENV=test mix run --no-start --no-compile elixir_lcov.exs DATA OUT
#
# `mix run` puts the project's compiled modules on the code path, so each
# module can name its own source file. A line run by several clauses keeps
# its highest hit count.

[data, out] = System.argv()

# :cover prints a notice on import and on every analysis of imported data,
# once per module. Its server inherits this group leader, so a silent one
# keeps a large project's run readable. Real failures still raise.
{:ok, quiet} = StringIO.open("")
Process.group_leader(self(), quiet)

{:ok, _} = :cover.start()
:ok = :cover.import(String.to_charlist(data))

hits =
  for module <- :cover.imported_modules(),
      source = module.module_info(:compile)[:source],
      source != nil,
      {:ok, calls} = :cover.analyse(module, :calls, :line),
      {{_module, line}, count} <- calls,
      line > 0 do
    {List.to_string(source), line, count}
  end

records =
  hits
  |> Enum.group_by(&elem(&1, 0), fn {_source, line, count} -> {line, count} end)
  |> Enum.sort()
  |> Enum.map(fn {source, lines} ->
    entries =
      lines
      |> Enum.group_by(&elem(&1, 0), &elem(&1, 1))
      |> Enum.sort()
      |> Enum.map(fn {line, counts} -> "DA:#{line},#{Enum.max(counts)}\n" end)

    ["SF:", source, "\n", entries, "end_of_record\n"]
  end)

File.write!(out, records)
