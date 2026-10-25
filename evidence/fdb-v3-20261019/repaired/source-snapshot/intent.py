"""Small, transcript-grounded memory; no evaluator labels or second model."""

import re

FOLLOWUP = re.compile(
    r"\b(?:same|those|that|it|them|actually|instead|make|change|switch|rather)\b", re.I
)
IMPLICIT_CORRECTION = re.compile(
    r"^\s*(?:no[,. ]|make (?:it|that)|change (?:it|that|the)|switch|move (?:it|that)|"
    r"use .+ (?:instead|for (?:the|my)))",
    re.I,
)
DIGITS = dict(
    zip("zero one two three four five six seven eight nine".split(), "0123456789", strict=True)
)


def compact(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def spelled_identifier(value):
    """Join space-separated single characters; literal hyphens are part of the ID."""
    if re.fullmatch(r"[A-Za-z0-9](?:\s+[A-Za-z0-9])+", value):
        return re.sub(r"\s", "", value)
    return value


def identifier_candidates(text, labels):
    """Only anchored literal/spelled identifiers; never infer an ASR homophone."""
    pattern = r"\b(?:" + "|".join(re.escape(label) for label in labels) + r")s?\b"
    candidates = []
    for match in re.finditer(pattern, text, re.I):
        tail = text[match.end() :]
        tail = re.sub(r"^\s*(?:(?:number|id|is|to)\b[\s:=-]*)*", "", tail, flags=re.I).lstrip(" :=")
        # "item two of the items ..." describes a quantity, not an ID.
        if re.match(r"(?:\d+|" + "|".join(DIGITS) + r")\s+of\b", tail, re.I):
            continue
        pieces = []
        for token in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|[-]|[,.;?!]", tail)[:18]:
            lower = token.casefold()
            if lower in {"dash", "hyphen", "-"}:
                pieces.append("-")
            elif lower in DIGITS:
                pieces.append(DIGITS[lower])
            elif token.isdigit() or (len(token) == 1 and token.isalpha()):
                pieces.append(token.upper())
            elif re.fullmatch(r"[A-Za-z0-9-]+", token) and (
                any(c.isdigit() for c in token) or (token.isupper() and len(token) <= 16)
            ):
                pieces.append(spelled_identifier(token))
            else:
                break
        if pieces:
            candidates.append("".join(pieces))
    return list(dict.fromkeys(candidates))


class IntentMemory:
    """Per-room memory with field provenance, retained only on explicit follow-ups."""

    def __init__(self):
        self.slots = {}
        self.last_input = {}
        self.sequence = {}

    def enrich(self, spec, args, text, input_id, *, prior=None):
        args = dict(args)
        carried = {}
        prior = self.slots.get(spec.name, {}) if prior is None else prior
        if FOLLOWUP.search(text):
            for key in spec.retained_slots:
                removed = key == "max_price" and re.search(
                    r"\b(?:no|remove|without|drop|ignore) (?:the )?(?:budget|price)(?: limit| ceiling| cap)?\b",
                    text,
                    re.I,
                )
                if removed:
                    args.pop(key, None)
                elif key not in args and key in prior:
                    args[key] = prior[key]["value"]
                    carried[key] = prior[key]
        return args, carried

    def record(self, spec, args, text, input_id, revision, *, sequence=0):
        if sequence < self.sequence.get(spec.name, 0):
            return
        self.sequence[spec.name] = sequence
        if self.last_input.get(spec.name) != input_id and not FOLLOWUP.search(text):
            self.slots[spec.name] = {}
        slots = self.slots.setdefault(spec.name, {})
        for key in spec.retained_slots:
            if key not in args:
                slots.pop(key, None)
            value = args.get(key)
            # Do not promote model inventions into future context. Nonliteral
            # numeric/boolean constraints remain the model's responsibility.
            attested = value is not None and compact(value) and compact(value) in compact(text)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                literal = (
                    str(int(value))
                    if isinstance(value, float) and value.is_integer()
                    else str(value)
                )
                spoken = re.sub(r"(?<=\d),(?=\d)", "", text)
                attested = bool(
                    re.search(r"(?<![\w.])" + re.escape(literal) + r"(?:\.0+)?(?![\w.])", spoken)
                )
            if attested:
                slots[key] = {"value": value, "input_id": input_id, "revision": revision}
        self.last_input[spec.name] = input_id
