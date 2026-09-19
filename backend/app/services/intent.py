"""Understanding what a client said back.

A CA firm's clients reply in English, in Hindi, and mostly in neither —
"haan kal bhej dunga", "bhej diya sir", "kya bhejna hai". So this reads
Hinglish first and English second, because that is the order the messages
actually arrive in.

Rules, not a model, and for the same reasons as the classifier: it works with
no provider configured, it costs nothing, and every reading comes with the
phrase that produced it so a human can see why the agent concluded what it
did. A model-backed reader can be registered later behind the same interface.

The critical rule (§8, §28): **a reading is never state.** "bhej diya" — "I
sent it" — sets no requirement to received. Only a file arriving does that.
What this produces is a *recorded reading*, which the agent uses to decide
whether to wait, follow up, or fetch a human.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Protocol


class Intent:
    DOCUMENT_SENT = "document_sent"
    DOCUMENT_COMMITMENT = "document_commitment"
    DOCUMENT_NOT_AVAILABLE = "document_not_available"
    CLARIFICATION_REQUIRED = "clarification_required"
    DOCUMENT_QUESTION = "document_question"
    WANTS_HUMAN = "wants_human"
    DO_NOT_CONTACT = "do_not_contact"
    WRONG_NUMBER = "wrong_number"
    ACKNOWLEDGEMENT = "acknowledgement"
    OTHER = "other"

    #: Readings that must reach a person now, whatever else is queued.
    ESCALATING = frozenset({WANTS_HUMAN, DO_NOT_CONTACT, WRONG_NUMBER})


@dataclass(frozen=True)
class IntentReading:
    intent: str
    confidence: float
    #: The phrase that produced this reading. Shown to the CA, never hidden.
    matched: tuple[str, ...] = ()
    commitment_date: dt.date | None = None
    source: str = "rules"

    @property
    def escalates(self) -> bool:
        return self.intent in Intent.ESCALATING


class IntentReader(Protocol):
    name: str

    def read(self, text: str, *, today: dt.date | None = None) -> IntentReading: ...


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    intent: str
    weight: float


def _rule(expression: str, intent: str, weight: float = 1.0) -> _Rule:
    return _Rule(re.compile(expression, re.IGNORECASE), intent, weight)


# Ordered by how decisive the phrase is. "do not contact" outranks everything,
# because getting that one wrong is the worst outcome available here.
_RULES: tuple[_Rule, ...] = (
    # Stop contacting me.
    _rule(r"\b(mat bhejo|band karo|pareshan mat|message mat)\b", Intent.DO_NOT_CONTACT, 1.0),
    _rule(
        r"\b(do ?not contact|stop (messaging|sending)|unsubscribe|opt ?out)\b",
        Intent.DO_NOT_CONTACT,
        1.0,
    ),
    # Wrong number.
    _rule(r"\b(wrong number|galat number|ye kiska number|who is this)\b", Intent.WRONG_NUMBER, 1.0),
    # Get me a person.
    _rule(r"\b(baat kar(ao|ni)|sir se baat|call me|phone karo)\b", Intent.WANTS_HUMAN, 0.9),
    _rule(r"\b(talk to (someone|a person|human)|speak to)\b", Intent.WANTS_HUMAN, 0.9),
    # I already sent it.
    _rule(r"\b(bhej diya|bhej di|bheja hai|send kar diya|de diya)\b", Intent.DOCUMENT_SENT, 0.9),
    _rule(
        r"\b(already sent|sent (it|already|yesterday)|i have sent|shared it)\b",
        Intent.DOCUMENT_SENT,
        0.9,
    ),
    # I do not have it.
    _rule(r"\b(nahi hai|nai hai|nahi mila|available nahi)\b", Intent.DOCUMENT_NOT_AVAILABLE, 0.8),
    _rule(
        r"\b(don'?t have|do not have|not available|can'?t find)\b",
        Intent.DOCUMENT_NOT_AVAILABLE,
        0.8,
    ),
    # I will send it.
    _rule(
        r"\b(bhej dunga|bhej dungi|bhejta hu|bhej deta hu|kar dunga|de dunga)\b",
        Intent.DOCUMENT_COMMITMENT,
        0.85,
    ),
    _rule(
        r"\b(will send|i'?ll send|sending (it )?(soon|today|tomorrow)|send kar)\b",
        Intent.DOCUMENT_COMMITMENT,
        0.85,
    ),
    # What do you want?
    _rule(
        r"\b(samajh nahi|kya bhejna|kaun sa|konsa|kya chahiye)\b",
        Intent.CLARIFICATION_REQUIRED,
        0.8,
    ),
    _rule(
        r"\b(what (do you need|should i send)|which (one|document)|not sure what)\b",
        Intent.CLARIFICATION_REQUIRED,
        0.8,
    ),
    # Is this the one?
    _rule(
        r"\b(yeh wala|ye wala|is this (the )?(one|right)|correct hai|thik hai kya)\b",
        Intent.DOCUMENT_QUESTION,
        0.7,
    ),
    # Noise that means nothing actionable.
    _rule(
        r"^\s*(ok(ay)?|thik hai|theek hai|haan|ha|yes|sure|thanks|thank you|ji)\s*[.!]*\s*$",
        Intent.ACKNOWLEDGEMENT,
        0.6,
    ),
)

#: "kal" means tomorrow in Hindi and yesterday in Hindi. Context decides, and
#: an agent cannot. Treated as tomorrow here because it appears in a promise
#: — "kal bhej dunga" is future tense — and the date is recorded as a
#: *commitment* a human can see and correct, never as a fact about the past.
_WHEN: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(aaj|today|abhi|right now)\b", re.IGNORECASE), 0),
    (re.compile(r"\b(kal|tomorrow|kl)\b", re.IGNORECASE), 1),
    (re.compile(r"\b(parso|day after)\b", re.IGNORECASE), 2),
    (re.compile(r"\b(next week|agle hafte)\b", re.IGNORECASE), 7),
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "somvar": 0,
    "mangalvar": 1,
    "budhvar": 2,
    "guruvar": 3,
    "shukravar": 4,
}


def _commitment_date(text: str, today: dt.date) -> dt.date | None:
    for pattern, days in _WHEN:
        if pattern.search(text):
            return today + dt.timedelta(days=days)
    lowered = text.lower()
    for name, index in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            ahead = (index - today.weekday()) % 7 or 7
            return today + dt.timedelta(days=ahead)
    return None


@dataclass
class _Tally:
    score: float = 0.0
    matched: list[str] = field(default_factory=list)


class RulesIntentReader:
    """Phrase matching over Hinglish and English."""

    name = "rules"

    def read(self, text: str, *, today: dt.date | None = None) -> IntentReading:
        cleaned = (text or "").strip()
        if not cleaned:
            return IntentReading(Intent.OTHER, 0.0, source=self.name)

        day = today or dt.date.today()
        tallies: dict[str, _Tally] = {}
        for rule in _RULES:
            found = rule.pattern.search(cleaned)
            if found:
                tally = tallies.setdefault(rule.intent, _Tally())
                tally.score = max(tally.score, rule.weight)
                tally.matched.append(found.group(0))

        if not tallies:
            return IntentReading(Intent.OTHER, 0.0, source=self.name)

        # An escalating reading always wins. If someone asked to be left alone
        # and also said "kal bhej dunga", the part that matters is the first.
        for intent in (Intent.DO_NOT_CONTACT, Intent.WRONG_NUMBER, Intent.WANTS_HUMAN):
            if intent in tallies:
                return IntentReading(
                    intent,
                    round(tallies[intent].score, 2),
                    matched=tuple(tallies[intent].matched),
                    source=self.name,
                )

        intent, tally = max(tallies.items(), key=lambda item: item[1].score)
        commitment = (
            _commitment_date(cleaned, day) if intent == Intent.DOCUMENT_COMMITMENT else None
        )
        # A promise with no date is still a promise, but a vaguer one.
        confidence = tally.score if commitment or intent != Intent.DOCUMENT_COMMITMENT else 0.7

        return IntentReading(
            intent,
            round(confidence, 2),
            matched=tuple(dict.fromkeys(tally.matched)),
            commitment_date=commitment,
            source=self.name,
        )


_reader: IntentReader = RulesIntentReader()


def get_intent_reader() -> IntentReader:
    return _reader


def set_intent_reader(reader: IntentReader | None) -> None:
    global _reader
    _reader = reader or RulesIntentReader()


def read_intent(text: str, *, today: dt.date | None = None) -> IntentReading:
    return get_intent_reader().read(text, today=today)
