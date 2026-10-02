"""The password policy, the username rule, and the profile/deletion payloads.

Every schema that accepts a *new* password routes through the same validator,
so the policy is tested once here and then re-asserted on each door: a schema
that forgot to attach the check would otherwise be a way around it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.security import (
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequested,
)
from app.schemas.user import (
    MAX_PASSWORD_LENGTH,
    MAX_USERNAME_LENGTH,
    PASSWORD_RULES,
    UserCreate,
    UserDeletion,
    UserLogin,
    UserUpdate,
    password_min_length,
    password_rule_status,
    validate_password_strength,
)

COMPLIANT = "Correct-Horse-7"

#: ``(password, fragment of the rejection message)``. Each entry breaks exactly
#: one rule so the message names the rule that actually failed.
VIOLATIONS = [
    ("Ab1!", "at least 8 characters"),
    ("correct-horse-7", "an uppercase letter"),
    ("CORRECT-HORSE-7", "a lowercase letter"),
    ("Correct-Horse-Battery", "a digit"),
    ("CorrectHorse7", "a character that is not a letter or a digit"),
]


def _registration(**overrides) -> UserCreate:
    payload = {
        "username": "ada",
        "email": "ada@nexus.dev",
        "password": COMPLIANT,
    }
    return UserCreate(**{**payload, **overrides})


# -- The policy, through every door -----------------------------------------


def test_a_compliant_password_is_accepted():
    assert validate_password_strength(COMPLIANT) == COMPLIANT
    assert _registration().password == COMPLIANT


@pytest.mark.parametrize(("password", "fragment"), VIOLATIONS)
def test_each_rule_rejects_its_own_violation(password, fragment):
    with pytest.raises(ValueError, match=fragment):
        validate_password_strength(password)


def test_a_password_over_the_maximum_length_is_rejected():
    """An over-long password is rejected.

    bcrypt only looks at the first 72 bytes, so the tail of a long password
    would be silently ignored — the cap turns that into a rejection.
    """
    over_the_top = "A1!" + "a" * MAX_PASSWORD_LENGTH
    with pytest.raises(ValueError, match="at most"):
        validate_password_strength(over_the_top)


def test_the_policy_is_attached_to_every_payload_that_carries_a_new_password():
    """Registration, change and reset all go through the same door.

    A schema that declared ``password: str`` instead of ``Password`` would let a
    weak password straight into the service layer, and the tests above would
    still pass — they exercise the validator, not the wiring.
    """
    current = "irrelevant"
    for build in (
        lambda password: _registration(password=password),
        lambda password: PasswordChange(current_password=current, new_password=password),
        lambda password: PasswordResetConfirm(token="t" * 40, new_password=password),
    ):
        with pytest.raises(ValidationError):
            build("weak")
        assert build(COMPLIANT) is not None


def test_a_login_payload_is_not_subject_to_the_new_password_policy():
    """The policy gates what is *stored*; it must not lock out an old account.

    An account registered under a weaker policy still has to be able to sign in
    and then change its password to something compliant.
    """
    login = UserLogin(email="ada@nexus.dev", password="short")  # noqa: S106
    assert login.password == "short"
    with pytest.raises(ValidationError):
        UserLogin(email="ada@nexus.dev", password="")


# -- The minimum length is a setting, read lazily ---------------------------


def test_the_minimum_length_follows_the_settings(make_settings):
    """The specific reason the validator resolves settings on every call.

    A module-level constant would freeze whatever the environment held at
    import, and a stricter deployment would advertise one policy and enforce
    another.
    """
    make_settings(PASSWORD_MIN_LENGTH="24")  # noqa: S106

    assert password_min_length() == 24
    with pytest.raises(ValueError, match="at least 24 characters"):
        validate_password_strength("Correct-Horse-7")
    assert validate_password_strength("C0rrect-Horse-Battery-Staple-9") is not None


def test_a_looser_setting_loosens_the_rule(make_settings):
    make_settings(PASSWORD_MIN_LENGTH="4")  # noqa: S106

    assert validate_password_strength("Ab1!") == "Ab1!"


def test_the_default_minimum_length_is_eight(settings):
    assert settings.password_min_length == 8
    assert password_min_length() == 8


# -- The rule vocabulary -----------------------------------------------------


def test_the_rules_are_five_in_a_fixed_order():
    assert [rule.id for rule in PASSWORD_RULES] == [
        "min_length",
        "uppercase",
        "lowercase",
        "digit",
        "special",
    ]


def test_no_rule_text_bakes_in_a_length():
    """The checklist the frontend renders must not hard-code one deployment's number.

    ``password_min_length`` is a deployment setting; the minimum is reported
    through ``satisfied``, not spelled into shared copy that another deployment
    would then lie about.
    """
    for rule in PASSWORD_RULES:
        assert not any(character.isdigit() for character in rule.label)
        assert not any(character.isdigit() for character in rule.description)


def test_the_checklist_reports_every_rule_with_its_verdict():
    status = password_rule_status(COMPLIANT)

    assert [entry["id"] for entry in status] == [rule.id for rule in PASSWORD_RULES]
    assert all(entry["satisfied"] is True for entry in status)
    assert all(entry["label"] for entry in status)

    partial = {entry["id"]: entry["satisfied"] for entry in password_rule_status("Correct7")}
    assert partial == {
        "min_length": True,
        "uppercase": True,
        "lowercase": True,
        "digit": True,
        "special": False,
    }


# -- Usernames ---------------------------------------------------------------


@pytest.mark.parametrize(
    "username",
    ["ada", "Ada_Lovelace-1", "9lives", "a" * MAX_USERNAME_LENGTH, "ab1"],
)
def test_a_legal_username_is_accepted(username):
    assert _registration(username=username).username == username


@pytest.mark.parametrize(
    ("username", "reason"),
    [
        ("", "empty"),
        ("a", "too short"),
        ("ab", "too short — the pattern's minimum is three"),
        ("_ada", "leading underscore"),
        ("-ada", "leading hyphen"),
        ("ada lovelace", "space"),
        ("ada@home", "at sign"),
        ("ada.lovelace", "dot"),
        ("äda", "non-ASCII"),
        ("a" * (MAX_USERNAME_LENGTH + 1), "over the column width"),
    ],
)
def test_an_illegal_username_is_rejected(username, reason):
    with pytest.raises(ValidationError) as excinfo:
        _registration(username=username)
    assert any(entry["loc"] == ("username",) for entry in excinfo.value.errors())


def test_only_the_first_character_of_a_username_is_constrained():
    """A trailing ``_`` or ``-`` is legal; only a *leading* one is not.

    The pattern is ``^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$``: the first character has
    to be something a user can recognise at a glance, the rest may be either
    separator.
    """
    for username in ("ada_", "ada-", "a_-", "Ada--Lovelace"):
        assert _registration(username=username).username == username


def test_username_case_is_preserved_not_folded():
    """A handle is shown back to its owner; folding it would lock them out.

    The unique index compares the value verbatim, and the ``0002`` backfill
    builds handles that differ only in case for some rows — so a lower-casing
    validator would make two distinct accounts collide.
    """
    created = _registration(username="AdaLovelace")

    assert created.username == "AdaLovelace"
    assert created.username != created.username.lower()


def test_a_username_is_trimmed_but_a_blank_one_becomes_none():
    """``""`` means "clear the field" on a profile update, not "set it to blank"."""
    assert _registration(username="  ada  ").username == "ada"
    assert UserUpdate(username="   ").username is None


# -- Avatar URLs -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(document.domain)",
        "  javascript:alert(1)  ",
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "ftp://example.com/a.png",
        "/relative/avatar.png",
        "https://",
    ],
)
def test_a_non_http_avatar_url_is_rejected(url):
    """The stored XSS vector: an avatar rendered into ``<img src>``.

    ``javascript:`` is the one that matters; the rest are rejected because a
    stored avatar is only ever an ordinary remote image, and every other scheme
    is either a script source or a local file read.
    """
    with pytest.raises(ValidationError) as excinfo:
        UserUpdate(avatar_url=url)
    assert any(entry["loc"] == ("avatar_url",) for entry in excinfo.value.errors())


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/ada.png",
        "http://example.com/ada.png?v=2",
        "HTTPS://CDN.EXAMPLE.COM/Ada.png",
    ],
)
def test_an_http_avatar_url_is_accepted(url):
    assert UserUpdate(avatar_url=url).avatar_url == url.strip()


def test_an_empty_avatar_url_clears_the_field():
    """A client that wants to remove the avatar sends ``""``, not ``null``."""
    assert UserUpdate(avatar_url="").avatar_url is None
    assert UserUpdate(avatar_url="   ").avatar_url is None
    assert UserUpdate(avatar_url=None).avatar_url is None


# -- Account deletion --------------------------------------------------------


def test_deletion_needs_the_password_and_the_confirmation():
    deletion = UserDeletion(password=COMPLIANT, confirm=True)

    assert deletion.confirm is True
    assert deletion.password == COMPLIANT


def test_deletion_is_refused_without_an_explicit_confirmation():
    """``password`` alone cannot tell a typed form from a replayed script."""
    with pytest.raises(ValidationError) as excinfo:
        UserDeletion(password=COMPLIANT, confirm=False)
    assert any(entry["loc"] == ("confirm",) for entry in excinfo.value.errors())


def test_deletion_is_refused_without_a_password():
    with pytest.raises(ValidationError) as excinfo:
        UserDeletion(confirm=True)
    assert any(entry["loc"] == ("password",) for entry in excinfo.value.errors())


def test_deletion_refuses_an_empty_password():
    with pytest.raises(ValidationError):
        UserDeletion(password="", confirm=True)


def test_deletion_has_no_default_for_confirm():
    """Omitting the field must be a validation error, not an implicit ``True``."""
    with pytest.raises(ValidationError):
        UserDeletion.model_validate({"password": COMPLIANT})


# -- The reset schemas -------------------------------------------------------


def test_a_reset_request_normalises_the_address_like_every_other_email():
    assert PasswordResetRequest(email="  ADA@Nexus.DEV ").email == "ada@nexus.dev"


def test_a_reset_request_rejects_a_malformed_address():
    with pytest.raises(ValidationError):
        PasswordResetRequest(email="not-an-address")


def test_a_reset_token_longer_than_the_jwt_floor_is_required():
    """A short value gets a validation error rather than a misleading "expired"."""
    with pytest.raises(ValidationError):
        PasswordResetConfirm(token="short", new_password=COMPLIANT)  # noqa: S106


def test_the_reset_acknowledgement_defaults_to_accepted_with_no_token():
    body = PasswordResetRequested()

    assert body.accepted is True
    assert body.dev_token is None
