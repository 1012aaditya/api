"""Guards against dialect-specific mistakes in migrations and models.

Both checks here exist because the same bug was shipped twice: a boolean
column whose server default was written as ``1``. SQLite accepts it, stores it,
and compares it happily. PostgreSQL stores the default as ``true`` and then
refuses ``SELECT true = 1`` when Alembic compares the model against the
database — so ``alembic check`` dies and the schema cannot be verified at all.

Autogenerating a migration while pointed at SQLite is how it gets in: Alembic
renders ``true()`` as whatever the *connected* dialect uses, so a correct model
produces an incorrect migration.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import Boolean

from app.models import Base

MIGRATIONS = sorted((pathlib.Path(__file__).parent.parent / "alembic" / "versions").glob("*.py"))


def _boolean_columns_with_numeric_default(path: pathlib.Path) -> list[str]:
    """Find `sa.Column(..., sa.Boolean(), server_default=sa.text("1"))`."""
    tree = ast.parse(path.read_text())
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "Column"):
            continue

        is_boolean = any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "Boolean"
            for arg in node.args
        )
        if not is_boolean:
            continue

        for keyword in node.keywords:
            if keyword.arg != "server_default":
                continue
            value = keyword.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "text"
                and value.args
                and isinstance(value.args[0], ast.Constant)
                and str(value.args[0].value) in {"0", "1"}
            ):
                first = node.args[0] if node.args else None
                name = first.value if isinstance(first, ast.Constant) else "?"
                offenders.append(f"{name} (line {node.lineno})")
    return offenders


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migrations_use_portable_boolean_defaults(path: pathlib.Path) -> None:
    offenders = _boolean_columns_with_numeric_default(path)
    assert not offenders, (
        f"{path.name} gives a Boolean column a numeric server default: "
        f"{', '.join(offenders)}. PostgreSQL stores these as true/false and then "
        "refuses to compare them against 1, which breaks `alembic check`. "
        "Use sa.true() / sa.false()."
    )


def test_models_use_portable_boolean_defaults() -> None:
    """The same rule, applied to the declarative models."""
    offenders: list[str] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not isinstance(column.type, Boolean):
                continue
            default = column.server_default
            if default is None:
                continue
            rendered = str(getattr(default, "arg", default)).strip("'\" ")
            if rendered in {"0", "1"}:
                offenders.append(f"{table.name}.{column.name}")
    assert not offenders, (
        "These Boolean columns have a numeric server default, which PostgreSQL "
        f"will not compare against: {', '.join(offenders)}. Use true()/false()."
    )
