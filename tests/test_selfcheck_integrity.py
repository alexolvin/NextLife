"""Test strict selfcheck integrity control (Task K1-052).

Verifies that:
1. All 49 expected checks (C-01 .. C-49) are present and unique.
2. Deliberate removal of any check (e.g., C-27, C-28, C-29, C-31) aborts execution with RuntimeError.
3. Addition of unknown check IDs aborts execution with RuntimeError.
"""

import pytest
from scripts.selfcheck import collect_all_checks, verify_checks_integrity
from scripts.selfcheck_types import CheckDef


def test_collect_all_checks_contains_exactly_49_checks():
    checks = collect_all_checks()
    assert len(checks) == 49, f"Expected 49 checks, got {len(checks)}"
    ids = [c.id for c in checks]
    assert len(set(ids)) == 49, "Duplicate check IDs found!"
    expected_ids = {f"C-{i:02d}" for i in range(1, 50)}
    assert set(ids) == expected_ids, f"Mismatch in expected check IDs: {set(ids) ^ expected_ids}"


def test_verify_checks_integrity_passes_on_full_suite():
    checks = collect_all_checks()
    verify_checks_integrity(checks)


@pytest.mark.parametrize("missing_id", ["C-27", "C-28", "C-29", "C-31", "C-01", "C-48", "C-49"])
def test_deliberate_removal_of_check_raises_runtime_error(missing_id: str):
    checks = collect_all_checks()
    pruned_checks = [c for c in checks if c.id != missing_id]
    assert len(pruned_checks) == 48

    with pytest.raises(RuntimeError) as exc_info:
        verify_checks_integrity(pruned_checks)

    err_msg = str(exc_info.value)
    assert "Selfcheck integrity failure" in err_msg
    assert f"missing=['{missing_id}']" in err_msg


def test_extra_check_raises_runtime_error():
    checks = collect_all_checks()
    extra = CheckDef(
        id="C-99",
        category="Test",
        title="Extra Check",
        description="Fail",
        runner=lambda ctx, art: {},
        required_artifacts=[],
        validator=None,
    )
    with pytest.raises(RuntimeError) as exc_info:
        verify_checks_integrity(checks + [extra])

    err_msg = str(exc_info.value)
    assert "Selfcheck integrity failure" in err_msg
    assert "extra=['C-99']" in err_msg
