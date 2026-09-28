import asyncio
import importlib.util
import sys
from pathlib import Path

from .config import ROOT

FDB_COMMIT = "3e799c45a045256f47d5f1c9cda90157e2d2ec9e"


class FDBBackend:
    """Run the unchanged public mock functions; reproduce their latency asynchronously."""

    def __init__(self, latency_profile="instant", root: Path | None = None):
        folder = root or ROOT / "third_party/fdb/v3"
        if not (folder / "mock_apis.py").is_file():
            raise RuntimeError("Benchmark tools missing. Run ./reproduce.sh setup.")
        # Only API implementations and their delay definitions are loaded here.
        # Scenario metadata, recordings, expected calls and labels are never imported.
        for name in ("latency_injector", "mock_apis"):
            if name not in sys.modules:
                spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
        self.functions = sys.modules["mock_apis"].MockAPIRegistry.FUNCTIONS
        self.injector = sys.modules["latency_injector"].LatencyInjector(profile=latency_profile)
        self.search_filters = {}

    async def prepare(self, name):
        count = self.injector.call_counts.get(name, 0)
        self.injector.call_counts[name] = count + 1
        delay = self.injector._get_profile(name).get_delay_ms(count) / 1000
        await asyncio.sleep(delay)

    async def call(self, name, args):
        if name == "update_search_filter":
            result = self.functions[name](**args)
            if result.get("status") == "success":
                self.search_filters[args["filter_name"]] = args["value"]
            return result
        if name == "search_apartments":
            # The benchmark requests partial searches, while its public mock
            # requires all three positional filters. Supply simulation defaults
            # only to the unchanged mock; the recorded call retains the exact
            # constraints supplied by the user and proposed by the model.
            mock_args = dict(args)
            mock_args.setdefault("city", "unspecified")
            mock_args.setdefault("bedrooms", self.search_filters.get("min_bedrooms", 1))
            mock_args.setdefault("max_price", self.search_filters.get("max_price", 2000))
            return self.functions[name](**mock_args)
        return self.functions[name](**args)
