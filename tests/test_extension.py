from reprise.extension import ManualBackend, SOURCE_URL


async def test_code_correction_changes_guidance_without_reusing_old_code():
    backend = ManualBackend()
    four = await backend.call("lookup_manual", {
        "device_type": "Samsung washing machine", "error_code": "4C",
    })
    five = await backend.call("lookup_manual", {
        "device_type": "Samsung washing machine", "error_code": "5C",
    })
    assert four["meaning"] == "Water supply issue"
    assert five["meaning"] == "Water drainage issue"
    assert four["steps"] != five["steps"]
    assert four["source_url"] == five["source_url"] == SOURCE_URL
    assert four["model_specific"] is False


async def test_unsupported_code_has_no_invented_steps():
    result = await ManualBackend().call("lookup_manual", {
        "device_type": "Samsung washing machine", "error_code": "XYZ",
    })
    assert result["status"] == "not_found"
    assert "steps" not in result
