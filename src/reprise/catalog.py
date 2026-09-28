"""Public API contracts only. No benchmark scenarios or expected answers."""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    writes: bool = False
    references: dict[str, tuple[str, str, str]] = field(default_factory=dict)

    @property
    def schema(self):
        return {"type": "object", "properties": self.properties,
                "required": list(self.required), "additionalProperties": False}

    def declaration(self):
        return {"name": self.name, "description": self.description,
                "parameters": self.schema}


S = {"type": "string", "minLength": 1}
N = {"type": "number"}
I = {"type": "integer", "minimum": 1}

TOOLS = [
    ToolSpec("search_flights", "Search flights using the final corrected destination and date. "
             "Use month and numeric day without an ordinal suffix, e.g. November 1. "
             "Never invent a year.",
             {"destination": S, "date": S}, ("destination", "date")),
    ToolSpec("book_flight", "Book only when the user asks to book. Use a returned flight ID "
             "when available. Search first when the user supplied a destination and date.",
             {"passenger_name": S, "flight_id": S}, ("passenger_name",), True,
             {"flight_id": ("search_flights", "flights", "flight_id")}),
    ToolSpec("update_identity_doc", "Update the requested document with its final corrected "
             "number. Preserve letters, digits, and leading zeros. Do not use superseded numbers.",
             {"doc_type": S, "doc_number": S}, ("doc_type", "doc_number"), True),
    ToolSpec("get_card_benefits", "Retrieve benefits for the card type the user requests.",
             {"card_type": S}, ("card_type",)),
    ToolSpec("get_exchange_rate", "Convert the final amount between the requested currencies. "
             "Use this result for follow-up actions that depend on the converted amount.",
             {"amount": N, "from_currency": S, "to_currency": S},
             ("amount", "from_currency", "to_currency")),
    ToolSpec("modify_autopay", "Change autopay only when asked, using the corrected bill "
             "and source account. Use the account name without the generic word account. "
             "A card-benefits question alone does not authorize a change.",
             {"bill_type": S, "source_account": S}, ("bill_type", "source_account"), True),
    ToolSpec("search_apartments", "Search with the apartment constraints the user actually "
             "gave. City, bedrooms, price ceiling, and pet permission are independent optional "
             "filters. Omit an unspecified filter instead of asking for it or inventing a value. "
             "A returned listing identifier may name an origin for a follow-up commute when "
             "the mock result has no street address.",
             {"city": S, "bedrooms": I, "max_price": N,
              "pets_allowed": {"type": "boolean"}}, ()),
    ToolSpec("calculate_commute", "Calculate a commute using the requested origin, destination, "
             "and transport mode. Do not reverse origin and destination.",
             {"origin_address": S, "destination_address": S, "mode": S},
             ("origin_address", "destination_address")),
    ToolSpec("update_search_filter", "Persist a search filter only when the user asks to "
             "set or change it. Use a concise snake_case property key, such as pets_allowed, "
             "max_price, min_bedrooms, or neighborhood. A correction during an unfinished "
             "request is not by itself a separate filter-update request.",
             {"filter_name": S, "value": {"anyOf": [S, {"type": "boolean"}, N]}},
             ("filter_name", "value"), True),
    ToolSpec("track_order", "Retrieve the status of the final requested order identifier. "
             "Preserve letters and leading zeros; ignore an identifier the user replaced.",
             {"order_id": S}, ("order_id",)),
    ToolSpec("search_products", "Search products using the requested query, optional category and "
             "price ceiling. If the user says under, below, or up to a price, include "
             "max_price even when an action depends on the result. Only include a ceiling "
             "if supplied by the user.",
             {"query": S, "category": S, "max_price": N}, ("query",)),
    ToolSpec("add_to_cart", "Add the requested quantity only when asked. Obtain product_id "
             "from search_products or an explicit user-provided identifier; never invent it.",
             {"product_id": S, "quantity": I}, ("product_id", "quantity"), True,
             {"product_id": ("search_products", "products", "product_id")}),
]
CATALOG = {t.name: t for t in TOOLS}
