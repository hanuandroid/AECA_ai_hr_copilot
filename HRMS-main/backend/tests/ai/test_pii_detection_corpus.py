"""AIS-03: measured detection quality of the provider boundary on a synthetic corpus (no real personal data).

Each positive must be replaced (false negatives = 0); each negative must leave the text unchanged (false positives
= 0). KNOWN_MISSES pins what patterns cannot detect, so a change in coverage is visible in review.
"""

import pytest

from app.services.ai.errors import AIInvalidRequestError
from app.services.ai.llm.base import LLMMessage
from app.services.ai.provider_policy import prepare
from app.services.ai.validation import normalize_text, validate_question

POSITIVES = [  # (text, expected categories)
    ("Mail jane.doe+hr@example.test please", {"EMAIL"}),
    ("cc user_1@sub.example.co.in", {"EMAIL"}),
    ("PAN ABCDE1234F", {"PAN"}),
    ("IFSC SBIN0001234", {"IFSC"}),
    ("Aadhaar 2345 6789 0123", {"AADHAAR"}),
    ("Aadhaar 2345-6789-0123", {"AADHAAR"}),
    ("Aadhaar 234567890123", {"NUMBER"}),
    ("Account 001234567890", {"NUMBER"}),
    ("Call +91 98765 43210", {"PHONE"}),
    ("Call +919876543210", {"PHONE"}),
    ("Call 98765 43210", {"PHONE"}),
    ("Call 987-654-3210", {"PHONE"}),
    ("Call 098765 43210", {"PHONE"}),
    ("Call +1 (415) 555-0100", {"PHONE"}),
    ("Call +44 20 7946 0958", {"PHONE"}),
    ("Call 9876543210", {"NUMBER"}),
    ("Leave on 2026-10-18 98765 43210", {"PHONE"}),
    ("Full-width ９８７６５４３２１０", {"NUMBER"}),
    ("Devanagari ९८७६५४३२१०", {"NUMBER"}),
    ("Zero-width jane​@example.test", {"EMAIL"}),
    ("Two: a@example.test and b@example.test, PAN ABCDE1234F, +91 98765 43210", {"EMAIL", "PAN", "PHONE"}),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0In0.c2ln", {"CREDENTIAL"}),
]

NEGATIVES = [
    "Leave from 2026-10-18 to 2026-10-20",
    "Date 18/10/2026 and 18.10.2026 at 10:30",
    "Amount 1,50,000 INR, Rs. 25000, 2.5 days",
    "Ticket #42 for employee id 1042, order 12345678",
    "Invoice INV-2026-0042, version 1.2.3",
    "Rooms 101 202, extension 4321, 1234 5678",
    "Pay 12 345 and 5 days of 10 hours",
    "What is the WFH policy for 2 days a week?",
    "Reference 1234-5678-9012 starts with 1",
]

KNOWN_MISSES = [  # not recognisable by patterns: governance decisions G-01 / G-02 decide whether they may be sent
    "My manager Priya Sharma approved it",
    "Call nine eight seven six five four three two one zero",
    "Reason: recovering from surgery",
    "Phone 98765 4321 0",
]


def categories(text: str) -> set[str]:
    return set(prepare("sql", [LLMMessage(role="user", content=text)]).counts)


@pytest.mark.parametrize("text,expected", POSITIVES)
def test_positives_are_detected(text, expected):
    assert categories(text) == expected


@pytest.mark.parametrize("text", NEGATIVES)
def test_negatives_are_left_alone(text):
    payload = prepare("sql", [LLMMessage(role="user", content=text)])
    assert payload.total == 0 and payload.messages[0].content == text


@pytest.mark.parametrize("text", KNOWN_MISSES)
def test_known_misses_are_pinned(text):
    assert categories(text) == set()


def test_corpus_rates():
    false_negatives = [text for text, expected in POSITIVES if categories(text) != expected]
    false_positives = [text for text in NEGATIVES if categories(text)]
    assert (len(false_negatives), len(false_positives)) == (0, 0)
    assert (len(POSITIVES), len(NEGATIVES), len(KNOWN_MISSES)) == (22, 9, 4)  # corpus size is part of the record


def test_detection_is_idempotent_and_values_never_appear():
    text = "a@example.test, PAN ABCDE1234F, +91 98765 43210, account 001234567890"
    once = prepare("sql", [LLMMessage(role="user", content=text)]).messages[0].content
    twice = prepare("sql", [LLMMessage(role="user", content=once)])
    assert twice.total == 0 and twice.messages[0].content == once.replace("[PII_", "[PII-")  # nothing new detected
    for value in ("a@example.test", "ABCDE1234F", "98765 43210", "001234567890"):
        assert value not in once


@pytest.mark.parametrize("raw,expected", [
    ("ＷＦＨ policy", "WFH policy"),  # full-width letters
    ("leave​ policy‍", "leave policy"),  # zero-width space / joiner
    ("‮ycilop‬ WFH", "ycilop WFH"),  # bidi override removed (text kept, direction trick gone)
    ("ﬁle upload", "file upload"),  # ligature
])
def test_questions_are_normalised(raw, expected):
    assert validate_question(raw) == expected


def test_invisible_only_question_is_empty():
    with pytest.raises(AIInvalidRequestError):
        validate_question("​‌⁠")


def test_normalisation_keeps_ordinary_text():
    text = "Kann ich 2 Tage pro Woche von zu Hause arbeiten? 在家工作政策 — ok."
    assert normalize_text(text) == text
