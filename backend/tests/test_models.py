"""The two enumerations the persisted contracts are built from, and their cross-checks.

``AuditEvent`` and ``UserRole`` values are written into rows and filtered on by
consumers, so they are schema. ``UserRole`` additionally has a second,
deliberately separate definition in :mod:`app.core.permissions` (as plain strings,
so that ``app.core`` stays importable without the ORM) — so the two have to be
checked against each other rather than trusted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.permissions import ROLE_PERMISSIONS, USER_ROLE, permissions_for
from app.models.audit import AuditEvent, AuditLog, validate_audit_event
from app.models.session import Session
from app.models.user import DEFAULT_ROLE, User, UserRole, validate_role
from app.schemas.user import UserRead

# -- AuditEvent --------------------------------------------------------------


def test_every_audit_event_value_is_lowercase_snake_case():
    for event in AuditEvent:
        value = str(event)
        assert value == value.lower(), value
        assert " " not in value and "-" not in value, value
        assert value.replace("_", "").isalnum(), value


def test_every_audit_event_value_is_distinct():
    """A duplicate value would make one of the two events unfilterable."""
    values = [str(event) for event in AuditEvent]
    assert len(values) == len(set(values))


def test_the_member_name_is_the_uppercase_form_of_the_value():
    """So a reader can map ``user_login_failed`` back to ``USER_LOGIN_FAILED``."""
    for event in AuditEvent:
        assert event.name == str(event).upper()


def test_the_phase_2_event_vocabulary():
    assert {str(event) for event in AuditEvent} == {
        "user_registered",
        "user_login",
        "user_login_failed",
        "user_logout",
        "password_changed",
        "password_reset_requested",
        "password_reset_completed",
        "session_created",
        "session_revoked",
        "sessions_revoked_all",
        "account_updated",
        "account_deleted",
    }


def test_validate_audit_event_returns_the_member_for_a_known_value():
    assert validate_audit_event("user_login") is AuditEvent.USER_LOGIN
    assert validate_audit_event(AuditEvent.USER_LOGIN) is AuditEvent.USER_LOGIN


@pytest.mark.parametrize(
    "value",
    ["", "user_loginn", "USER_LOGIN", "user login", "user.login", None, 42, b"user_login"],
)
def test_validate_audit_event_raises_on_junk(value):
    """A mistyped event would be a silently missing event, which is the worst kind.

    ``user_loginn`` is invisible to every filter that reports on sign-ins, so
    the boundary rejects it rather than storing it.
    """
    with pytest.raises(ValueError, match="Unknown audit event"):
        validate_audit_event(value)


def test_validate_audit_event_names_the_offending_value():
    with pytest.raises(ValueError, match="user_loginn"):
        validate_audit_event("user_loginn")


def test_an_audit_row_exposes_its_json_column_under_a_reserved_name_free_attribute():
    """``metadata`` is a ``Base`` attribute, so the ORM attribute is ``metadata_``.

    The column is still called ``metadata`` in the database and in the
    migration; the rename is on the Python side only.
    """
    assert "metadata" in AuditLog.__table__.c
    assert AuditLog.__table__.c["metadata"].name == "metadata"
    assert "metadata_" in AuditLog.__mapper__.columns
    assert "metadata" not in AuditLog.__mapper__.columns


def test_an_audit_row_has_no_updated_at():
    """The mixin's ``onupdate`` would rewrite the one row that must not change."""
    assert "updated_at" not in AuditLog.__table__.c
    assert "created_at" in AuditLog.__table__.c


# -- UserRole ----------------------------------------------------------------


def test_the_role_values():
    assert {str(role) for role in UserRole} == {"user", "admin"}
    assert DEFAULT_ROLE == UserRole.USER.value == "user"


@pytest.mark.parametrize("value", ["", "User", "USER", "admin ", "superuser", "root", None, 7])
def test_validate_role_raises_on_junk(value):
    """Junk is rejected loudly.

    A typo must not become an account that is silently unprivileged rather
    than loudly unprivileged.
    """
    with pytest.raises(ValueError, match="Unknown role"):
        validate_role(value)


def test_validate_role_returns_the_member_for_a_known_value():
    assert validate_role("admin") is UserRole.ADMIN
    assert validate_role(UserRole.ADMIN) is UserRole.ADMIN


def test_a_user_role_of_junk_degrades_to_the_default_rather_than_raising():
    """A drifted role must deny, not crash the request that reads it.

    The permission map is the fail-closed lookup this mirrors; if
    :attr:`User.role_enum` raised instead, one bad row would turn every
    protected endpoint into a 500 for its owner.
    """
    user = User(id=uuid.uuid4(), email="a@b.test", username="ada", role="wizard")

    assert user.role_enum is UserRole.USER
    assert permissions_for(user.role) == frozenset()


# -- The cross-check between the two role definitions -----------------------


def test_the_role_values_and_the_permission_map_keys_are_the_same_set():
    """The assertion that stops ``app.core.permissions`` and the ORM drifting.

    They are defined twice on purpose — ``app.core`` must stay importable
    without the ORM layer — so nothing but this check keeps them aligned. A role
    present in one and not the other is the worst kind of bug: an account whose
    role the map does not know is granted *nothing*, silently, with no error
    anywhere.
    """
    assert {str(role) for role in UserRole} == set(ROLE_PERMISSIONS)


def test_the_duplicated_role_constants_agree_with_the_enum():
    assert UserRole.USER.value == USER_ROLE


def test_no_role_is_granted_an_empty_permission_set():
    """The other half of the cross-check, stated as the property it protects."""
    for role in ROLE_PERMISSIONS:
        assert permissions_for(role), f"{role!r} is a role that grants nothing"


def test_admin_is_a_strict_superset_of_user():
    """No capability may be added to ``user`` that ``admin`` lacks."""
    user_only = permissions_for(UserRole.USER.value) - permissions_for("admin")
    assert user_only == frozenset()


# -- The session model -------------------------------------------------------


def test_a_session_stores_a_fixed_width_hex_digest():
    column = Session.__table__.c["token_hash"]
    assert column.type.length == 64
    assert column.nullable is False
    assert column.index is True


def test_a_session_cascades_with_its_account_but_may_be_revoked():
    foreign_key = next(iter(Session.__table__.c["user_id"].foreign_keys))
    assert foreign_key.column is User.__table__.c["id"]
    assert foreign_key.ondelete == "CASCADE"
    assert Session.__table__.c["revoked_at"].nullable is True


# -- UserRead ----------------------------------------------------------------


def _user(role: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email="ada@nexus.dev",
        username="ada",
        display_name=None,
        avatar_url=None,
        role=role,
        hashed_password="x",  # noqa: S106
        is_active=True,
        is_verified=False,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


def test_the_public_user_representation_carries_no_credential_material():
    fields = set(UserRead.model_fields)
    assert "hashed_password" not in fields
    assert "is_superuser" not in fields, "the role is now the single answer"


def test_permissions_are_derived_from_the_role_and_sorted():
    """Derived, so a client branches on a capability instead of a role name."""
    read = UserRead.model_validate(_user("admin"))

    assert read.permissions == sorted(str(permission) for permission in permissions_for("admin"))
    assert read.permissions == read.permissions  # stable across calls
    assert UserRead.model_validate(_user("admin")).permissions == read.permissions


def test_an_unknown_role_yields_an_empty_permission_list():
    """Fail-closed, and the same answer the permission checks would give."""
    assert UserRead.model_validate(_user("wizard")).permissions == []


def test_a_user_role_reports_its_capabilities_in_the_response():
    assert UserRead.model_validate(_user("user")).permissions == sorted(
        str(permission) for permission in permissions_for("user")
    )
