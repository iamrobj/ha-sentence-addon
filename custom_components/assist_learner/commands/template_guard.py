"""Static analysis of LLM-written Jinja response templates (nothing from the LLM is trusted).

Only literal entity ids from the command's own entity list may be read, via an allow-listed set of
functions and filters. Anything else fails validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jinja2 import Environment, TemplateSyntaxError, nodes

ALLOWED_FUNCS = {"states", "state_attr", "is_state", "is_state_attr"}
ALLOWED_FILTERS = {"round", "float", "int", "default", "title", "lower", "upper", "abs", "string", "capitalize"}
ALLOWED_TESTS = {"number", "defined", "none", "string"}
SLOTS_NAME = "slots"

_ALLOWED_NODES = (
    nodes.Template,
    nodes.Output,
    nodes.TemplateData,
    nodes.Const,
    nodes.Call,
    nodes.Name,
    nodes.Filter,
    nodes.Test,
    nodes.Getattr,
    nodes.Getitem,
    nodes.Add,
    nodes.Sub,
    nodes.Mul,
    nodes.Div,
    nodes.Concat,
    nodes.Compare,
    nodes.Operand,
    nodes.And,
    nodes.Or,
    nodes.Not,
    nodes.If,
    nodes.CondExpr,
    nodes.Neg,
    nodes.Pos,
    nodes.Mod,
    nodes.FloorDiv,
)


@dataclass
class GuardResult:
    errors: list[str] = field(default_factory=list)
    entities: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_template(
    source: str, allowed_entities: set[str], slot_names: set[str] | None = None
) -> GuardResult:
    res = GuardResult()
    if not source or not source.strip():
        res.errors.append("template is empty")
        return res
    try:
        tree = Environment().parse(source)
    except TemplateSyntaxError as err:
        res.errors.append(f"syntax error: {err.message}")
        return res
    slot_names = slot_names or set()

    def walk(node: nodes.Node) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            res.errors.append(f"'{type(node).__name__}' is not allowed in response templates")
            return
        if isinstance(node, nodes.Call):
            fn = node.node
            if not (isinstance(fn, nodes.Name) and fn.name in ALLOWED_FUNCS):
                res.errors.append("only states(), state_attr(), is_state() and is_state_attr() may be called")
                return
            if not node.args or not isinstance(node.args[0], nodes.Const) or not isinstance(
                node.args[0].value, str
            ):
                res.errors.append(f"{fn.name}() needs a literal entity id as its first argument")
                return
            entity = node.args[0].value
            if entity not in allowed_entities:
                res.errors.append(f"entity '{entity}' is not in the command's entity list")
                return
            res.entities.add(entity)
            if node.dyn_args or node.dyn_kwargs:
                res.errors.append("argument unpacking is not allowed")
                return
        elif isinstance(node, nodes.Name):
            if node.name not in ALLOWED_FUNCS | {SLOTS_NAME, "true", "false", "none", "True", "False", "None"}:
                res.errors.append(f"variable '{node.name}' is not allowed")
                return
        elif isinstance(node, nodes.Filter):
            if node.name not in ALLOWED_FILTERS:
                res.errors.append(f"filter '{node.name}' is not allowed")
                return
        elif isinstance(node, nodes.Test):
            if node.name not in ALLOWED_TESTS:
                res.errors.append(f"test '{node.name}' is not allowed")
                return
        elif isinstance(node, nodes.Getattr):
            if not (isinstance(node.node, nodes.Name) and node.node.name == SLOTS_NAME):
                res.errors.append("attribute access is only allowed on 'slots'")
                return
            if slot_names and node.attr not in slot_names:
                res.errors.append(f"unknown slot '{node.attr}'")
                return
        elif isinstance(node, nodes.Getitem):
            if not (isinstance(node.node, nodes.Name) and node.node.name == SLOTS_NAME):
                res.errors.append("item access is only allowed on 'slots'")
                return
        for child in node.iter_child_nodes():
            walk(child)

    walk(tree)
    return res
