"""What a firm is on, and what that entitles them to.

``Organization.plan`` has been a string nothing read since the schema was
written. This gives it meaning, with one decision shaping everything else:

**An allowance is not a wall.**

A developer who hits an API quota retries tomorrow. A CA firm that hits one
on the 18th of the month, with a GST filing due on the 20th, cannot. Cutting
them off at the exact moment they are busiest is the single most damaging
thing this software could do to a practice, and it would happen every month
to every firm that grew.

So a plan's document allowance is what the monthly price includes, not a
limit on what the software will do. Past it the work continues and the
overage appears on the statement. There is a ceiling as well, set far above
the allowance, and it exists to catch a runaway loop or an abusive account —
never an ordinary busy month. A firm is warned as it approaches its
allowance and told plainly when it is into overage, because a bill nobody
saw coming is its own kind of harm.

Prices here are the defaults a deployment ships with. They are numbers in
code, not a commitment: an operator sets their own, and the statement shows
whatever is configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    #: Rupees per month, excluding tax.
    monthly_price: Decimal
    #: Documents the monthly price includes. Not a cap.
    included_documents: int
    #: Rupees per document beyond the allowance.
    overage_per_document: Decimal
    #: Clients the firm may hold. Unlike documents this *is* enforced, because
    #: adding a client is deliberate, never urgent, and nobody's filing
    #: deadline turns on it.
    included_clients: int
    #: The runaway guard. Far above the allowance, and reached only by a loop
    #: or an abusive account.
    ceiling_documents: int
    #: Whether the firm may switch voice calls on at all.
    voice_available: bool
    description: str

    def overage(self, documents_used: int) -> int:
        return max(0, documents_used - self.included_documents)

    def usage_charge(self, documents_used: int) -> Decimal:
        return (self.overage_per_document * self.overage(documents_used)).quantize(
            Decimal("0.01")
        )


#: The default catalogue. A deployment may price differently; nothing here
#: is charged to anybody without an operator configuring payment.
PLANS: dict[str, Plan] = {
    "trial": Plan(
        key="trial",
        name="Trial",
        monthly_price=Decimal("0"),
        included_documents=100,
        overage_per_document=Decimal("0"),
        included_clients=25,
        ceiling_documents=300,
        voice_available=False,
        description=(
            "For a firm trying this on a handful of clients. No charge, and "
            "no card."
        ),
    ),
    "practice": Plan(
        key="practice",
        name="Practice",
        monthly_price=Decimal("3000"),
        included_documents=500,
        overage_per_document=Decimal("3"),
        included_clients=100,
        ceiling_documents=5000,
        voice_available=True,
        description="A firm of two to five people, up to a hundred clients.",
    ),
    "firm": Plan(
        key="firm",
        name="Firm",
        monthly_price=Decimal("5000"),
        included_documents=1500,
        overage_per_document=Decimal("3"),
        included_clients=400,
        ceiling_documents=15000,
        voice_available=True,
        description="A practice of five to twenty, up to four hundred clients.",
    ),
}

DEFAULT_PLAN = "trial"

#: Where the dashboard starts saying something. Deliberately early: a firm
#: should hear about a bill before it arrives, not with it.
WARN_AT = 0.8


def get_plan(key: str | None) -> Plan:
    """The named plan, or the trial.

    An unrecognised plan string resolves to the most restrictive plan rather
    than raising: a typo in a database column should not stop a firm working,
    and it must certainly not silently grant them the largest plan.
    """
    return PLANS.get((key or "").strip().lower(), PLANS[DEFAULT_PLAN])
