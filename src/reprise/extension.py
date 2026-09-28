"""Working washer-help extension grounded in Samsung's public support page."""
import asyncio

from .catalog import S, ToolSpec

SOURCE_TITLE = "Samsung UK — What do the codes on my washing machine mean?"
SOURCE_URL = "https://www.samsung.com/uk/support/home-appliances/what-do-the-codes-on-my-washing-machine-mean/"

MANUAL_TOOLS = [
    ToolSpec(
        "lookup_manual",
        "Look up general Samsung washing machine guidance for a read error code. "
        "Give the exact code as seen or heard. Include the model if known. "
        "The result tells you whether the source supports this device and code.",
        {"device_type": S, "error_code": S, "model": S},
        ("device_type", "error_code"),
    ),
]

GUIDANCE = {
    "4C": {
        "meaning": "Water supply issue",
        "steps": ["Check that the water tap is fully open and water is flowing.",
                  "Check the supply hose for kinks or blockages.",
                  "Check and clean the mesh inlet filter, following the appliance manual."],
    },
    "5C": {
        "meaning": "Water drainage issue",
        "steps": ["Check the drain hose and waste connection for kinks or blockages.",
                  "If water remains in the drum, use the emergency drain hose as the manual directs.",
                  "Clean the drain pump filter following the appliance manual."],
    },
}


class ManualBackend:
    def __init__(self, trace=None):
        self.trace = trace

    async def prepare(self, name):
        await asyncio.sleep(0)

    async def call(self, name, args):
        device = args["device_type"].casefold()
        code = args["error_code"].strip().upper().replace(" ", "")
        code = {"4E": "4C", "5E": "5C"}.get(code, code)
        model = args.get("model", "unspecified")
        supported = any(word in device for word in ("washer", "washing machine", "laundry"))
        if not supported or code not in GUIDANCE:
            return {"status": "not_found", "device_type": args["device_type"],
                    "model": model, "error_code": code,
                    "message": "No supported guidance for this device/code. Ask for a clearer code "
                               "or direct the user to their model manual.",
                    "source_title": SOURCE_TITLE, "source_url": SOURCE_URL}
        item = GUIDANCE[code]
        if self.trace:
            self.trace.event("manual_found", code=code, meaning=item["meaning"],
                             source_title=SOURCE_TITLE, source_url=SOURCE_URL)
        return {"status": "success", "device_type": args["device_type"],
                "model": model, "error_code": code, "meaning": item["meaning"],
                "steps": item["steps"], "scope": "general Samsung washing machine support",
                "model_specific": False,
                "source_title": SOURCE_TITLE, "source_url": SOURCE_URL}
