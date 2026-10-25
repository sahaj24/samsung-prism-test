"""Resumable fresh FDB-v3 run with a separate native Google judging queue.

Run: python -m reprise.gemma_benchmark --name hardened-gemma-YYYYMMDD
No agent, controller, tools, Workspace integration or upstream files are changed.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from .backend import FDB_COMMIT
from .config import ROOT, Settings, source_digest
from .evaluation import _load_evaluator, execution_confirmed, run_suite
from .gemma_judge import GemmaJudge, JudgeFailure, read_diagnostics
from .metrics import distribution


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def validate_resume(saved, current):
    for key in (
        "source_sha256",
        "settings",
        "selected",
        "input_sha256",
        "metadata_sha256",
        "upstream_commit",
        "judge_model",
        "vad_silence_ms",
        "uv_lock_sha256",
        "upstream_evaluator_sha256",
    ):
        if saved.get(key) != current.get(key):
            raise ValueError(f"Resume configuration changed: {key}")


def validate_judge(judge, output):
    examples = [
        ({"city": "Las Vegas"}, {"city": "Vegas"}, "search", True),
        ({"order_id": "ABC123"}, {"order_id": "XYZ999"}, "track_order", False),
        ({"amount": 100}, {"amount": 103}, "transfer_money", True),
        ({"amount": 100}, {"amount": 200}, "transfer_money", False),
    ]
    results = []
    for expected, actual, function, want in examples:
        record = judge.argument(expected, actual, function)
        results.append(
            {
                "judgment_id": record["judgment_id"],
                "expected_decision": want,
                "actual_decision": record["decision"],
                "model": record["model"],
                "response_model": record.get("response_model"),
                "latency_s": record["latency_s"],
                "usage": record["usage"],
                "passed": record["decision"]["correct"] is want,
            }
        )
    atomic_json(Path(output) / "judge-validation.json", results)
    return results


def evaluate_case(scenario, result, judge):
    upstream = ROOT / "third_party/fdb/v3"
    strict = _load_evaluator(upstream / "evaluate_pass_rate.py", "gemma_strict_scorer")
    tools = _load_evaluator(upstream / "evaluate_tool_calls.py", "gemma_tool_scorer")
    calls = result["actual_tool_calls"]
    expected = scenario["expected_tool_calls"]
    selection = tools.evaluate_tool_selection(expected, calls)
    exact_pass = strict.evaluate_scenario_pass(scenario, calls, use_llm=False)
    exact_args = tools.evaluate_argument_accuracy(expected, calls, use_llm=False)
    remaining = defaultdict(list)
    for call in calls:
        remaining[call["function"]].append(call)
    arguments, scores, errors = [], [], []
    for call in expected:
        function = call["function"]
        if not remaining[function]:
            scores.append(0.0)
            arguments.append(
                {
                    "function": function,
                    "score": 0.0,
                    "reason": "Function not called",
                    "judgment_id": None,
                    "judge_required": False,
                }
            )
            continue
        actual = remaining[function].pop(0)
        try:
            record = judge.argument(call.get("args", {}), actual.get("args", {}), function)
            score = float(record["decision"]["correct"])
            scores.append(score)
            arguments.append(
                {
                    "function": function,
                    "score": score,
                    "judgment_id": record["judgment_id"],
                    "decision": record["decision"],
                    "expected_args": call.get("args", {}),
                    "actual_args": actual.get("args", {}),
                }
            )
        except JudgeFailure as exc:
            scores.append(None)
            errors.append({"kind": "argument", "function": function, "error": str(exc)})
            arguments.append({"function": function, "score": None, "error": str(exc)})
    argument_accuracy = (
        (sum(scores) / len(scores) if scores else 1.0) if None not in scores else None
    )
    response_score, response_record = None, None
    transcript = result.get("transcript", "")
    if transcript.strip():
        try:
            expected_response = scenario.get("dialogue", [{}])[-1].get("ai", "")
            response_record = judge.response(expected_response, transcript)
            response_score = float(response_record["decision"]["correct"])
            response_record = {key: response_record[key] for key in ("judgment_id", "decision")}
        except JudgeFailure as exc:
            errors.append({"kind": "response", "error": str(exc)})
    else:
        response_score = 0.0
        response_record = {
            "judgment_id": None,
            "judge_required": False,
            "decision": None,
            "reason": "No transcript available; upstream score is zero",
        }
    latency = {
        "task_completion_s": None,
        "official_audio_response_latency_s": None,
        "missing_evidence": "No independent timestamped ASR and shared input/output audio clock",
        "trace_metrics": result.get("trace_metrics", {}),
    }
    chunks, user_end = result.get("asr_chunks"), result.get("user_speech_end_rel")
    if chunks and user_end is not None and result.get("audio_clock_aligned") is True:
        try:
            info = judge.key_information(user_end, chunks, calls)
            latency.update(
                task_completion_s=info["decision"]["key_info_start_time"] - user_end,
                key_information_judgment_id=info["judgment_id"],
                missing_evidence=None,
            )
        except JudgeFailure as exc:
            errors.append({"kind": "key_information", "error": str(exc)})
    confirmed = execution_confirmed(calls)
    operational = not result.get("infrastructure_error") and confirmed is True
    selection_pass = not selection["unmatched_expected"] and not selection["unexpected_calls"]
    semantic_passed = (
        None
        if argument_accuracy is None
        else (selection_pass and argument_accuracy == 1 and operational)
    )
    strict_passed = exact_pass["passed"] and operational
    return {
        "sample": result["sample"],
        "scenario_id": scenario["id"],
        "domain": scenario["domain"],
        "difficulty": scenario["difficulty"],
        "strict_passed": strict_passed,
        "strict_label_passed": exact_pass["passed"],
        "strict_checks": exact_pass,
        "strict_argument_accuracy": exact_args["score"],
        "strict_argument_details": exact_args["details"],
        "semantic_passed": semantic_passed,
        "semantic_argument_accuracy": argument_accuracy,
        "spoken_response_accuracy": response_score,
        "spoken_response_source": result.get("transcript_source", "provider output transcription"),
        "spoken_response_judged": bool(response_record and response_record.get("judgment_id")),
        "judge_complete": not errors,
        "judge_errors": errors,
        "tool_selection": selection,
        "judgments": {"arguments": arguments, "response": response_record},
        "infrastructure_error": result.get("infrastructure_error"),
        "execution_confirmed": confirmed,
        "trace_closed": result.get("trace_closed"),
        "latency": latency,
    }


def write_reports(output, manifest, cases):
    output = Path(output)
    selected = manifest["selected"]
    completed = {case["sample"] for case in cases}
    incomplete = [name for name in selected if name not in completed]

    def mean(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    common = {
        "schema_version": 1,
        "selected": len(selected),
        "completed": len(cases),
        "incomplete_cases": incomplete,
        "voice_model": manifest["voice_model"],
        "official_score": None,
        "historical_strict_reference": {
            "passed": 77,
            "selected": 100,
            "note": "Historical run retained; no claim of improvement from incomplete current run",
        },
    }
    judge_label = (
        "FDB-v3 / Gemma 4 31B custom judge"
        if manifest["judge_model"] == "gemma-4-31b-it"
        else "FDB-v3 / Gemma 4 26B A4B custom judge"
        if manifest["judge_model"] == "gemma-4-26b-a4b-it"
        else f"FDB-v3 / {manifest['judge_model']} custom judge"
    )
    for label, key, filename in [
        ("Strict pinned FDB-v3 tool scoring", "strict_passed", "strict-evaluation.json"),
        (judge_label, "semantic_passed", "gemma-semantic-evaluation.json"),
    ]:
        passed = sum(c[key] is True for c in cases)
        breakdown = {}
        for group in ("domain", "difficulty"):
            breakdown[group] = {
                value: {
                    "completed": len(rows),
                    "passed": sum(c[key] is True for c in rows),
                    "unjudged": sum(c[key] is None for c in rows),
                }
                for value, rows in (
                    (value, [c for c in cases if c[group] == value])
                    for value in sorted({c[group] for c in cases})
                )
            }
        report = {
            **common,
            "label": label,
            "judge_model": manifest["judge_model"] if key == "semantic_passed" else None,
            "passed": passed,
            "completed_pass_rate": passed / len(cases) if cases else None,
            "selected_set_pass_rate_missing_as_fail": passed / len(selected),
            "unjudged": sum(c[key] is None for c in cases),
            "breakdown": breakdown,
            "infrastructure_errors": dict(
                Counter(c["infrastructure_error"] for c in cases if c["infrastructure_error"])
            ),
            "judge_errors": sum(len(c["judge_errors"]) for c in cases),
            "argument_accuracy_macro": mean(
                c[
                    "semantic_argument_accuracy"
                    if key == "semantic_passed"
                    else "strict_argument_accuracy"
                ]
                for c in cases
            ),
            "spoken_response_accuracy": mean(c["spoken_response_accuracy"] for c in cases),
            "spoken_responses_judged": sum(c["spoken_response_judged"] for c in cases),
        }
        atomic_json(output / filename, report)
    selections = [c["tool_selection"] for c in cases]
    matched = sum(c["matched"] for c in selections)
    actual = sum(c["total_actual"] for c in selections)
    expected = sum(c["total_expected"] for c in selections)
    precision, recall = matched / actual if actual else 1.0, matched / expected if expected else 1.0
    strict_details = [d for c in cases for d in c["strict_argument_details"]]
    semantic_details = [d for c in cases for d in c["judgments"]["arguments"]]
    categories = Counter()
    for c in cases:
        categories.update(
            {
                "missing_call": len(c["tool_selection"]["unmatched_expected"]),
                "extra_call": len(c["tool_selection"]["unexpected_calls"]),
            }
        )
        for detail in c["strict_argument_details"]:
            if detail["score"] == 0:
                reason = detail.get("explanation", detail.get("reason", ""))
                categories[
                    "missing_argument"
                    if reason.startswith("Missing argument:")
                    else "missing_call_argument_zero"
                    if reason == "Function not called"
                    else "value_mismatch"
                ] += 1
    timings = defaultdict(list)
    for c in cases:
        path = output / "cases" / c["sample"] / "events.jsonl"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["event"] == "tool_executed":
                timings["tool_execution_s"].append(event.get("duration_s"))
            elif event["event"] == "controller_decision":
                timings["controller_gate_s"].append(event.get("gate_duration_s"))
            elif event["event"] == "model_metrics":
                metric = event.get("metrics", {})
                if metric.get("type") == "realtime_model_metrics" and metric.get("ttft", -1) >= 0:
                    timings["sdk_ttft_s"].append(metric["ttft"])
    tools_report = {
        **common,
        "tool_selection": {
            "micro_precision": precision,
            "micro_recall": recall,
            "micro_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
            "macro_precision": mean(c["precision"] for c in selections),
            "macro_recall": mean(c["recall"] for c in selections),
            "macro_f1": mean(c["score"] for c in selections),
            "matched": matched,
            "expected": expected,
            "actual": actual,
        },
        "strict_argument_accuracy_micro": mean(d["score"] for d in strict_details),
        "semantic_argument_accuracy_micro_judged_only": mean(d["score"] for d in semantic_details),
        "semantic_argument_unjudged": sum(d["score"] is None for d in semantic_details),
        "argument_failure_categories": dict(categories),
        "latencies": {name: distribution(values) for name, values in timings.items()},
        "official_audio_response_latency_s": None,
        "task_completion_latency_s": None,
        "latency_limitation": "SDK TTFT and monotonic tool/gate durations only; no aligned independent ASR",
        "billing_visibility": None,
    }
    atomic_json(output / "tool-metrics.json", tools_report)
    atomic_json(output / "case-level-results.json", sorted(cases, key=lambda c: c["sample"]))
    path = output / "judge-diagnostics.jsonl"
    records = read_diagnostics(path)
    usage_keys = (
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "total_token_count",
    )
    atomic_json(
        output / "judge-summary.json",
        {
            "model": manifest["judge_model"],
            "request_count": len(records),
            "successful_requests": sum(not r["error"] for r in records),
            "errors": dict(
                Counter(
                    str(r["error"]["status_code"] or r["error"]["type"])
                    for r in records
                    if r["error"]
                )
            ),
            "rate_limit_errors": sum(
                bool(r["error"] and r["error"]["status_code"] == 429) for r in records
            ),
            "usage": {
                k: sum((r.get("usage") or {}).get(k) or 0 for r in records) for k in usage_keys
            },
            "requests_without_usage": sum(r.get("usage") is None for r in records),
            "request_latency_s": distribution(r["latency_s"] for r in records),
            "fallbacks": 0,
            "unjudged_cases": sum(not c["judge_complete"] for c in cases),
            "scheduling": {
                "rpm": 28,
                "tpm_reserved_input_plus_max_output": 14000,
                "daily_requests": 14400,
            },
        },
    )


async def run(args, *, judge=None, on_gate=None):
    load_dotenv(ROOT / ".env", override=True)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.update(
        GEMINI_MODEL="gemini-3.8-live", REPRISE_MODE="benchmark", GEMINI_AUTH_MODE="api_key"
    )
    for key in (
        "GOOGLE_API_KEY",
        "GOOGLE_API_KEY_2",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ):
        if not os.getenv(key):
            raise ValueError(f"Missing credential: {key}")
    output = ROOT / "runs" / args.name
    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid run name")
    output.mkdir(parents=True, exist_ok=True)
    upstream = ROOT / "third_party/fdb/v3"
    commit = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != FDB_COMMIT:
        raise ValueError("Wrong FDB checkout")
    if subprocess.check_output(
        ["git", "-C", str(upstream), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("Upstream checkout was modified")
    dataset = ROOT / "data/fdb_v3_data_released"
    inputs = sorted(dataset.glob("*/input.wav"))
    if len(inputs) != 100:
        raise ValueError(f"Expected 100 released recordings; found {len(inputs)}")
    settings = Settings.from_env()

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = {
        "created_at": time.time(),
        "run_id": args.name,
        "selected": [p.parent.name for p in inputs],
        "voice_model": settings.model,
        "judge_model": getattr(judge, "model", "gemma-4-31b-it"),
        "source_sha256": source_digest(),
        "settings": asdict(settings),
        "upstream_commit": commit,
        "concurrency": 2,
        "judge_concurrency": 2,
        "input_sha256": {p.parent.name: sha(p) for p in inputs},
        "metadata_sha256": {p.parent.name: sha(p.parent / "metadata.json") for p in inputs},
        "uv_lock_sha256": sha(ROOT / "uv.lock"),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "git_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)
        ).hexdigest(),
        "upstream_evaluator_sha256": {p.name: sha(p) for p in upstream.glob("*.py")},
        "credentials": {"voice": "GOOGLE_API_KEY", "judge": "GOOGLE_API_KEY_2"},
        "mutations": "FDB mocks only; Workspace mode disabled",
        "billing_enabled_by_runner": False,
        "semantic_label": f"FDB-v3 / {getattr(judge, 'model', 'gemma-4-31b-it')} custom judge",
        "state": "validating",
        "vad_silence_ms": int(os.getenv("REPRISE_VAD_SILENCE_MS", "650")),
    }
    config_path = output / "run-configuration.json"
    if config_path.exists():
        saved = json.loads(config_path.read_text())
        validate_resume(saved, manifest)
        manifest = saved
    else:
        atomic_json(config_path, manifest)
        shutil.copytree(
            ROOT / "src/reprise",
            output / "source-snapshot",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    if judge is None:
        judge = GemmaJudge(output)
        validation = await asyncio.to_thread(validate_judge, judge, output)
        if not all(row["passed"] for row in validation):
            raise JudgeFailure("Known-example judge validation failed; inference not started")
    cases = []
    for path in sorted((output / "cases").glob("*/evaluation.json")):
        case = json.loads(path.read_text())
        if case["judge_complete"]:
            cases.append(case)
    completed = {case["sample"] for case in cases}
    worker_log = output / "worker.log"
    log_offset = worker_log.stat().st_size if worker_log.exists() else 0
    log = worker_log.open("a")
    worker = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "reprise.agent", "start", cwd=ROOT, stdout=log, stderr=log
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if worker.returncode is not None:
                raise RuntimeError("Worker exited; inspect worker.log")
            if (
                '"message": "registered worker"'
                in worker_log.read_text(errors="replace")[log_offset:]
            ):
                break
            await asyncio.sleep(0.5)
        else:
            raise TimeoutError("Worker registration timeout")

        async def infer(index, audio):
            if getattr(judge, "failed", False):
                raise JudgeFailure("Shared judge paused after exhausted retries")
            if source_digest() != manifest["source_sha256"]:
                raise RuntimeError("Source changed during run; checkpoint retained")
            sample = audio.parent.name
            target = output / "cases" / sample
            result_path = target / "result_reprise.json"
            if result_path.exists():
                result = json.loads(result_path.read_text())
                if result["infrastructure_error"] is None:
                    return result
                failure_path = (
                    output / "infrastructure-failures" / (sample + "-" + str(time.time_ns()))
                )
                failure_path.parent.mkdir(exist_ok=True)
                shutil.copytree(target, failure_path)
            # Recover completed per-case inference even if export was interrupted.
            for previous in sorted(
                (ROOT / "runs").glob(f"{args.name}-i{index:03d}-r*"), reverse=True
            ):
                path = previous / sample / "result_reprise.json"
                if path.exists():
                    result = json.loads(path.read_text())
                    if result["infrastructure_error"] is None:
                        shutil.copytree(path.parent, target, dirs_exist_ok=True)
                        return result
            generation = len(list((ROOT / "runs").glob(f"{args.name}-i{index:03d}-r*")))
            subargs = SimpleNamespace(
                dataset=str(dataset),
                sample=[sample],
                limit=0,
                seed=20260927,
                name=f"{args.name}-i{index:03d}-r{generation}",
                concurrency=1,
                infrastructure_retries=2,
            )
            directory = await run_suite(subargs)
            shutil.copytree(directory / sample, target, dirs_exist_ok=True)
            return json.loads(result_path.read_text())

        async def evaluate(index, audio):
            result = await infer(index, audio)
            if source_digest() != manifest["source_sha256"]:
                raise RuntimeError("Source changed during run; checkpoint retained")
            scenario = json.loads((audio.parent / "metadata.json").read_text())
            case = await asyncio.to_thread(evaluate_case, scenario, result, judge)
            atomic_json(output / "cases" / case["sample"] / "evaluation.json", case)
            cases[:] = [c for c in cases if c["sample"] != case["sample"]] + [case]
            write_reports(output, manifest, cases)
            print(
                json.dumps(
                    {
                        "completed": len(cases),
                        "selected": 100,
                        "sample": case["sample"],
                        "strict": case["strict_passed"],
                        "semantic": case["semantic_passed"],
                        "response": case["spoken_response_accuracy"],
                        "judge_errors": case["judge_errors"],
                    }
                ),
                flush=True,
            )
            return case

        gate_path = output / "one-case-gate.json"
        if not gate_path.exists():
            pilot = await evaluate(0, inputs[0])
            result = json.loads(
                (output / "cases" / pilot["sample"] / "result_reprise.json").read_text()
            )
            valid = (
                not pilot["infrastructure_error"]
                and pilot["judge_complete"]
                and bool(result["actual_tool_calls"])
                and bool(result.get("transcript", "").strip())
                and pilot["spoken_response_judged"]
            )
            atomic_json(
                output / "one-case-attempt.json",
                {
                    "passed": valid,
                    "case": pilot,
                    "note": "Task failure is allowed; infrastructure/judge failure is not",
                },
            )
            if not valid:
                raise RuntimeError("One-case pipeline gate failed; full run not started")
            atomic_json(
                gate_path, {"passed": True, "sample": pilot["sample"], "checked_at": time.time()}
            )
            completed.add(pilot["sample"])
        if on_gate is not None:
            await on_gate()
        manifest["state"] = "full_run"
        atomic_json(config_path, manifest)
        # Two judge workers share one rate budget and coalesce identical requests.
        queue = asyncio.Queue()
        semaphore = asyncio.Semaphore(2)

        async def produce(index, audio):
            if audio.parent.name in completed:
                return
            async with semaphore:
                result = await infer(index, audio)
                await queue.put((audio, result))

        async def consume():
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    audio, result = item
                    if (
                        source_digest() != manifest["source_sha256"]
                        or result.get("source_unchanged") is not True
                    ):
                        raise RuntimeError("Source changed during run; checkpoint retained")
                    scenario = json.loads((audio.parent / "metadata.json").read_text())
                    case = await asyncio.to_thread(evaluate_case, scenario, result, judge)
                    atomic_json(output / "cases" / case["sample"] / "evaluation.json", case)
                    cases[:] = [c for c in cases if c["sample"] != case["sample"]] + [case]
                    write_reports(output, manifest, cases)
                    print(
                        json.dumps(
                            {
                                "completed": len(cases),
                                "selected": 100,
                                "sample": case["sample"],
                                "strict": case["strict_passed"],
                                "semantic": case["semantic_passed"],
                                "response": case["spoken_response_accuracy"],
                                "judge_errors": len(case["judge_errors"]),
                            }
                        ),
                        flush=True,
                    )
                    if not case["judge_complete"]:
                        raise JudgeFailure("Semantic evaluation incomplete; checkpoint retained")
                finally:
                    queue.task_done()

        consumers = [asyncio.create_task(consume()) for _ in range(2)]

        async def produce_all():
            async with asyncio.TaskGroup() as group:
                for i, p in enumerate(inputs):
                    group.create_task(produce(i, p))
            for _ in consumers:
                await queue.put(None)

        producer = asyncio.create_task(produce_all())
        tasks = [producer, *consumers]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        manifest["state"] = (
            "complete"
            if len(cases) == 100 and all(c["judge_complete"] for c in cases)
            else "incomplete"
        )
        atomic_json(config_path, manifest)
        write_reports(output, manifest, cases)
    finally:
        worker.terminate()
        try:
            await asyncio.wait_for(worker.wait(), 20)
        except asyncio.TimeoutError:
            worker.kill()
            await worker.wait()
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
