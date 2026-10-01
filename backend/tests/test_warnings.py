"""The ``filterwarnings`` ini rules that guard application code.

``pytest.ini`` ignores ``DeprecationWarning`` outright and then escalates the
ones raised from ``app.*``. Order matters: pytest prepends each ini entry, so
the last one wins, and putting the blanket ignore last would silence the very
escalation the rule exists for.
"""

from __future__ import annotations

import warnings

import pytest


def _warn_from(module_name: str) -> None:
    """Emit a ``DeprecationWarning`` attributed to ``module_name``.

    A filter's ``module`` field is matched against the module a warning is
    issued from, which :func:`warnings.warn_explicit` takes as an argument — so
    this exercises a module-scoped filter without editing a real module to make
    it warn.
    """
    warnings.warn_explicit(
        "legacy call path",
        DeprecationWarning,
        f"{module_name.replace('.', '/')}.py",
        1,
        module=module_name,
    )


def _rule_index(action: str, module_pattern: str | None) -> int | None:
    """Where a ``DeprecationWarning`` rule with this module pattern sits."""
    for index, (rule_action, _, category, module, _) in enumerate(warnings.filters):
        if rule_action != action or category is not DeprecationWarning:
            continue
        if getattr(module, "pattern", None) == module_pattern:
            return index
    return None


def test_a_deprecation_warning_from_an_app_module_becomes_an_error():
    with pytest.raises(DeprecationWarning, match="legacy call path"):
        _warn_from("app.core.legacy_probe")


def test_a_deprecation_warning_from_outside_app_stays_ignored():
    _warn_from("tests.some_other_probe")


def test_the_app_scoped_rule_outranks_the_blanket_ignore():
    """Reordering the ini entries would silently disable the escalation."""
    app_rule = _rule_index("error", "app.*")
    ignore_rule = _rule_index("ignore", None)

    assert app_rule is not None, f"no app-scoped error rule in {warnings.filters}"
    assert ignore_rule is not None, f"no blanket ignore rule in {warnings.filters}"
    assert app_rule < ignore_rule
