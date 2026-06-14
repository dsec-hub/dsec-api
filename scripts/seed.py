"""Seed the club-domain tables with realistic sample data.

Usage (from dsec-api/):
    .venv/bin/python scripts/seed.py           # seed only if empty (no-op otherwise)
    .venv/bin/python scripts/seed.py --reset   # wipe domain tables first, then seed

Touches ONLY the domain tables (people/events/sponsors/finance) — never the
operational tables (event_log/api_key/rate_limit). The data is shaped to light
up every dashboard section: events needing attention, a DUSA pipeline spread,
outstanding finance, sponsor stages, and a committee roster.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Event, FinanceEntry, Person, Sponsor  # noqa: E402


def _seed(session) -> dict:
    # --- People (flush to assign ids so events/sponsors can reference them) ---
    alice = Person(name="Alice Chen", type="Exec", committee="Executive",
                   role_title="President", email="president@dsec.club", status="Active")
    bob = Person(name="Bob Singh", type="Exec", committee="Executive",
                 role_title="Treasurer", email="treasurer@dsec.club", status="Active")
    priya = Person(name="Priya Patel", type="Committee Lead", committee="Events",
                   role_title="Events Lead", email="events@dsec.club", status="Active")
    marcus = Person(name="Marcus Lee", type="Committee Lead", committee="Marketing",
                    role_title="Marketing Lead", email="marketing@dsec.club", status="Active")
    dana = Person(name="Dana Kim", type="Committee Member", committee="Events",
                  email="dana@dsec.club", status="Active")
    tom = Person(name="Tom Nguyen", type="General Member", email="tom@example.com",
                 status="Active")
    sarah = Person(name="Sarah Wood", type="External Contact",
                   role_title="Partnerships Manager", email="sarah@techcorp.com",
                   status="Prospect")
    session.add_all([alice, bob, priya, marcus, dana, tom, sarah])
    session.flush()

    # --- Events (today is mid-2026; mix of statuses + DUSA states) ---
    bbq = Event(
        name="Welcome BBQ", type="Social", status="Planning",
        start_date=date(2026, 7, 10), trimester="T2 2026", format="In-person",
        venue="Campus Lawn", event_lead_id=priya.id, committee="Events",
        dusa_submission_status="Submitted", dusa_deadline=date(2026, 6, 20),
        dusa_required=True, food_provided=True, expected_attendance=120,
    )
    industry = Event(
        name="Industry Night", type="Networking", status="Planning",
        start_date=date(2026, 8, 5), trimester="T2 2026", format="In-person",
        venue="Hub 2.01", event_lead_id=marcus.id, committee="Marketing",
        dusa_submission_status="Not Started", dusa_deadline=date(2026, 6, 25),
        dusa_required=True, external_guests=True, expected_attendance=80,
    )
    hackathon = Event(
        name="Hackathon 2026", type="Flagship", status="Idea",
        start_date=date(2026, 9, 15), trimester="T2 2026", format="Hybrid",
        event_lead_id=None, committee="Events",  # no lead yet -> needs attention
        dusa_submission_status="Not Started", dusa_deadline=date(2026, 8, 1),
        dusa_required=True, expected_attendance=150,
    )
    trivia = Event(
        name="Trivia Night", type="Social", status="Confirmed",
        start_date=date(2026, 6, 28), trimester="T2 2026", format="In-person",
        venue="The Den", event_lead_id=dana.id, committee="Events",
        dusa_submission_status="Approved", dusa_deadline=date(2026, 6, 5),
        dusa_required=True, food_provided=True, expected_attendance=60,
    )
    agm = Event(
        name="Annual General Meeting", type="Meeting", status="Planning",
        start_date=date(2026, 7, 20), trimester="T2 2026", format="In-person",
        venue="Hub 2.05", event_lead_id=alice.id, committee="Executive",
        dusa_submission_status="Not Required", dusa_required=False,
        expected_attendance=40,
    )
    past = Event(
        name="Welcome Week Stall", type="Outreach", status="Completed",
        start_date=date(2026, 3, 2), trimester="T1 2026", format="In-person",
        event_lead_id=priya.id, committee="Marketing",
        dusa_submission_status="Approved", expected_attendance=200,
        actual_attendance=220,
    )
    session.add_all([bbq, industry, hackathon, trivia, agm, past])
    session.flush()

    # --- Sponsors (varied pipeline stages) ---
    session.add_all([
        Sponsor(organisation="TechCorp", stage="Negotiating",
                contact_person_id=sarah.id, tier="Gold", value_aud=5000,
                dusa_approved=True),
        Sponsor(organisation="Foodie Co", stage="Contacted", tier="Silver",
                value_aud=1500, dusa_approved=False),
        Sponsor(organisation="DevTools Inc", stage="Secured", tier="Gold",
                value_aud=8000, dusa_approved=True),
    ])

    # --- Finance (outstanding total = 2000 + 5000 + 450.50 = 7450.50) ---
    session.add_all([
        FinanceEntry(item="DUSA Grant T2", type="Grant", amount_aud=2000,
                     status="Requested", date_requested=date(2026, 6, 1)),
        FinanceEntry(item="TechCorp Sponsorship", type="Sponsorship Income",
                     amount_aud=5000, status="Invoiced"),
        FinanceEntry(item="BBQ Food Supplies", type="Other Expense",
                     amount_aud=450.50, status="Pending", gst_included=True,
                     related_event_id=bbq.id),
        FinanceEntry(item="Trivia Prizes", type="Reimbursement", amount_aud=120,
                     status="Paid", date_paid=date(2026, 6, 5)),
        FinanceEntry(item="Banner Printing", type="Other Expense", amount_aud=300,
                     status="Rejected"),
    ])

    session.commit()
    return {
        "people": session.execute(select(func.count()).select_from(Person)).scalar(),
        "events": session.execute(select(func.count()).select_from(Event)).scalar(),
        "sponsors": session.execute(select(func.count()).select_from(Sponsor)).scalar(),
        "finance": session.execute(select(func.count()).select_from(FinanceEntry)).scalar(),
    }


def _reset(session) -> None:
    # FK-safe order: finance -> sponsors -> events -> people.
    session.execute(delete(FinanceEntry))
    session.execute(delete(Sponsor))
    session.execute(delete(Event))
    session.execute(delete(Person))
    session.commit()


def main() -> int:
    reset = "--reset" in sys.argv[1:]
    session = SessionLocal()
    try:
        if reset:
            _reset(session)
            print("Reset: domain tables cleared.")
        existing = session.execute(select(func.count()).select_from(Person)).scalar()
        if existing and not reset:
            print(f"Already seeded ({existing} people present). Use --reset to reseed.")
            return 0
        counts = _seed(session)
        print(f"Seeded: {counts}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
