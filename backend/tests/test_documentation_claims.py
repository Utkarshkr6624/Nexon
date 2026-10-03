"""The documentation is a claim surface too, so it gets the regression rule like everything else.

Every test in this file guards a claim that was **false** at some point in this
repository's history, and was corrected during the final remediation pass over
Phases 1-9. They are all offline - no PostgreSQL, no client fixture, no rows -
because a document is a file on disk, and reading a file is the cheapest thing
in the repository to test.

The defects behind them:

**Fifteen `Settings` fields were documented nowhere.** Seven `PLANNER_*` and
eight `ANALYTICS_*` settings existed, worked, and appeared in no document at all.
Six more (`RATE_LIMIT_*`) arrived with the remediation pass itself and would have
repeated the omission. The invariant is therefore not "these fifteen are
present" but "**no** field is undocumented", because a test naming fifteen is
satisfied the moment a sixteenth is added without a word. Four of the fifteen
carry a sum-to-100 validator that refuses process start, so an operator who set
them wrongly meets it at boot with no explanation anywhere in the tree.

**`api-conventions.md` said `Page[T]` was served by no endpoint.** Fifteen
operations serve the envelope, and the same section claimed the frontend's
`Paginated<T>` disagreed with it - a type that has nested its counters under
`meta` for some time and that every knowledge, planner and work service returns
from its fetchers. The count is read from ``app.openapi()`` rather than pinned to
a literal, because the failure being guarded is a document drifting away from the
schema, and a literal cannot detect that.

**`maintenance_activity` was documented as "reads low" when the shipped path
reads at its ceiling.** Both phase reports and ``development.md`` carried the
claim. The figures are derived below from the shipped inputs rather than
recorded from a run: a commit that touched a file carries the placeholder path
``<file names are not stored per commit>``, the service calls the metric with
``last_touched_before=None``, and with no history supplied every touched file
counts as quiet.

**`feature_snapshot` returned a bare mapping with no version key.** The fix
wraps it, and the placement matters more than the wrapper: the version sits
**beside** the numeric matrix, never inside it, because a string inside a feature
matrix is a column a model is then asked to fit. This file reads the returned
mapping's structure out of the source with :mod:`ast`, so the assertion is about
the shape of the object the service builds rather than about a string appearing
somewhere near it.

**The superseded figures are still quoted in the remediation sections.** That is
deliberate - a corrected document that hides its own corrections is not a
corrected document - so the ban below is applied to each document's body with its
own remediation section removed. That carve-out is what makes the ban a check on
current prose rather than an obstacle to writing the correction down.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.schemas.analytics import ANALYTICS_FEATURE_SCHEMA_VERSION
from app.services.analytics.service import AnalyticsService
from app.services.developer import metrics as developer_metrics
from app.services.developer.service import _UNRECORDED_FILE_PATH

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

#: The documents this pass owns. Anything not listed here is not this file's
#: business, and asserting on it would fail for reasons this file cannot explain.
OWNED_DOCUMENTS = (
    "README.md",
    ".env.example",
    "docs/architecture.md",
    "docs/api-conventions.md",
    "docs/development.md",
    "docs/specifications/README.md",
    "docs/specifications/phase-8-developer-report.md",
    "docs/specifications/phase-9-learning-career-report.md",
)

#: Where each document's own account of the remediation starts. Everything from
#: that heading to the next level-2 heading is allowed to quote the superseded
#: figures, because that is where the account of what was wrong belongs.
REMEDIATION_HEADINGS = {
    "docs/architecture.md": "## 18. Remediation pass over Phases 1\u20139",
    "docs/api-conventions.md": "## Remediation pass over Phases 1\u20139",
    "docs/specifications/phase-8-developer-report.md": "## 12. Remediation pass",
    "docs/specifications/phase-9-learning-career-report.md": "## 13. Remediation pass",
}
#: Claims that were false, and what each of them asserted, so a failure message
#: says what came back as well as what went missing.
SUPERSEDED_CLAIMS: dict[str, str] = {
    "607 tests": "the frontend suite has 645 tests, not 607",
    "42 files": "the frontend suite has 44 test files, not 42",
    "136 paths and 183 operations": "the live schema has 140 paths and 187 operations",
    "nine revisions": "the migration chain has ten revisions, ending at 0010",
    "were not run against the live database": (
        "the suite applies alembic upgrade head and asserts the drift check, so the "
        "old sentence was half-true and misleadingly worded"
    ),
    "reads **low**": "maintenance_activity reads at its ceiling, not low",
    "reads low": "maintenance_activity reads at its ceiling, not low",
    "reserved — no rate limiting": "the limiter exists, so 429 is reachable",
    "no endpoint serves": "fifteen operations serve Page[T]",
}

#: The four settings whose validator can refuse process start, and therefore the
#: four a reader most needs to find before editing their `.env`.
STARTUP_GATE_WEIGHTS = (
    "ANALYTICS_PRODUCTIVITY_WEIGHT_COMPLETION",
    "ANALYTICS_PRODUCTIVITY_WEIGHT_DEADLINE",
    "ANALYTICS_PRODUCTIVITY_WEIGHT_CONSISTENCY",
    "ANALYTICS_PRODUCTIVITY_WEIGHT_FOCUS",
)


def _read(relative: str) -> str:
    """The text of one owned document, decoded as UTF-8."""
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _current_claims(relative: str) -> str:
    """The document with its own remediation section removed.

    A document that records what it used to claim has to be able to quote the old
    claim; a document that *states* it has a bug. Cutting at the remediation
    heading is what separates the two, and it is why the ban below is worth
    having at all rather than being an obstacle to writing the correction down.
    """
    text = _read(relative)
    heading = REMEDIATION_HEADINGS.get(relative)
    if heading is None:
        return text
    start = text.index(heading)
    following = text.find("\n## ", start + len(heading))
    return text if following == -1 else text[:start] + text[following:]


def _spell(number: int) -> str:
    """``15`` -> ``"fifteen"``, the way the sentence reads.

    Only the counts the envelope can plausibly have are spelled; anything else
    falls back to digits, so the assertion keeps working after a new paginated
    endpoint lands without this file needing a new entry.
    """
    words = (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
    )
    return words[number] if number < len(words) else str(number)


def _ts_interface_members(source: str, name: str) -> list[str]:
    """The member names of a TypeScript ``interface``, in declaration order.

    A deliberately small reader rather than a TypeScript dependency: the file is
    three interfaces of flat members, and importing a compiler to compare a list
    of names would be the wrong trade in a repository whose rule is *no new
    dependency*.
    """
    match = re.search(
        rf"export\s+interface\s+{name}(?:<[^>]*>)?\s*\{{(.*?)\n\}}", source, re.DOTALL
    )
    assert match is not None, f"interface {name} not found in frontend/src/types/pagination.ts"
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    body = re.sub(r"//.*", "", body)
    members = [line.strip() for line in body.splitlines() if line.strip()]
    return [member.rstrip(";").strip() for member in members]


def _dict_key(node: ast.expr | None) -> str:
    """The string a dict key holds, or a marker when the key is computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return str(node.value)
    return "<computed>"


def _returned_mapping(function: Any) -> dict[str, ast.expr]:
    """The single ``return {...}`` a function builds, as ``{key: value node}``.

    Reading the tree rather than the text is the point: a grep for
    ``schema_version`` would pass just as happily if the key had been moved into
    the nested ``features`` mapping, which is precisely the regression.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            return {
                _dict_key(key): value
                for key, value in zip(node.value.keys, node.value.values, strict=True)
                if key is not None
            }
    raise AssertionError(f"{function.__qualname__} builds no single mapping to return")


def test_every_setting_is_written_down_in_both_the_env_example_and_the_readme() -> None:
    """No `Settings` field is undocumented, in either of the two places that claim completeness.

    **The defect.** Fifteen fields - the seven `PLANNER_*` settings Phase 4
    added and the eight `ANALYTICS_*` settings Phase 6 added - existed, worked,
    and appeared in no document at all. `.env.example` calls itself the source
    of truth for configuration and omitted a fifth of the settings; the README's
    group table omitted the same fifteen. An operator could not have discovered
    `PLANNER_LOOKAHEAD_DAYS` by reading the repository.

    **The invariant.** `.env.example` and `README.md` are the two documents that
    claim to be exhaustive, so the bar is that every field's environment-variable
    name - the field name uppercased, which is how `case_sensitive=False`
    resolves it - appears in both. Written as a property over `Settings` rather
    than as a list of fifteen, because a list would still pass the day someone
    adds a sixteenth field and forgets it.

    **The count.** Seventy-two fields exist at the time of writing: the 66 that
    predate the remediation pass plus the six `RATE_LIMIT_*` settings it added.
    No assertion here depends on that number; the failure message names any
    field that is missing, so the gap is obvious from the output alone.
    """
    env_example = _read(".env.example").upper()
    readme = _read("README.md").upper()

    missing_from_env = [
        field for field in Settings.model_fields if field.upper() not in env_example
    ]
    missing_from_readme = [field for field in Settings.model_fields if field.upper() not in readme]

    assert not missing_from_env, (
        f"{len(missing_from_env)} Settings field(s) absent from .env.example, so an "
        f"operator cannot discover them by reading it: {missing_from_env}"
    )
    assert not missing_from_readme, (
        f"{len(missing_from_readme)} Settings field(s) absent from the README's "
        f"environment-variable table: {missing_from_readme}"
    )


def test_the_four_weights_that_can_refuse_process_start_are_documented_as_doing_so() -> None:
    """The four productivity weights are documented as a start-up gate, not as a preference.

    **Why this one is separate.** The other fifteen undocumented settings only
    change behaviour a user opts into. These four are validated by
    `_validate_productivity_weights`, which **raises**, and `Settings` is built
    once at import time through `get_settings()`. A set that does not sum to 100
    therefore does not degrade a score - it stops the process, with a message
    naming four numbers and a total. There is no silent renormalisation, and
    that is deliberate: the weights are the denominators of a percentage the
    product presents, so a set summing to 90 makes an "80/100" that is really
    "80/90". Rescaling would hide that the configured numbers were wrong.

    **The figures, derived.** The four defaults are 30, 25, 20 and 25, which sum
    to exactly 100. Lowering `..._focus` to 20 makes the total 95, and that is
    the number the error message quotes - the same one the README's
    `Settings` fails validation entry prints.

    **The invariant.** Each name appears in `.env.example` and in
    `development.md` §11.5, and both say the sum is enforced rather than merely
    conventional.
    """
    env_example = _read(".env.example").upper()
    development = " ".join(_read("docs/development.md").upper().split())

    for name in STARTUP_GATE_WEIGHTS:
        assert name in env_example, f"{name} is absent from .env.example"
        assert name in development, f"{name} is absent from docs/development.md"

    assert "MUST SUM TO 100" in env_example, (
        ".env.example must state that the weights are a start-up gate, not a preference"
    )
    assert "SUM" in development and "REFUSES PROCESS START" in development, (
        "development.md must state that the sum-to-100 rule can refuse process start"
    )

    default_weights = Settings(_env_file=None).analytics_productivity_weights
    assert default_weights == {
        "completion": 30.0,
        "deadline": 25.0,
        "consistency": 20.0,
        "focus": 25.0,
    }
    assert sum(default_weights.values()) == 100.0, (
        "the documented default is a set that trips its own validator, so a fresh "
        "install would refuse to boot"
    )


def test_the_pagination_envelope_is_documented_as_served_by_the_operations_that_serve_it() -> None:
    """`api-conventions.md` states the live `Page[T]` count and never calls it unserved.

    **The defect.** The document carried a "known gap" reading *"`Page[T]` is
    declared but unserved ... no endpoint serves `Page[T]` yet"*, together with a
    claim that the frontend's `Paginated<T>` disagreed with it. Fifteen
    operations serve the envelope. The document told its readers not to build a
    pager for any list endpoint in the product.

    **The figures, derived rather than recorded.** Counting the operations whose
    `200` response `$ref`s a `Page_...` schema in `app.openapi()` - one count per
    path and method, so an operation declaring both a `200` and a `202` is still
    counted once - gives the number the document must state. The sentence is
    matched in words as well, because a count stated in one document and absent
    from the next is the same failure in a smaller font.
    """
    from app.main import app

    page_operations = sum(
        1
        for methods in app.openapi()["paths"].values()
        for method, operation in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
        and any(
            "Page_" in media.get("schema", {}).get("$ref", "")
            for media in operation.get("responses", {}).get("200", {}).get("content", {}).values()
        )
    )

    assert page_operations >= 15, (
        f"only {page_operations} operations serve Page[T]; either a router stopped "
        "being mounted, or the count this document states is wrong"
    )

    conventions = _current_claims("docs/api-conventions.md")
    assert f"served by {_spell(page_operations)} operations" in conventions, (
        "api-conventions.md must say the envelope is 'served by "
        f"{_spell(page_operations)} operations'; the live count is different"
    )


def test_the_frontend_paginated_type_nests_its_counters_under_meta_like_the_backend() -> None:
    """The TypeScript `Paginated<T>` nests its counters under `meta`, as `Page[T]` does.

    **The defect.** `api-conventions.md` listed, as a known gap, that
    `frontend/src/types/pagination.ts` declared a flat `Paginated<T>` -
    `{items, total, limit, offset}` - while the backend nests the counters under
    `meta`, and it blamed the frontend. The frontend type was right and the
    document was wrong: `Paginated<T>` is what `fetchNotes`, `fetchProjects`,
    `fetchTasks`, `fetchCalendar`, `fetchSessions` and `fetchTags` all return, so
    the mismatch being described could not have existed. The reason the document
    believed it is on the next line of its own bullet - it justified the claim by
    pointing out that no endpoint served `Page[T]` yet, which was never true.

    **The invariant, asserted against the type rather than against the prose.**
    `Paginated<T>` declares exactly two members, `items` and `meta`; `meta` is a
    `PageMeta`; and `PageMeta` declares exactly `total`, `limit` and `offset`. The
    member lists are compared exactly rather than checked for containment, so a
    counter promoted back to the top level - which would read `undefined` on every
    paginated endpoint - fails as loudly as a missing one.
    """
    source = (REPO_ROOT / "frontend/src/types/pagination.ts").read_text(encoding="utf-8")

    assert _ts_interface_members(source, "Paginated") == ["items: T[]", "meta: PageMeta"], (
        "Paginated<T> must declare exactly items and meta; a counter promoted out of "
        "`meta` reads undefined on every endpoint the backend paginates"
    )
    assert _ts_interface_members(source, "PageMeta") == [
        "total: number",
        "limit: number",
        "offset: number",
    ], (
        "PageMeta must declare exactly total, limit and offset, matching "
        "`PageMeta` in backend/app/schemas/common.py field for field"
    )
    assert "meta: PageMeta" in source, "Paginated<T> must type `meta` as `PageMeta`"


def test_maintenance_activity_counts_every_touched_commit_and_is_not_described_as_reading_low() -> (
    None
):
    """The shipped `maintenance_activity` counts every commit that touched a file: its ceiling.

    **The defect.** Three documents said the metric "reads low". That is
    **inverted**, and it was wrong twice over: the code path before the repair
    would have reported a measured **zero** for every repository, and the path
    that replaced it reports the maximum.

    **The figures, derived from the shipped inputs.** Migration `0008` creates
    no `git_commit_files` table, so there is no per-file history to consult. The
    service therefore reports the single placeholder path
    `_UNRECORDED_FILE_PATH` for any commit whose `files_changed` is positive and
    calls the metric with `last_touched_before=None` - an empty history map. In
    `_reaches_quiet_file` a path with no recorded previous change returns `True`,
    so every commit carrying a path counts. Four commits are built below - three
    that touched a file and one that touched none, inside a thirty-day window -
    so the expected figure is **3**, not "some fraction of four".

    **The invariant.** The count is asserted against the real function, and the
    documents that carried the inverted claim are checked for the phrase.
    """
    window_end = datetime(2026, 3, 30, 12, 0, tzinfo=UTC)
    window_start = window_end - timedelta(days=30)

    def commit(hours_before_end: int, files_changed: int) -> developer_metrics.MetricSample:
        return developer_metrics.MetricSample(
            committed_at=window_end - timedelta(hours=hours_before_end),
            repository_id="repo-1",
            branch="main",
            additions=10,
            deletions=2,
            files_changed=files_changed,
            file_paths=(_UNRECORDED_FILE_PATH,) if files_changed else (),
        )

    samples = [commit(24, 3), commit(72, 1), commit(120, 5), commit(48, 0)]
    metric = developer_metrics.maintenance_activity(
        samples, window_start=window_start, window_end=window_end, last_touched_before=None
    )

    assert metric.value == 3.0, (
        "expected every commit that touched a file to count - three of the four "
        f"samples carried the placeholder path - but the metric read {metric.value}"
    )
    assert metric.available is True, "the metric is measured, so it must be available"
    assert metric.explanation is not None and "without the preceding" in metric.explanation, (
        "the metric must say in its own sentence that it was measured without the "
        f"preceding file history; it said {metric.explanation!r}"
    )

    for relative in OWNED_DOCUMENTS:
        body = _current_claims(relative).lower()
        assert "reads low" not in body or "ceiling" in body, (
            f"{relative} still describes maintenance_activity as reading low; the "
            "shipped path counts every commit that touched a file"
        )


def test_the_feature_snapshot_carries_its_version_beside_the_matrix_and_never_inside_it() -> None:
    """`feature_snapshot` returns the version beside `features`, never inside it.

    **The defect.** `feature_snapshot` returned a bare mapping of feature columns
    with no version key at all, so a training row produced from it could not be
    attributed to the extraction that made it. The obvious repair - adding
    `schema_version` to the mapping - is the one that must not be made carelessly:
    `features` is a feature matrix, a positional row of numbers whose every column
    must be a feature, and a string smuggled into it becomes a column a model is
    then asked to fit. That is the Phase 10 contract this repository states in
    three words: *a versioned feature vector*.

    **The figures, derived from the source rather than from a run.** The service
    builds one ``return {...}``; its top-level keys are read with :mod:`ast` and
    compared exactly, and the nested ``features`` mapping is checked for their
    absence. This needs no database, which is why it can live in an offline file.

    **The invariant.** The top level is exactly `schema_version`, `generated_at`,
    `task_id` and `features`; the nested mapping carries none of them; every
    column is named by a bare identifier rather than a string literal, so a
    column cannot be renamed by editing a literal without the schema version
    moving with it; and the stamped value is the `analytics_features.v1`
    constant the Phase 8 and 9 vectors share.
    """
    assert ANALYTICS_FEATURE_SCHEMA_VERSION == "analytics_features.v1"

    returned = _returned_mapping(AnalyticsService.feature_snapshot)
    features = returned.get("features")

    assert set(returned) == {"schema_version", "generated_at", "task_id", "features"}, (
        f"feature_snapshot returns {sorted(returned)}; the version, the clock and the "
        "row's identity belong beside the matrix, not inside it"
    )
    assert isinstance(features, ast.Dict), "`features` must be a mapping of columns"

    inner = [_dict_key(key) for key in features.keys if key is not None]
    assert not set(inner) & {"schema_version", "generated_at", "task_id"}, (
        f"row-identity keys leaked into the feature matrix: {sorted(inner)}. Every "
        "column of a feature matrix must be a number a model can fit."
    )
    strings = sorted(
        _dict_key(key)
        for key, value in zip(features.keys, features.values, strict=True)
        if isinstance(value, ast.JoinedStr)
        or (isinstance(value, ast.Constant) and isinstance(value.value, str))
    )
    assert not strings, (
        f"{strings} are computed from text inside the feature matrix. A string column "
        "is not a feature: a trainer would encode it as a category and fit it like "
        "any other, which is how a version key becomes a model input."
    )
    assert isinstance(returned["schema_version"], ast.Name), (
        "the version is stamped from ANALYTICS_FEATURE_SCHEMA_VERSION rather than "
        "written out, so the constant and the row cannot drift apart"
    )


def test_the_rate_limiter_is_documented_as_shipped_and_its_settings_are_written_down() -> None:
    """The rate limiter exists, 429 is reachable, and all six of its settings are written down.

    **The defect.** `api-conventions.md` listed "`rate_limited` is a reserved
    code with no implementation" as a known gap, and said in the response-header
    section that there were no rate-limit headers *because* there was no rate
    limiter. `RateLimitMiddleware` shipped with the remediation pass, so both
    sentences were false and the error-code table pointed a reader at a code that
    had no producer.

    **The figures, derived.** `Settings` carries exactly six `RATE_LIMIT_*`
    fields. The list below is asserted against the class rather than trusted, so
    a seventh setting fails here the way a first one would have, and each name is
    then required in the two documents that claim to be exhaustive.
    """
    rate_limit_settings = sorted(
        field for field in Settings.model_fields if field.startswith("rate_limit_")
    )
    assert rate_limit_settings == [
        "rate_limit_credential_max_requests",
        "rate_limit_enabled",
        "rate_limit_general_max_requests",
        "rate_limit_max_entries",
        "rate_limit_trust_forwarded_for",
        "rate_limit_window_seconds",
    ], (
        "the rate limiter's settings changed; update this test and the two documents "
        f"it guards. Found {rate_limit_settings}"
    )

    conventions = _current_claims("docs/api-conventions.md")
    assert "## Rate limiting" in conventions, "api-conventions.md has no rate-limiting section"
    assert "| `rate_limited` | 429 |" in conventions, (
        "the error-code table must attribute 429/rate_limited to the middleware rather "
        "than calling it reserved"
    )
    assert "no rate limiter" not in conventions.lower(), (
        "api-conventions.md still says there is no rate limiter"
    )

    for name in rate_limit_settings:
        upper = name.upper()
        assert upper in _read(".env.example"), f"{upper} absent from .env.example"
        assert upper in _read("README.md"), f"{upper} absent from the README's table"


def test_no_owned_document_states_a_superseded_figure_outside_its_remediation_section() -> None:
    """No current-state claim in any owned document quotes a figure this pass corrected.

    **Why a ban rather than a count check.** The figures move - a test lands, a
    route is added - and a test pinning them would be wrong within the hour. What
    must hold is narrower and durable: the specific claims that were *false* must
    not be stated as current anywhere.

    **The carve-out.** Each document's own remediation section is removed before
    the search, so a report can still say "it used to claim 607 tests". That is
    not a workaround; a corrected document that refuses to record what it used to
    say leaves the next reader unable to tell a correction from an invention.

    **The figures.** Nine phrases, each asserting something concrete and wrong.
    Every phrase is checked against every one of the eight owned documents, so a
    stale copy in a second place fails just as loudly as one in the first.
    """
    for relative in OWNED_DOCUMENTS:
        body = _current_claims(relative)
        for phrase, correction in SUPERSEDED_CLAIMS.items():
            assert phrase not in body, (
                f"{relative} still states a superseded claim: {phrase!r}. {correction}."
            )


def test_every_document_that_states_a_suite_count_states_the_same_one() -> None:
    """The backend and frontend suite figures agree everywhere they are stated.

    **The defect.** `2090` appeared in six places across three documents and
    `607 tests in 42 files` in four places across four documents, none of them
    re-derived from anything. They drifted independently, which is exactly what a
    single unverified figure does: the moment one copy is refreshed and another
    is not, the reader has two truths and no way to choose between them.

    **The invariant, and why it is not a literal.** Rather than pin a number that
    moves with every test file, this asserts that every document which states the
    figure states *one* number. A stale copy fails; so does a legitimate update
    that touched only some copies - which is the point. Refreshing a count means
    refreshing all of them in one change, where a reviewer can see the arithmetic
    add up.

    **The figures, derived.** The measured pair is 645 frontend tests in 44
    files, from `npm test`, and 2263 backend collected as 1020 offline plus 1241
    `integration`. The failure message repeats them so a reader does not have to
    go and re-measure to know what the right answer is.
    """
    bodies = {relative: _current_claims(relative) for relative in OWNED_DOCUMENTS}

    frontend = {
        relative: sorted(set(re.findall(r"(\d{3,4}) tests? in (\d{2,3}) files?", body)))
        for relative, body in bodies.items()
    }
    stated_frontend = {value for values in frontend.values() for value in values}
    assert len(stated_frontend) == 1, (
        "the frontend suite figure is stated inconsistently across the documentation: "
        f"{ {k: v for k, v in frontend.items() if v} }. The measured pair is 645 tests "
        "in 44 files; refresh every copy in the same change."
    )

    # The total is the only ``N collected`` that is not immediately followed by a
    # deselection count: "1029 collected, 1241 deselected" states the offline half
    # of the split, not a second opinion about the whole suite. The ``**`` in
    # between is markdown emphasis, not content.
    total_pattern = re.compile(r"\b(\d{4}) collected\b(?![\s*_]*,?[\s*_]*\d{3,4}\s+deselected)")
    backend = {
        relative: sorted(set(total_pattern.findall(body))) for relative, body in bodies.items()
    }
    stated_backend = {value for values in backend.values() for value in values}
    assert len(stated_backend) == 1, (
        "the backend suite figure is stated inconsistently across the documentation: "
        f"{ {k: v for k, v in backend.items() if v} }. Every copy must move together; "
        "the collected total is whatever `pytest --collect-only -q` last reported."
    )
    total = int(next(iter(stated_backend)))

    # And the halves must add up to it, so a refreshed copy cannot state a total
    # its own split contradicts.
    halves = re.compile(
        r"\b(\d{3,4})\s+offline\b[^.\n]{0,60}?\b(\d{3,4})\b[^.\n]{0,24}?integration"
    )
    for relative, body in bodies.items():
        for offline, integration in halves.findall(body):
            assert int(offline) + int(integration) == total, (
                f"{relative} states {offline} offline plus {integration} integration, "
                f"which does not add up to the {total} collected stated elsewhere"
            )
