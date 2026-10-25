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

GOAL_RESET = re.compile(
    r"\b(?:never mind|scratch that|forget (?:it|that))(?:\s*[,.;!?]|$)|"
    r"\b(?:never mind|forget|cancel|stop) (?:it )?(?:all|everything)\b|"
    r"^\s*(?:actually[ ,]*)?(?:cancel|stop)(?: (?:it|that))?[.! ]*$|"
    r"\bdifferent (?:goal|request)\b", re.I
)
REVISION_MARKER = re.compile(r"\b(?:actually|instead|rather|make (?:it|that)|change (?:it|that)|move (?:it|that))\b", re.I)
REFRESH = re.compile(r"\b(?:refresh|latest|(?:check|search|look)\b.+\bagain)\b", re.I)


def leaves(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from leaves(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from leaves(child)
    elif isinstance(value, str):
        yield value


def scope_matches(text, values, scope=()):
    """Lexical scope evidence only; never rewrites or fuzzy-matches an argument."""
    labels = [*scope, *leaves(values)]
    return any(label and re.search(r"(?<!\w)" + re.escape(label).replace('_', r'[\s_-]')
                                  + r"(?:s)?(?!\w)", text, re.I) for label in labels)


def affected_indices(text, previous, subjects):
    """Explicit entity evidence, then a unique last-clause referent; otherwise fail closed."""
    if GOAL_RESET.search(text):
        return set(range(len(subjects)))
    markers = list(REVISION_MARKER.finditer(text))
    fragment = text[markers[-1].end():] if markers else text
    def matches(fragment):
        # A named item is stronger evidence than its shared domain ("task").
        # Otherwise correcting one of two Tasks would discard both of them.
        specific = {
            i for i, (values, scope) in enumerate(subjects)
            if scope_matches(fragment, [v for v in leaves(values)
                                       if v.casefold() not in {s.casefold() for s in scope}])
        }
        return specific or {
            i for i, (values, scope) in enumerate(subjects) if scope_matches(fragment, values, scope)
        }
    direct = matches(fragment)
    if direct:
        return direct
    # Elliptical replacements ("actually six") refer only to a uniquely evidenced
    # last clause. A multi-item/unclear clause invalidates pending work for re-planning.
    context = text[:markers[-1].start()] if markers and text[:markers[-1].start()].strip(' ,—-') else previous
    context = re.split(r"\b(?:and|then|also)\b|[;—]", context, flags=re.I)[-1]
    focused = matches(context)
    return focused if len(focused) == 1 else set(range(len(subjects)))


def undecided_read(text, args, scope):
    def freeform(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"title", "notes", "body"} and isinstance(child, str):
                    yield child
                else:
                    yield from freeform(child)
        elif isinstance(value, list):
            for child in value:
                yield from freeform(child)
    wording = set(freeform(args))
    for clause in re.split(r"\b(?:and|then|also)\b|[;]", text, flags=re.I):
        if re.search(r"\b(?:undecided|not sure|not decided|haven't decided)\b", clause, re.I):
            if scope_matches(clause, args, scope):
                return True
        if re.search(r"\b(?:compare|both|each|all alternatives)\b", clause, re.I):
            continue
        if re.search(r"\b(?:or|either)\b", clause, re.I):
            # A full Boolean search query is one stable query. Choosing one side
            # of a spoken alternative is different from searching the supplied OR.
            for value in leaves(args):
                checked = re.sub(r"\bor (?:something|anything)(?: like that)?\b", "", clause, flags=re.I) if value in wording else clause
                if not re.search(r"\bor\b", value, re.I) and re.search(
                    r"(?<!\w)" + re.escape(value) + r"\s+or\b|\bor\s+" + re.escape(value) + r"(?!\w)", checked, re.I
                ):
                    return True
    return False


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
