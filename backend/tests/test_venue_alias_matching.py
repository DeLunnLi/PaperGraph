"""Venue-alias matching must not produce substring false positives.

Found by mutation testing: flipping `len(a) <= 8` to `len(a) < 8` and
`len(ac) >= 12` to `> 12` in `_contains_venue_alias` left the whole suite green,
i.e. the rule that decides *strict token match vs. loose substring match* was
completely untested.

That boundary is what stops short acronyms from matching inside unrelated
words — "ACL" inside "clinical", "KDD" inside "backdoor". Getting it wrong
silently pollutes venue-filtered search results with irrelevant papers, which
is exactly the kind of quiet quality regression that never raises an error.
"""
from __future__ import annotations

import pytest

from app.services.retrieval.paper_filters import _contains_venue_alias


@pytest.mark.parametrize(
    "text,venue",
    [
        ("Proceedings of ACL 2024", "ACL"),
        ("accepted at acl", "ACL"),
        ("published at KDD", "KDD"),
        ("(ACL) main conference", "ACL"),
    ],
)
def test_short_acronym_matches_as_a_token(text: str, venue: str) -> None:
    assert _contains_venue_alias(text, venue) is True


@pytest.mark.parametrize(
    "text,venue",
    [
        ("a clinical study of drug response", "ACL"),   # 'acl' inside 'clinical'
        ("an unrelated backdoor attack paper", "KDD"),  # 'kdd' inside 'backdoor'
        ("oracle database internals", "ACL"),           # 'acl' inside 'oracle'
    ],
)
def test_short_acronym_does_not_match_inside_words(text: str, venue: str) -> None:
    """The substring trap the token rule exists to prevent."""
    assert _contains_venue_alias(text, venue) is False


def test_formal_conference_name_matches_as_substring() -> None:
    """Long/formal names are allowed loose matching."""
    assert _contains_venue_alias(
        "neural information processing systems 2023", "NeurIPS"
    ) is True


def test_empty_inputs_are_false() -> None:
    assert _contains_venue_alias("", "ACL") is False
    assert _contains_venue_alias("Proceedings of ACL", None) is False


def test_single_character_alias_is_ignored() -> None:
    """`len(a) < 2` guard: a 1-char alias would match almost anything."""
    assert _contains_venue_alias("a paper about x", "x") is False


# ── length boundaries (these are what the mutation testing exposed) ──────────
# `len(a) <= 8` selects the strict-token path. An alias of exactly 6 chars
# ("sigkdd") must still be token-matched; an 11-char one ("interspeech") is
# past the cutoff and is allowed to match as a plain substring.


def test_six_char_alias_still_requires_token_boundaries() -> None:
    """<= 8 means 6 is on the token side: 'sigkddx' must not match."""
    assert _contains_venue_alias("presented at sigkdd", "SIGKDD") is True
    assert _contains_venue_alias("sigkddx nonsense", "SIGKDD") is False


def test_long_alias_uses_substring_matching() -> None:
    """> 8 chars falls through to the substring branch by design."""
    assert _contains_venue_alias("proceedings of interspeech 2024", "INTERSPEECH") is True
    assert _contains_venue_alias("xxinterspeechxx", "INTERSPEECH") is True


def test_formal_name_matches_after_punctuation_is_stripped() -> None:
    """The `len(ac) >= 12` compact branch: punctuation/spacing differences in a
    long formal name must still match."""
    assert _contains_venue_alias(
        "advances-in-neural-information-processing-systems", "NeurIPS"
    ) is True
