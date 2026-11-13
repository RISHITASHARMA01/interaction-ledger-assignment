"""Derived facts: linking, calls, effective dispositions, buckets.

Pure functions over plain records, so the rules most likely to break are unit-testable without a
database. Everything here is recomputed from the ledger on every report, which is what makes
"an orphan after drop_1 counts once its lead arrives in drop_2" automatically true.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import Disposition

INITIATED = "call.initiated"
TELEPHONY_FAILED = "call.telephony_failed"
CONNECTED = "call.connected"
DISPOSITION_SET = "disposition.set"
DISPOSITION_OVERRIDE = "disposition.override"

YET_TO_REACH_OUT = "yet_to_reach_out"
COMPLETED = "completed"
TELEPHONY_FAILED_BUCKET = "in_progress_telephony_failed"
DIALED = "in_progress_dialed"
BUCKETS = (YET_TO_REACH_OUT, TELEPHONY_FAILED_BUCKET, DIALED, COMPLETED)


@dataclass(frozen=True)
class LeadRec:
    lead_id: int
    workflow_id: str
    phone_token: str
    external_ref: str
    batch_id: str
    phone_last4: str


@dataclass(frozen=True)
class EventRec:
    event_id: str
    event_type: str | None
    workflow_id: str | None
    call_id: str | None
    occurred_at_us: int
    phone_token: str | None
    disposition_code: str | None


@dataclass
class Call:
    call_key: tuple
    lead_id: int
    initiated_us: int
    connected: bool = False
    telephony_failed: bool = False
    effective_disposition: str | None = None


@dataclass
class LeadFacts:
    lead: LeadRec
    calls: list[Call] = field(default_factory=list)
    bucket: str = YET_TO_REACH_OUT
    connected_calls: int = 0
    last_call_us: int | None = None
    current_disposition: str | None = None
    ever_success: bool = False


@dataclass
class Facts:
    leads: dict[int, LeadFacts]
    calls: list[Call]
    orphan_events: int
    unknown_disposition_events: int


def _order(e: EventRec) -> tuple:
    """Deterministic event order: time, then event_id as a tie-breaker."""
    return (e.occurred_at_us, e.event_id)


def link_events(leads: list[LeadRec], events: list[EventRec]) -> dict[str, int | None]:
    """event_id -> lead_id (None == orphan)."""
    by_phone = {(l.workflow_id, l.phone_token): l.lead_id for l in leads}
    link: dict[str, int | None] = {}

    # Pass 1: events that carry a phone link by (workflow_id, phone).
    for e in events:
        if e.event_type != DISPOSITION_OVERRIDE:
            link[e.event_id] = (by_phone.get((e.workflow_id, e.phone_token))
                                if e.phone_token else None)

    # A call's lead: its (earliest) call.initiated's lead if linked, else the lead of its
    # earliest linked non-override event.
    call_lead: dict[tuple, tuple] = {}  # (wf, call_id) -> (priority, order, lead_id)
    for e in events:
        lead_id = link.get(e.event_id)
        if lead_id is None or e.call_id is None or e.event_type == DISPOSITION_OVERRIDE:
            continue
        cand = (0 if e.event_type == INITIATED else 1, _order(e), lead_id)
        key = (e.workflow_id, e.call_id)
        if key not in call_lead or cand < call_lead[key]:
            call_lead[key] = cand

    # Pass 2: overrides have no phone; they link through call_id.
    for e in events:
        if e.event_type == DISPOSITION_OVERRIDE:
            hit = call_lead.get((e.workflow_id, e.call_id)) if e.call_id else None
            link[e.event_id] = hit[2] if hit else None
    return link


def bucket_for(calls: list[Call], catalogue: dict[str, Disposition]) -> str:
    if not calls:
        return YET_TO_REACH_OUT
    if any(c.effective_disposition and catalogue[c.effective_disposition].is_terminal
           for c in calls):
        return COMPLETED
    if all(c.telephony_failed for c in calls):
        return TELEPHONY_FAILED_BUCKET
    return DIALED


def effective_disposition(sets: list[EventRec], overrides: list[EventRec]) -> str | None:
    """Latest override by occurred_at; else latest disposition.set; else None."""
    if overrides:
        return max(overrides, key=_order).disposition_code
    if sets:
        return max(sets, key=_order).disposition_code
    return None


def derive(leads: list[LeadRec], events: list[EventRec],
           dispositions: dict[str, dict[str, Disposition]]) -> Facts:
    link = link_events(leads, events)
    orphans = sum(1 for e in events if link[e.event_id] is None)

    # Linked events grouped per call. Unknown dispositions are counted and dropped.
    unknown = 0
    initiated: dict[tuple, EventRec] = {}
    per_call: dict[tuple, list[EventRec]] = defaultdict(list)
    for e in events:
        lead_id = link[e.event_id]
        if lead_id is None:
            continue
        if e.event_type in (DISPOSITION_SET, DISPOSITION_OVERRIDE):
            if e.disposition_code not in dispositions.get(e.workflow_id, {}):
                unknown += 1
                continue
        # An event without a call_id cannot belong to any call other than itself.
        key = (e.workflow_id, e.call_id) if e.call_id is not None else ("", e.event_id)
        if e.event_type == INITIATED:
            if key not in initiated or _order(e) < _order(initiated[key]):
                initiated[key] = e
        else:
            per_call[key].append(e)

    lead_by_id = {l.lead_id: l for l in leads}
    facts = {l.lead_id: LeadFacts(l) for l in leads}
    calls: list[Call] = []
    for key, ini in initiated.items():
        lead_id = link[ini.event_id]
        # Only events that link to the same lead as the call.initiated belong to the call.
        evs = [e for e in per_call.get(key, ()) if link[e.event_id] == lead_id]
        call = Call(key, lead_id, ini.occurred_at_us)
        call.connected = any(e.event_type == CONNECTED for e in evs)
        call.telephony_failed = any(e.event_type == TELEPHONY_FAILED for e in evs)
        call.effective_disposition = effective_disposition(
            [e for e in evs if e.event_type == DISPOSITION_SET],
            [e for e in evs if e.event_type == DISPOSITION_OVERRIDE])
        calls.append(call)
        facts[lead_id].calls.append(call)

    for lf in facts.values():
        catalogue = dispositions.get(lead_by_id[lf.lead.lead_id].workflow_id, {})
        lf.calls.sort(key=lambda c: (c.initiated_us, c.call_key))
        lf.bucket = bucket_for(lf.calls, catalogue)
        lf.connected_calls = sum(c.connected for c in lf.calls)
        if lf.calls:
            lf.last_call_us = lf.calls[-1].initiated_us
        with_disp = [c for c in lf.calls if c.effective_disposition]
        lf.current_disposition = with_disp[-1].effective_disposition if with_disp else None
        lf.ever_success = any(catalogue[c.effective_disposition].is_success for c in with_disp)

    return Facts(facts, calls, orphans, unknown)
