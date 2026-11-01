from nextlife.media.vlm_check import (
    verify_master_against_original_vlm,
)


def test_vlm_check_blocked_external_when_endpoint_null(tmp_path):
    """DoD: Without endpoint from config, status is BLOCKED-EXTERNAL."""
    fake_conf = tmp_path / "confirmations_null.yaml"
    fake_conf.write_text("models:\n  vlm_endpoint: null\n", encoding="utf-8")

    res = verify_master_against_original_vlm(
        original_path="/tmp/orig.jpg",
        master_path="/tmp/master.png",
        confirmations_path=fake_conf,
    )
    assert res.status == "BLOCKED-EXTERNAL"
    assert "blocked externally" in res.reason.lower()


def test_vlm_check_on_recorded_retouched_defect():
    """DoD: On recorded responses, retouched defect is rejected."""
    recorded_defect_retouched = {
        "defect_retouched": True,
        "confidence": 0.94,
        "region": {"x": 120, "y": 240, "w": 50, "h": 50},
        "description": "Scratch visible in original was blurred or airbrushed out in master.",
    }

    res = verify_master_against_original_vlm(
        original_path="/tmp/orig.jpg",
        master_path="/tmp/master.png",
        recorded_response=recorded_defect_retouched,
    )
    assert res.status == "REJECTED"
    assert res.verdict == "DEFECT_RETOUCHED"
    assert res.defect_retouched is True
    assert "retouching/concealment" in res.reason


def test_vlm_check_on_recorded_authentic_master():
    """Verify authentic master with preserved defects passes verification."""
    recorded_authentic = {
        "defect_retouched": False,
        "confidence": 0.98,
        "description": "All physical features and declared scratches are preserved.",
    }

    res = verify_master_against_original_vlm(
        original_path="/tmp/orig.jpg",
        master_path="/tmp/master.png",
        recorded_response=recorded_authentic,
    )
    assert res.status == "PASSED"
    assert res.verdict == "INTEGRITY_VERIFIED"
    assert res.defect_retouched is False
