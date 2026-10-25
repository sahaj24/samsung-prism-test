"""Scripted virtual-time policy comparison. No model, speech, network or real writes.

wait_complete defers proposals until the final correction; eager uses baseline admission;
selective uses the production guarded Coordinator. Every simulated API call stays in traces.
"""

import asyncio
import heapq
import itertools
import json

import pytest
from test_coordinator import make
from test_selective_recovery import EVENT, FLIGHT, TASK


class Clock:
    def __init__(self):
        self.now = 0
        self.queue = []
        self.counter = itertools.count()

    def at(self, when, callback):
        heapq.heappush(self.queue, (when, next(self.counter), callback))

    async def sleep(self, duration):
        future = asyncio.get_running_loop().create_future()
        self.at(self.now + duration, lambda: None if future.done() else future.set_result(None))
        await future

    async def run(self):
        while self.queue:
            self.now = self.queue[0][0]
            while self.queue and self.queue[0][0] == self.now:
                _, _, callback = heapq.heappop(self.queue)
                callback()
            # Drain ready controller tasks before advancing the virtual backend clock.
            for _ in range(64):
                await asyncio.sleep(0)


class Service:
    def __init__(self, clock):
        self.clock, self.calls, self.completed = clock, [], []

    async def prepare(self, name):
        if name == "calendar_create":
            await self.clock.sleep(3)
        elif name == "task_create":
            await self.clock.sleep(1)

    async def call(self, name, args):
        self.calls.append({"name": name, "args": dict(args), "at": self.clock.now})
        if name == "flight_lookup":
            await self.clock.sleep(4)
        self.completed.append({"name": name, "args": dict(args), "at": self.clock.now})
        return {"status": "success", "items": [{"flight_id": "fixture-only"}]}


SCENARIOS = {
    "read_interrupted_by_time_correction": {
        "times": [0, 1],
        "texts": ["Find my flight and schedule coffee at five", "Actually coffee at six"],
        "values": ["five", "six"],
        "task": False,
    },
    "successive_corrections_and_parallel_task": {
        "times": [0, 1, 2],
        "texts": [
            "Find my flight and schedule coffee at five and add task collect fabric",
            "Actually coffee at six",
            "Actually coffee at seven",
        ],
        "values": ["five", "six", "seven"],
        "task": True,
    },
    "undecided_alternatives": {
        "times": [0],
        "texts": ["Find flight to Oslo or Madrid, I have not decided"],
        "values": [],
        "task": False,
    },
}


async def simulate(tmp_path, strategy, scenario):
    data = SCENARIOS[scenario]
    clock = Clock()
    backend = Service(clock)
    c = make(tmp_path, backend, policy="baseline" if strategy == "eager" else "guarded")
    jobs = []

    def submit(index):
        text = " ".join(data["texts"]) if strategy == "wait_complete" else data["texts"][index]
        c.observe_transcript(text)
        if scenario == "undecided_alternatives":
            calls = [(FLIGHT, {"query": city}) for city in ["Oslo", "Madrid"]]
        else:
            calls = [
                (FLIGHT, {"query": "my flight"}),
                (EVENT, {"title": "coffee", "time": data["values"][index]}),
            ]
            if data["task"] and (index == 0 or strategy == "wait_complete"):
                calls.append((TASK, {"title": "collect fabric"}))
        for spec, args in calls:
            jobs.append(asyncio.create_task(c.execute(spec, args)))

    for index, when in enumerate(data["times"]):
        if strategy != "wait_complete" or index == len(data["times"]) - 1:
            clock.at(when, lambda i=index: submit(i))
    await clock.run()
    outcomes = await asyncio.gather(*jobs, return_exceptions=True)
    final = data["values"][-1] if data["values"] else None
    writes = [row for row in backend.calls if row["name"] != "flight_lookup"]
    stale = [r for r in writes if r["name"] == "calendar_create" and r["args"]["time"] != final]
    reads = [r for r in backend.calls if r["name"] == "flight_lookup"]
    correct = [
        r
        for r in backend.completed
        if (r["name"] == "calendar_create" and r["args"]["time"] == final)
        or r["name"] == "task_create"
        or r["name"] == "flight_lookup"
    ]
    if final is None:
        final_correct = not backend.calls
        completed_at = clock.now if final_correct else None
        wasted = len(reads)
    else:
        final_correct = not stale and sum(r["name"] == "calendar_create" for r in writes) == 1
        final_correct &= not data["task"] or sum(r["name"] == "task_create" for r in writes) == 1
        completed_at = max(r["at"] for r in correct) if correct else None
        wasted = max(0, len(reads) - 1)
    events = [json.loads(line) for line in c.trace.path.read_text().splitlines()]
    result = {
        "scenario": scenario,
        "strategy": strategy,
        "clock_unit": "scripted seconds, not voice/model latency",
        "calls": backend.calls,
        "wasted_reads": wasted,
        "wasted_tool_calls": wasted + len(stale),
        "stale_writes": len(stale),
        "duplicate_creations": max(0, sum(r["name"] == "calendar_create" for r in writes) - 1),
        "reused_results": sum(e["event"] == "read_result_reused" for e in events),
        "final_action_correct": bool(final_correct),
        "time_to_correct_completion": completed_at if final_correct else None,
        "errors": [type(r).__name__ for r in outcomes if isinstance(r, BaseException)],
    }
    (tmp_path / "comparison.json").write_text(json.dumps(result, indent=2))
    await c.close()
    return result


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_selective_recovery_matches_wait_correctness_with_less_work_than_eager(
    tmp_path, scenario
):
    results = {
        name: await simulate(tmp_path / name, name, scenario)
        for name in ["wait_complete", "eager", "selective"]
    }
    wait, eager, selective = (results[n] for n in ["wait_complete", "eager", "selective"])
    assert wait["final_action_correct"] and selective["final_action_correct"]
    assert (
        selective["wasted_reads"]
        == selective["stale_writes"]
        == selective["duplicate_creations"]
        == 0
    )
    if scenario == "undecided_alternatives":
        assert eager["wasted_reads"] == 2
    else:
        assert eager["wasted_reads"] > 0 and eager["stale_writes"] > 0
        assert selective["time_to_correct_completion"] <= wait["time_to_correct_completion"]
        assert selective["reused_results"] > 0
