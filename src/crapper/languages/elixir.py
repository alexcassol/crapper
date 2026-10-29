"""Elixir functions, macros, and cyclomatic complexity.

An entry is one function: every clause of `def`, `defp`, `defmacro`, or
`defmacrop` with the same name and arity in the same module. The name is
`name/arity`, the way Elixir writes it. A bodiless head (a protocol
callback, or a head that only declares default arguments) is not a clause.
A definition inside another definition, such as one generated in a `quote`,
stays with the enclosing definition.

Decision points follow the structural rule of the other languages:

- `if` and `unless`
- each `and`, `or`, `&&`, `||`, and `when` guard
- each clause of `case`, `cond`, `receive`, and `receive ... after`
- each `rescue` and `catch` clause, and each `with ... else` clause
- each `<-` clause of `with`
- each generator and filter of `for`
- each clause of an anonymous function with more than one clause
- each clause of a multi-clause function, the same as a `case`

An OTP or LiveView callback (`handle_event`, `handle_info`, `handle_call`,
`handle_cast`, `handle_continue`, `handle_async`) is a set of independent
handlers, not one decision tree. Each clause whose first argument is a
literal (a string, an atom, or a tuple that starts with an atom) is its own
entry, named like `handle_event/3 "save"` or `handle_info/2 :tick`, the way a
TypeScript route callback is `GET /users`. Clauses with the same literal
share an entry. The other clauses stay under the plain `name/arity`.

The namespace is the module. Nested `defmodule` names join with a dot. A
`defimpl` is `Protocol.Type`, with the enclosing module as the type when
`for:` is absent. Code outside any module takes its namespace from the path.
"""

from collections.abc import Iterator

from crapper.languages.treesitter import (
    child_of_type,
    descendants,
    end_line,
    node_text,
    parse,
    start_line,
)
from crapper.model import Function

_DEFINERS = {"def", "defp", "defmacro", "defmacrop"}
_LOGIC = {"and", "or", "&&", "||", "when"}
_BRANCHES = {"if", "unless"}
_CLAUSE_BLOCKS = {"do_block", "else_block", "rescue_block", "catch_block", "after_block"}
_CALLBACKS = {
    "handle_event",
    "handle_info",
    "handle_call",
    "handle_cast",
    "handle_continue",
    "handle_async",
}
_ATOMS = {"atom", "quoted_atom"}


def _call_name(data: bytes, node) -> str | None:
    if node.type != "call" or not node.children:
        return None
    target = node.children[0]
    if target.type != "identifier":
        return None
    return node_text(data, target)


def _operator(node) -> str | None:
    for child in node.children:
        if not child.is_named:
            return child.type
    return None


def _arguments(node) -> list:
    args = child_of_type(node, "arguments")
    if args is None:
        return []
    return list(args.named_children)


def _is_generator(node) -> bool:
    return node.type == "binary_operator" and _operator(node) == "<-"


def _call_decisions(data: bytes, node) -> int:
    name = _call_name(data, node)
    if name in _BRANCHES:
        return 1
    if name == "with":
        return sum(1 for arg in _arguments(node) if _is_generator(arg))
    if name == "for":
        return sum(1 for arg in _arguments(node) if arg.type != "keywords")
    return 0


def _anonymous_decisions(node) -> int:
    clauses = sum(1 for child in node.named_children if child.type == "stab_clause")
    return clauses if clauses > 1 else 0


def _decisions(data: bytes, node) -> int:
    if node.type == "binary_operator":
        return 1 if _operator(node) in _LOGIC else 0
    if node.type == "stab_clause":
        return 1 if node.parent is not None and node.parent.type in _CLAUSE_BLOCKS else 0
    if node.type == "anonymous_function":
        return _anonymous_decisions(node)
    return _call_decisions(data, node)


def _clause_decisions(data: bytes, clause) -> int:
    return sum(_decisions(data, node) for node in descendants(clause))


def _without(node, operator: str):
    """The left side of `left <operator> right`, or the node itself."""

    if node.type == "binary_operator" and _operator(node) == operator:
        return node.named_children[0]
    return node


def _head_call(definition):
    args = _arguments(definition)
    if not args:
        return None
    return _without(args[0], "when")


def _head(data: bytes, definition) -> tuple[str, int] | None:
    """`(name, arity)` of a definition, or None for an unquoted or operator name."""

    head = _head_call(definition)
    if head is None:
        return None
    if head.type == "identifier":
        return node_text(data, head), 0
    name = _call_name(data, head)
    if name is None:
        return None
    return name, len(_arguments(head))


def _literal(data: bytes, pattern) -> str | None:
    pattern = _without(pattern, "=")
    if pattern.type in _ATOMS:
        return node_text(data, pattern)
    if pattern.type == "string" and child_of_type(pattern, "interpolation") is None:
        return node_text(data, pattern)
    if pattern.type == "tuple" and pattern.named_children:
        first = pattern.named_children[0]
        return node_text(data, first) if first.type in _ATOMS else None
    return None


def _dispatch_key(data: bytes, definition, name: str) -> str | None:
    """The literal a callback clause handles, such as `"save"` or `:tick`."""

    if name not in _CALLBACKS:
        return None
    params = _arguments(_head_call(definition))
    if not params:
        return None
    return _literal(data, params[0])


def _entry_name(data: bytes, definition, head: tuple[str, int]) -> str:
    name, arity = head
    key = _dispatch_key(data, definition, name)
    return f"{name}/{arity} {key}" if key else f"{name}/{arity}"


def _has_body(definition) -> bool:
    if child_of_type(definition, "do_block") is not None:
        return True
    args = child_of_type(definition, "arguments")
    return args is not None and child_of_type(args, "keywords") is not None


def _ancestors(node) -> Iterator:
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _is_definition(data: bytes, node) -> bool:
    return _call_name(data, node) in _DEFINERS


def _is_top_definition(data: bytes, node) -> bool:
    if not _is_definition(data, node):
        return False
    return not any(_is_definition(data, parent) for parent in _ancestors(node))


def _impl_target(data: bytes, impl, current: str) -> str:
    for arg in _arguments(impl):
        if arg.type != "keywords":
            continue
        for pair in arg.named_children:
            key = child_of_type(pair, "keyword")
            if key is not None and node_text(data, key).strip() == "for:":
                return node_text(data, pair.named_children[-1])
    return current


def _enter_module(data: bytes, node, current: str) -> str:
    name = _call_name(data, node)
    args = _arguments(node)
    if name not in {"defmodule", "defimpl"} or not args:
        return current
    alias = node_text(data, args[0])
    if name == "defimpl":
        return f"{alias}.{_impl_target(data, node, current)}"
    return f"{current}.{alias}" if current else alias


def _module_of(data: bytes, node) -> str:
    current = ""
    for parent in reversed(list(_ancestors(node))):
        current = _enter_module(data, parent, current)
    return current


def _path_namespace(path: str, source_root: str | None) -> str:
    relative = path.replace("\\", "/")
    root = (source_root or "").replace("\\", "/").rstrip("/")
    if root and relative.startswith(root + "/"):
        relative = relative[len(root) + 1 :]
    for prefix in ("./", "lib/"):
        if relative.startswith(prefix):
            relative = relative[len(prefix) :]
    if relative.endswith(".ex"):
        relative = relative[: -len(".ex")]
    return relative.replace("/", ".")


def _group(data: bytes, root) -> dict[tuple[str, str], list]:
    groups: dict[tuple[str, str], list] = {}
    for node in descendants(root):
        if not _is_top_definition(data, node):
            continue
        head = _head(data, node)
        if head is None:
            continue
        key = (_module_of(data, node), _entry_name(data, node, head))
        groups.setdefault(key, []).append(node)
    return groups


def _complexity(data: bytes, clauses: list) -> int:
    dispatch = len(clauses) if len(clauses) > 1 else 0
    return 1 + dispatch + sum(_clause_decisions(data, clause) for clause in clauses)


def _function(data: bytes, key, nodes, path: str, source_root: str | None) -> Function | None:
    clauses = [node for node in nodes if _has_body(node)]
    if not clauses:
        return None
    namespace, name = key
    return Function(
        name=name,
        namespace=namespace or _path_namespace(path, source_root),
        complexity=_complexity(data, clauses),
        start_line=min(start_line(node) for node in nodes),
        end_line=max(end_line(node) for node in nodes),
        path=path,
        language="elixir",
        start_byte=min(node.start_byte for node in nodes),
        end_byte=max(node.end_byte for node in nodes),
        line_ranges=tuple((start_line(node), end_line(node)) for node in nodes),
    )


def functions_in_source(
    source: str, path: str, source_root: str | None = None
) -> list[Function]:
    data, tree = parse(source, "elixir")
    found: list[Function] = []
    for key, nodes in _group(data, tree.root_node).items():
        function = _function(data, key, nodes, path, source_root)
        if function is not None:
            found.append(function)
    return found
