"""Club-domain models: creation, FK relations, and the soft-delete flag."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models import Event, FinanceEntry, Person, Sponsor


def test_event_links_to_lead_person(db):
    lead = Person(name="Priya Patel", type="Committee Lead", committee="Events", status="Active")
    db.add(lead)
    db.flush()  # assigns lead.id
    db.add(Event(
        name="Welcome BBQ", status="Planning", start_date=date(2026, 7, 10),
        event_lead_id=lead.id, dusa_required=True,
    ))
    db.commit()

    row = db.execute(select(Event).where(Event.name == "Welcome BBQ")).scalar_one()
    assert row.event_lead_id == lead.id
    assert row.archived is False        # soft-delete default
    assert row.created_at is not None   # timestamp default applied


def test_sponsor_and_finance_relations_and_outstanding_sum(db):
    person = Person(name="Sarah Wood", type="External Contact", status="Prospect")
    db.add(person)
    db.flush()
    event = Event(name="Industry Night", status="Planning")
    db.add(event)
    db.flush()

    db.add_all([
        Sponsor(organisation="TechCorp", stage="Negotiating",
                contact_person_id=person.id, value_aud=5000),
        FinanceEntry(item="Catering", type="Other Expense", amount_aud=450.50,
                     status="Pending", related_event_id=event.id),
        FinanceEntry(item="Prizes", type="Reimbursement", amount_aud=120,
                     status="Paid"),  # excluded from outstanding
    ])
    db.commit()

    assert db.execute(select(Sponsor)).scalar_one().contact_person_id == person.id

    outstanding = db.execute(
        select(func.coalesce(func.sum(FinanceEntry.amount_aud), 0))
        .where(FinanceEntry.status.notin_(["Paid", "Rejected"]))
    ).scalar()
    assert float(outstanding) == 450.50  # the Paid row is excluded
