import json
import os
from pathlib import Path

import pytest


def test_trace_metrics_keep_outcomes_durations_and_missing_audio_alignment_separate():
    from reprise.metrics import summarize_events

    events = [
        {"event": "tool_raw_proposed", "proposal_id": "p1", "elapsed_s": 0.1},
        {"event": "controller_decision", "gate_duration_s": 0.8},
        {"event": "tool_dispatch", "proposal_id": "p1", "elapsed_s": 1},
        {
            "event": "tool_executed",
            "proposal_id": "p1",
            "outcome": "approval_required",
            "duration_s": 0.1,
        },
        {"event": "tool_superseded"},
        {"event": "duplicate_coalesced"},
        {"event": "external_api_completed", "outcome": "unknown", "duration_s": 0.3},
        {
            "event": "model_metrics",
            "metrics": {
                "type": "realtime_model_metrics",
                "request_id": "sdk-1",
                "ttft": 0.2,
                "input_tokens": 4,
                "output_tokens": 2,
            },
        },
        {"event": "session_usage", "usage": {"tokens": 6}},
    ]
    result = summarize_events(events)
    assert result["tool_outcomes"] == {"approval_required": 1}
    assert result["external_api_outcomes"] == {"unknown": 1}
    assert result["timings"]["tool_duration_s"]["count"] == 1
    assert result["timings"]["tool_duration_s"]["mean"] == pytest.approx(0.1)
    assert result["timings"]["gate_duration_s"]["p95"] == pytest.approx(0.8)
    assert result["timings"]["sdk_ttft_s"]["count"] == 1
    assert result["timings"]["vad_to_assistant_state_s"]["mean"] is None
    assert result["official_audio_alignment_available"] is False
    assert result["sdk_usage_latest"] == {"tokens": 6}
    assert result["event_counts"]["tool_superseded"] == 1
    assert result["billable_cost"] is None


def test_interruption_proxy_uses_matching_speech_state_boundaries():
    from reprise.metrics import summarize_events

    result = summarize_events(
        [
            {"event": "agent_state", "state": "speaking", "elapsed_s": 1},
            {"event": "speech_started", "elapsed_s": 2},
            {"event": "agent_state", "state": "listening", "elapsed_s": 2.15},
            {"event": "speech_ended", "elapsed_s": 3},
            {"event": "agent_state", "state": "speaking", "elapsed_s": 3.4},
        ]
    )
    assert result["user_interruptions_observed"] == 1
    assert result["timings"]["interruption_to_state_stop_s"]["mean"] == pytest.approx(0.15)
    assert result["timings"]["vad_to_assistant_state_s"]["mean"] == pytest.approx(0.4)


async def test_trace_collection_waits_for_inflight_tool_and_marks_closed(monkeypatch):
    import reprise.evaluation as evaluation

    batches = iter(
        [
            [{"event": "tool_dispatch", "proposal_id": "p"}],
            [
                {"event": "tool_dispatch", "proposal_id": "p"},
                {"event": "tool_executed", "proposal_id": "p"},
                {"event": "session_closed"},
            ],
        ]
    )
    monkeypatch.setattr(evaluation, "read_events", lambda room: next(batches))
    events, complete = await evaluation.collect_trace("room", timeout=0.2, interval=0.001)
    assert complete and events[-1]["event"] == "session_closed"


def test_score_is_offline_by_default_and_fails_closed_for_paid_judging(tmp_path, monkeypatch):
    from reprise.evaluation import score_results

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        score_results(tmp_path, benchmark=tmp_path / "labels.json", use_llm=True)


def test_synthetic_scoring_reports_infrastructure_failures_and_both_denominators(tmp_path):
    from reprise.evaluation import score_results

    # Already pinned upstream source, not an old recording or paid/model run.
    upstream = Path(
        os.getenv("FDB_TEST_ROOT", str(Path(__file__).resolve().parents[1] / "third_party/fdb/v3"))
    )
    if not upstream.exists():
        pytest.skip("Pinned upstream fixture not available locally")
    benchmark = tmp_path / "labels.json"
    benchmark.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "synthetic-1",
                        "domain": "test",
                        "difficulty": "easy",
                        "title": "Synthetic only",
                        "dialogue": [{"user": "lookup", "ai": "found"}],
                        "expected_tool_calls": [{"function": "lookup", "args": {"key": "x"}}],
                    }
                ]
            }
        )
    )
    for name, error in [("good", None), ("infra", "model_session_error"), ("uncertain", None)]:
        case = tmp_path / name
        case.mkdir()
        (case / "result_reprise.json").write_text(
            json.dumps(
                {
                    "example_id": "synthetic-1",
                    "transcript": "found",
                    "infrastructure_error": error,
                    "actual_tool_calls": [
                        {
                            "function": "lookup",
                            "args": {"key": "x"},
                            **({"outcome": "unknown"} if name == "uncertain" else {}),
                        }
                    ],
                }
            )
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"selected": ["good", "infra", "uncertain", "missing"]})
    )
    report = score_results(tmp_path, benchmark=benchmark, upstream=upstream)
    assert report["exact_passed"] == 1
    assert report["completed"] == 3 and report["selected"] == 4
    assert report["selected_set_pass_rate_missing_as_fail"] == 0.25
    assert report["completed_pass_rate"] == pytest.approx(1 / 3)
    assert report["execution_outcome_failures"] == 1
    assert report["tool_selection_f1_all"] == 1
    assert report["argument_accuracy_all"] == 1
    assert report["response_quality"] is None
    assert report["audio_latency_samples"] == 0


def test_cli_score_never_enables_judging_just_because_key_exists(tmp_path, monkeypatch):
    from reprise import cli, evaluation

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-key")
    monkeypatch.setattr("sys.argv", ["reprise", "score", str(tmp_path)])
    seen = []

    def score(*args, **kwargs):
        seen.append(kwargs)
        return {"completed": 0, "method": "offline"}

    monkeypatch.setattr(evaluation, "score_results", score)
    cli.main()
    assert len(seen) == 1 and seen[0]["use_llm"] is False
    assert json.loads((tmp_path / "evaluation_exact.json").read_text())["method"] == "offline"


def test_cli_metrics_can_summarize_events_without_cloud_or_upstream(tmp_path, monkeypatch, capsys):
    from reprise import cli

    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"event": "tool_executed", "outcome": "unknown", "duration_s": 0.5}) + "\n"
    )
    monkeypatch.setattr("sys.argv", ["reprise", "metrics", str(path)])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["tool_outcomes"] == {"unknown": 1}


def test_all_does_not_launch_paid_judge_when_key_is_present(tmp_path, monkeypatch):
    from reprise import cli, evaluation

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-key")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["reprise", "all", "--model", "same-gemini-model"])
    monkeypatch.setattr(cli, "setup", lambda **kw: None)

    async def doctor():
        pass

    async def suite(args):
        return tmp_path

    monkeypatch.setattr(cli, "doctor", doctor)
    monkeypatch.setattr(evaluation, "run_suite", suite)

    def forbidden(*a, **kw):
        pytest.fail("No implicit judging subprocess is permitted")

    monkeypatch.setattr(cli.subprocess, "run", forbidden)

    class Worker:
        def __init__(self, *a, **kw):
            kw["stdout"].write('"message": "registered worker"')
            kw["stdout"].flush()

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", Worker)
    cli.main()


def test_worker_configuration_mismatch_is_visible_for_model_comparisons():
    from reprise.config import Settings
    from reprise.evaluation import worker_configuration_errors

    manifest = {"source_sha256": "expected-source"}
    events = [
        {
            "event": "session_started",
            "model": "different-model",
            "policy": "guarded",
            "source_sha256": "different-source",
            "mode": "productivity",
        }
    ]
    errors = worker_configuration_errors(events, Settings(), manifest)
    assert errors == ["model", "mode", "source_sha256"]


def test_dispatched_without_terminal_event_remains_unknown_in_export():
    from reprise.evaluation import calls_from_events

    calls = calls_from_events(
        [
            {
                "event": "tool_dispatch",
                "proposal_id": "p1",
                "function": "lookup",
                "args": {"key": "x"},
                "wall_time": 10,
            },
            {
                "event": "tool_executed",
                "proposal_id": "p1",
                "function": "lookup",
                "args": {"key": "x"},
                "timestamp_start": 10,
                "timestamp_end": 11,
                "outcome": "success",
            },
            {
                "event": "tool_dispatch",
                "proposal_id": "p2",
                "function": "extra",
                "args": {},
                "wall_time": 12,
            },
        ]
    )
    assert len(calls) == 2
    assert calls[1]["function"] == "extra" and calls[1]["outcome"] == "unknown"
    assert calls[1]["timestamp_start"] == 12
    assert "timestamp_end" not in calls[1]


def test_saved_scoring_does_not_resurrect_pass_with_unfinished_extra_dispatch(tmp_path):
    from reprise.evaluation import score_results

    upstream = Path(
        os.getenv("FDB_TEST_ROOT", str(Path(__file__).resolve().parents[1] / "third_party/fdb/v3"))
    )
    if not upstream.exists():
        pytest.skip("Pinned upstream fixture not available locally")
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "synthetic-2",
                        "domain": "test",
                        "difficulty": "easy",
                        "title": "Unfinished dispatch",
                        "dialogue": [{"user": "lookup", "ai": "found"}],
                        "expected_tool_calls": [{"function": "lookup", "args": {"key": "x"}}],
                    }
                ]
            }
        )
    )
    sample = tmp_path / "case"
    sample.mkdir()
    completed = {
        "event": "tool_executed",
        "proposal_id": "p1",
        "function": "lookup",
        "args": {"key": "x"},
        "timestamp_start": 10,
        "timestamp_end": 11,
        "outcome": "success",
    }
    (sample / "result_reprise.json").write_text(
        json.dumps(
            {
                "example_id": "synthetic-2",
                "transcript": "found",
                "trace_closed": False,
                "actual_tool_calls": [completed],
            }
        )
    )
    (sample / "events.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                completed,
                {
                    "event": "tool_dispatch",
                    "proposal_id": "p2",
                    "function": "extra",
                    "args": {},
                    "wall_time": 12,
                },
            ]
        )
        + "\n"
    )
    report = score_results(tmp_path, upstream=upstream, benchmark=labels)
    assert report["exact_passed"] == 0
    assert report["execution_outcome_failures"] == 1
    assert report["results"][0]["metrics"]["actual_calls_count"] == 2


def test_worker_vad_configuration_difference_is_not_hidden_by_same_model_name():
    from reprise.config import Settings
    from reprise.evaluation import worker_configuration_errors
    events = [{'event': 'session_started', 'model': 'gemini-3.8-live', 'policy': 'guarded',
               'source_sha256': 'source', 'mode': 'benchmark', 'vad_silence_ms': 800}]
    manifest = {'source_sha256': 'source', 'vad_silence_ms': 650}
    assert worker_configuration_errors(events, Settings(), manifest) == ['vad_silence_ms']
