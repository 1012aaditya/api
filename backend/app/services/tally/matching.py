"""Resolving a supplier printed on an invoice to a ledger in the customer's books.

The order of the strategies is the whole design, and it runs strongest first:

1. **GSTIN** — a registration number is the supplier's identity. If their
   ledger master carries the same GSTIN, that is not a guess, it is a fact.
2. **A confirmed alias** — a human has answered this exact question before.
3. **Normalised name** — "ACME TRADERS PVT. LTD." and "Acme Traders Private
   Limited" are the same string once case, punctuation and company suffixes
   are removed. Still deterministic.
4. **Fuzzy similarity** — a *suggestion*, never an answer. It is offered to a
   human and applied only once they confirm it.

The line between 3 and 4 is the one that matters. Posting to the wrong ledger
is worse than posting nothing: nothing is a gap somebody notices, and a wrong
ledger is a number that quietly reconciles to something untrue.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.models import Ledger, LedgerAlias

#: Below this, a name is not even worth showing as a suggestion — the reviewer
#: is better served by an empty box than by a distracting wrong answer.
SUGGESTION_FLOOR = 0.72

#: Corporate form. Two companies are not distinguished by whether someone
#: typed "Pvt Ltd" or "Private Limited", so neither should matching be.
_SUFFIXES = (
    "private limited",
    "public limited",
    "pvt ltd",
    "pvt limited",
    "pvt",
    "private",
    "limited",
    "ltd",
    "llp",
    "limited liability partnership",
    "and company",
    "and co",
    "co",
    "corporation",
    "corp",
    "incorporated",
    "inc",
    "enterprises",
    "enterprise",
)

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

MatchMethod = Literal["gstin", "alias", "exact_name", "fuzzy_name", "unmatched"]


def normalize_name(value: str | None) -> str:
    """Case-fold, strip punctuation and drop the corporate suffix.

    Unicode is normalised first so that a composed and a decomposed 'ā' do not
    look like different suppliers.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("&", " and ")
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    # Repeatedly, because "Acme Traders Pvt Ltd" carries two of them.
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(" " + suffix):
                text = text[: -len(suffix) - 1].strip()
                changed = True
    return text


def normalize_gstin(value: str | None) -> str:
    if not value:
        return ""
    return _WHITESPACE.sub("", value).upper()


def similarity(left: str, right: str) -> float:
    """How alike two normalised names are, between 0 and 1.

    Character similarity alone is not enough here, because of how people
    actually name ledgers. "Udupi Software Systems" against "Udupi Software
    Systems Pvt Ltd - Bengaluru" scores 0.71 on raw character overlap — below
    any sensible floor — even though it is obviously the same supplier with a
    branch appended. A real run produced exactly that, and offered the reviewer
    an empty suggestion list.

    So whole-token containment is scored too, and the better of the two wins.
    A name whose tokens are all present at the start of a longer one scores by
    how much of it they cover; otherwise the shared-token fraction applies.
    Both are only ever *suggestions* — nothing here can produce a match on its
    own, which is what makes it safe to be generous.
    """
    if not left or not right:
        return 0.0

    score = SequenceMatcher(None, left, right).ratio()

    left_tokens, right_tokens = left.split(), right.split()
    if not left_tokens or not right_tokens:
        return score

    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    if longer[: len(shorter)] == shorter:
        # "acme traders" inside "acme traders bengaluru": scaled by coverage,
        # so a single shared first word does not score like a whole name.
        score = max(score, 0.75 + 0.20 * (len(shorter) / len(longer)))
    else:
        shared = set(left_tokens) & set(right_tokens)
        union = set(left_tokens) | set(right_tokens)
        score = max(score, 0.95 * len(shared) / len(union))

    return min(score, 1.0)


@dataclass(frozen=True)
class LedgerMatch:
    """The outcome of resolving one supplier.

    ``confirmed`` is the field callers must branch on. It is True only for a
    fact (a GSTIN, or a human's earlier answer) or a deterministic string
    equality — never for a similarity score, however high.
    """

    ledger_id: str | None
    ledger_name: str | None
    method: MatchMethod
    confidence: float
    confirmed: bool
    #: Ranked alternatives for a human to choose from, best first.
    suggestions: tuple[tuple[str, str, float], ...] = ()

    @property
    def matched(self) -> bool:
        return self.ledger_id is not None and self.confirmed


UNMATCHED = LedgerMatch(
    ledger_id=None, ledger_name=None, method="unmatched", confidence=0.0, confirmed=False
)


class LedgerMatcher:
    """Resolves suppliers against one organization's ledger master.

    Built once per export run and held in memory: a batch of 200 invoices
    should not mean 200 round trips to look up the same forty suppliers.
    """

    def __init__(
        self,
        ledgers: Sequence[Ledger],
        aliases: Iterable[LedgerAlias] = (),
        *,
        suggestion_floor: float = SUGGESTION_FLOOR,
    ) -> None:
        self._ledgers = [ledger for ledger in ledgers if ledger.is_active]
        self._by_id = {ledger.id: ledger for ledger in self._ledgers}
        self._suggestion_floor = suggestion_floor

        self._by_gstin: dict[str, Ledger] = {}
        name_counts: dict[str, list[Ledger]] = {}
        for ledger in self._ledgers:
            gstin = normalize_gstin(ledger.gstin)
            # First one wins: two ledgers sharing a GSTIN is a bookkeeping
            # problem in their master, and picking arbitrarily would hide it.
            if gstin and gstin not in self._by_gstin:
                self._by_gstin[gstin] = ledger
            if ledger.normalized_name:
                name_counts.setdefault(ledger.normalized_name, []).append(ledger)

        # A key that two ledgers share cannot identify either of them. Rather
        # than pick whichever was imported first, drop it from the exact-name
        # index entirely: the supplier falls through to suggestions and a human
        # says which "Acme Traders" this invoice is from.
        self._by_name: dict[str, Ledger] = {
            key: matches[0] for key, matches in name_counts.items() if len(matches) == 1
        }
        self._ambiguous_names: frozenset[str] = frozenset(
            key for key, matches in name_counts.items() if len(matches) > 1
        )

        self._alias_gstin: dict[str, str] = {}
        self._alias_name: dict[str, str] = {}
        for alias in aliases:
            if alias.ledger_id not in self._by_id:
                continue  # the ledger was deleted; the alias is dead weight
            if alias.key_type == "gstin":
                self._alias_gstin[normalize_gstin(alias.match_key)] = alias.ledger_id
            else:
                self._alias_name[normalize_name(alias.match_key)] = alias.ledger_id

    def _hit(self, ledger: Ledger, method: MatchMethod, confidence: float) -> LedgerMatch:
        return LedgerMatch(
            ledger_id=ledger.id,
            ledger_name=ledger.name,
            method=method,
            confidence=confidence,
            confirmed=True,
        )

    def match(self, *, name: str | None, gstin: str | None) -> LedgerMatch:
        """Resolve one supplier, strongest evidence first."""
        key_gstin = normalize_gstin(gstin)
        key_name = normalize_name(name)

        # 1. The registration number. Identity, not resemblance.
        if key_gstin:
            ledger = self._by_gstin.get(key_gstin)
            if ledger is not None:
                return self._hit(ledger, "gstin", 1.0)

            alias_id = self._alias_gstin.get(key_gstin)
            if alias_id is not None:
                return self._hit(self._by_id[alias_id], "alias", 1.0)

        # 2. A question a human already answered.
        if key_name:
            alias_id = self._alias_name.get(key_name)
            if alias_id is not None:
                return self._hit(self._by_id[alias_id], "alias", 1.0)

            # 3. The same string, once the noise is gone — unless two of
            #    their ledgers share it, in which case nobody can tell which.
            ledger = self._by_name.get(key_name)
            if ledger is not None:
                return self._hit(ledger, "exact_name", 0.97)

        # 4. Nothing certain. Offer alternatives; commit to none of them.
        return LedgerMatch(
            ledger_id=None,
            ledger_name=None,
            method="unmatched",
            confidence=0.0,
            confirmed=False,
            suggestions=self.suggest(name=name, gstin=gstin),
        )

    def suggest(
        self, *, name: str | None, gstin: str | None = None, limit: int = 3
    ) -> tuple[tuple[str, str, float], ...]:
        """Ranked (ledger_id, ledger_name, score) candidates for a human.

        Returned separately from ``match`` so that the caller cannot mistake a
        suggestion for a resolution: these never carry ``confirmed``.
        """
        key_name = normalize_name(name)
        if not key_name:
            return ()

        scored = [
            (ledger.id, ledger.name, similarity(key_name, ledger.normalized_name))
            for ledger in self._ledgers
            if ledger.normalized_name
        ]
        scored = [row for row in scored if row[2] >= self._suggestion_floor]
        scored.sort(key=lambda row: (-row[2], row[1]))
        return tuple(scored[:limit])

    def get(self, ledger_id: str) -> Ledger | None:
        return self._by_id.get(ledger_id)

    def by_name(self, name: str | None) -> Ledger | None:
        """Exact-name lookup, for validating configured tax ledgers."""
        if not name:
            return None
        return self._by_name.get(normalize_name(name))

    def is_ambiguous(self, name: str | None) -> bool:
        """True when two ledgers normalise to the same name as this one."""
        return normalize_name(name) in self._ambiguous_names

    def __len__(self) -> int:
        return len(self._ledgers)
