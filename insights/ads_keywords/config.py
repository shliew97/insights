# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Configuration for the Google Ads keyword intent classifier.

Follows the conventions of :mod:`insights.customer_retention.config`: every
business rule the marketing team may want to tune is a documented default on
:class:`KeywordIntentConfig`, and the dataclass can be built from (and
overridden by) the query's ``backend_function_args`` JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Intent taxonomy
# --------------------------------------------------------------------------- #
# Every keyword gets exactly one of these labels.
HIGH_INTENT = "HIGH_INTENT"  # searcher is ready to book / buy the service
IRRELEVANT = "IRRELEVANT"  # intent mismatch: jobs, courses, definitions, DIY
POTENTIAL_NEGATIVE = "POTENTIAL_NEGATIVE"  # likely wasted spend -> candidate negative
UNKNOWN = "UNKNOWN"  # engine unsure -> manual review, then codify as a signal term

INTENTS: tuple[str, ...] = (HIGH_INTENT, IRRELEVANT, POTENTIAL_NEGATIVE, UNKNOWN)

# What the marketer should do with a keyword of this intent.
INTENT_ACTIONS: dict[str, str] = {
    HIGH_INTENT: "Keep / increase bid",
    IRRELEVANT: "Pause keyword",
    POTENTIAL_NEGATIVE: "Add as negative keyword",
    UNKNOWN: "Manual review",
}

# --------------------------------------------------------------------------- #
# Signal term lists (matched case-insensitively)
# --------------------------------------------------------------------------- #
# What the business sells. A keyword with no service term can never be
# HIGH_INTENT on its own.
DEFAULT_SERVICE_TERMS: tuple[str, ...] = (
    "massage",
    "masseur",
    "masseuse",
    "deep tissue",
    "swedish massage",
    "thai massage",
    "sports massage",
    "prenatal massage",
    "postnatal massage",
    "lymphatic drainage",
    "trigger point",
    "hot stone",
    "aromatherapy",
    "reflexology",
    "bodywork",
    "shiatsu",
    "acupressure",
    "physiotherapy",
    "spa",
    "facial",
    "body treatment",
    "wellness",
    "therapy",
    "therapist",
    "treatment",
    # Malay / local terms (business reports in Asia/Kuala_Lumpur time)
    "urut",
    "pijat",
    "terapi",
)

# Explicit booking actions - the strongest service-intent signal.
DEFAULT_BOOKING_TERMS: tuple[str, ...] = (
    "book",
    "booking",
    "appointment",
    "appointments",
    "reserve",
    "reservation",
    "schedule",
    "walk in",
    "walk-in",
    "same day",
    "same-day",
    "outcall",
    "home service",
    "home visit",
    "mobile massage",
)

# Softer commercial signals (proximity, price, availability, quality).
DEFAULT_HIGH_INTENT_TERMS: tuple[str, ...] = (
    "near me",
    "near by",
    "nearby",
    "close to me",
    "around me",
    "open now",
    "open late",
    "24 hour",
    "24/7",
    "price",
    "prices",
    "cost",
    "how much",
    "rate",
    "rates",
    "charges",
    "fee",
    "best",
    "top rated",
    "cheap",
)

# Someone whose need is NOT a customer buying a session.
DEFAULT_IRRELEVANT_TERMS: tuple[str, ...] = (
    "course",
    "courses",
    "class",
    "classes",
    "training",
    "certification",
    "certificate",
    "diploma",
    "degree",
    "school",
    "academy",
    "college",
    "learn",
    "learning",
    "tutorial",
    "how to",
    "how do",
    "diy",
    "job",
    "jobs",
    "career",
    "vacancy",
    "hiring",
    "salary",
    "meaning",
    "definition",
    "wikipedia",
    "franchise",
    "business plan",
    "license",
    "insurance",
)

# Product / equipment buyers and freebie seekers - the classic wasted spend.
DEFAULT_PRODUCT_TERMS: tuple[str, ...] = (
    "chair",
    "chairs",
    "gun",
    "machine",
    "massager",
    "device",
    "mat",
    "pad",
    "cushion",
    "pillow",
    "recliner",
    "heater",
    "oil",
    "lotion",
    "cream",
    "gel",
    "roller",
    "stick",
    "belt",
    "shoes",
    "slippers",
    "buy",
    "for sale",
    "amazon",
    "shopee",
    "lazada",
    "massage bed",
    "massage table",
    "accessories",
    "equipment",
    "supplies",
    "free",
)

# Clearly off-brand / policy-risky queries: always a negative keyword.
DEFAULT_OFF_BRAND_TERMS: tuple[str, ...] = (
    "video",
    "videos",
    "youtube",
    "watch",
    "download",
    "torrent",
    "porn",
    "xxx",
    "nude",
    "naked",
    "happy ending",
    "escort",
    "togel",
    "slot",
    "casino",
    "hack",
    "cheat",
    "fake",
    "scam",
)


@dataclass(slots=True)
class KeywordIntentConfig:
    """Tunable business rules for the keyword intent classifier."""

    service_terms: tuple[str, ...] = DEFAULT_SERVICE_TERMS
    booking_terms: tuple[str, ...] = DEFAULT_BOOKING_TERMS
    high_intent_terms: tuple[str, ...] = DEFAULT_HIGH_INTENT_TERMS
    irrelevant_terms: tuple[str, ...] = DEFAULT_IRRELEVANT_TERMS
    product_terms: tuple[str, ...] = DEFAULT_PRODUCT_TERMS
    off_brand_terms: tuple[str, ...] = DEFAULT_OFF_BRAND_TERMS

    def __post_init__(self) -> None:
        # Normalise tuple-able inputs so callers can pass lists from JSON.
        for name in (
            "service_terms",
            "booking_terms",
            "high_intent_terms",
            "irrelevant_terms",
            "product_terms",
            "off_brand_terms",
        ):
            value = getattr(self, name)
            setattr(self, name, tuple(str(term).strip().lower() for term in value if str(term).strip()))

    @classmethod
    def from_dict(cls, data: dict | None) -> KeywordIntentConfig:
        data = dict(data or {})
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})
