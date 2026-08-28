from lingjing_harness.runtime.verifier import ResultVerifier


def _critic():
    return {
        "ready": True,
        "evidence_coverage": 1.0,
        "terminal_coverage": 1.0,
        "unresolved_contradictions": [],
    }


def test_external_evidence_alone_cannot_satisfy_final_evidence_floor():
    result = ResultVerifier.final(
        [{"tool": "web.research", "status": "completed", "result": {}}],
        [],
        [
            {
                "kind": "external",
                "title": "public source",
                "detail": "untrusted external observation",
                "url": "https://example.com/source",
            }
        ],
        allow_adaptation=False,
        critic=_critic(),
    )

    assert result["passed"] is False
    assert result["checks"]["evidence_backed"] is False
    assert result["checks"]["external_evidence_bounded"] is False
    assert result["evidence_provenance"] == {
        "local": 0,
        "external": 1,
        "completed_audits": 0,
        "external_only": True,
    }


def test_external_evidence_can_supplement_owned_result_evidence():
    result = ResultVerifier.final(
        [
            {"tool": "search.run", "status": "completed", "result": {}},
            {"tool": "web.research", "status": "completed", "result": {}},
        ],
        [],
        [
            {"kind": "result", "title": "owned result", "detail": "rank 1"},
            {"kind": "external", "title": "public source", "detail": "context"},
        ],
        allow_adaptation=False,
        critic=_critic(),
    )

    assert result["passed"] is True
    assert result["checks"]["evidence_backed"] is True
    assert result["checks"]["external_evidence_bounded"] is True
    assert result["evidence_provenance"]["external_only"] is False


def test_completed_owned_audit_satisfies_evidence_floor_without_result_rows():
    result = ResultVerifier.final(
        [{"tool": "search.audit", "status": "completed", "result": {"queries": 8}}],
        [],
        [],
        allow_adaptation=False,
        critic=_critic(),
    )

    assert result["passed"] is True
    assert result["evidence_provenance"]["completed_audits"] == 1


def test_failed_audit_is_not_counted_as_evidence():
    result = ResultVerifier.final(
        [{"tool": "search.audit", "status": "failed", "result": {}}],
        [],
        [],
        allow_adaptation=False,
        critic=_critic(),
    )

    assert result["passed"] is False
    assert result["checks"]["no_failed_tools"] is False
    assert result["checks"]["evidence_backed"] is False
    assert result["evidence_provenance"]["completed_audits"] == 0
