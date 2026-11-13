from ledger.facts import (COMPLETED, DIALED, TELEPHONY_FAILED_BUCKET, YET_TO_REACH_OUT, EventRec,
                          LeadRec, derive)

WF = "motor_renewal"


def lead(i=1, token="t1"):
    return LeadRec(i, WF, token, f"REF{i}", "batch_001", "1234")


class Ev:
    """Tiny builder: Ev(call, minute).initiated() etc."""
    seq = 0

    def __init__(self, call="c1", token="t1", wf=WF):
        self.call, self.token, self.wf = call, token, wf

    def _e(self, etype, minute, code=None, token="same"):
        Ev.seq += 1
        return EventRec(f"evt_{Ev.seq:06d}", etype, self.wf, self.call,
                        minute * 60_000_000, self.token if token == "same" else token, code)

    def initiated(self, m=0): return self._e("call.initiated", m)
    def connected(self, m=1): return self._e("call.connected", m)
    def failed(self, m=1): return self._e("call.telephony_failed", m)
    def ended(self, m=2): return self._e("call.ended", m)
    def set(self, code, m=3): return self._e("disposition.set", m, code)
    def override(self, code, m=10): return self._e("disposition.override", m, code, token=None)


def run(config, leads, events):
    return derive(leads, events, config.dispositions)


# ------------------------------------------------------------------ effective disposition
def test_override_beats_set_even_if_set_is_later(config):
    c = Ev()
    f = run(config, [lead()], [c.initiated(), c.connected(), c.set("callback_requested", m=50),
                               c.override("renewed", m=10)])
    assert f.leads[1].calls[0].effective_disposition == "renewed"
    assert f.leads[1].ever_success and f.leads[1].bucket == COMPLETED


def test_latest_override_and_latest_set_win(config):
    c = Ev()
    f = run(config, [lead()], [c.initiated(), c.set("renewed", 5), c.set("callback_requested", 3)])
    assert f.leads[1].calls[0].effective_disposition == "renewed"
    f = run(config, [lead()], [c.initiated(), c.set("renewed", 5),
                               c.override("not_interested", 40), c.override("payment_link_sent", 30)])
    assert f.leads[1].calls[0].effective_disposition == "not_interested"


def test_override_links_through_call_id_and_arrival_order_is_irrelevant(config):
    c = Ev()
    evs = [c.override("renewed"), c.set("callback_requested"), c.connected(), c.initiated()]
    f = run(config, [lead()], evs)
    assert f.orphan_events == 0
    assert f.leads[1].current_disposition == "renewed"


def test_unknown_disposition_is_counted_and_ignored(config):
    c = Ev()
    f = run(config, [lead()], [c.initiated(), c.set("renewed", 3), c.set("RENEWD", 9),
                               c.override("feedback_captured")])  # other workflow's code
    assert f.unknown_disposition_events == 2
    assert f.leads[1].calls[0].effective_disposition == "renewed"


# ------------------------------------------------------------------ orphans
def test_orphans(config):
    known, unknown = Ev("c1", "t1"), Ev("c2", "nobody")
    no_phone = Ev("c3", None)
    other_wf = Ev("c4", "t1", wf="policy_feedback")
    evs = [known.initiated(), unknown.initiated(), unknown.override("renewed"),
           no_phone.initiated(), other_wf.initiated(), Ev("c9").override("renewed")]
    f = run(config, [lead()], evs)
    # unknown phone (2: incl. its override), no phone, wrong workflow, override for unknown call
    assert f.orphan_events == 5
    assert len(f.calls) == 1


def test_orphan_becomes_linked_once_lead_exists(config):
    c = Ev(token="late")
    evs = [c.initiated(), c.connected(), c.override("renewed")]
    assert run(config, [], evs).orphan_events == 3
    f = run(config, [lead(token="late")], evs)
    assert f.orphan_events == 0 and f.leads[1].ever_success


def test_unknown_disposition_on_orphan_counts_only_as_orphan(config):
    c = Ev(token="nobody")
    f = run(config, [lead()], [c.initiated(), c.set("RENEWD")])
    assert (f.orphan_events, f.unknown_disposition_events) == (2, 0)


# ------------------------------------------------------------------ calls & buckets
def test_call_needs_initiated(config):
    c = Ev()
    f = run(config, [lead()], [c.connected(), c.set("renewed")])
    assert f.leads[1].calls == [] and f.leads[1].bucket == YET_TO_REACH_OUT
    assert f.orphan_events == 0  # linked, just not a call


def test_bucket_precedence(config):
    a, b = Ev("a"), Ev("b")
    # all calls telephony-failed
    f = run(config, [lead()], [a.initiated(0), a.failed(1), b.initiated(100), b.failed(101)])
    assert f.leads[1].bucket == TELEPHONY_FAILED_BUCKET
    # one failed, one rang out -> dialed
    f = run(config, [lead()], [a.initiated(0), a.failed(1), b.initiated(100), b.ended(101)])
    assert f.leads[1].bucket == DIALED
    # terminal beats everything, even if the other call failed
    f = run(config, [lead()], [a.initiated(0), a.failed(1), b.initiated(100),
                               b.set("not_interested", 105)])
    assert f.leads[1].bucket == COMPLETED and not f.leads[1].ever_success
    # override can un-complete: terminal set overridden by non-terminal
    f = run(config, [lead()], [b.initiated(0), b.set("not_interested"), b.override("callback_requested")])
    assert f.leads[1].bucket == DIALED


def test_current_disposition_is_most_recent_call_that_has_one(config):
    a, b, c = Ev("a"), Ev("b"), Ev("c")
    f = run(config, [lead()], [
        a.initiated(0), a.set("callback_requested", 5),
        b.initiated(100), b.set("payment_link_sent", 105),
        c.initiated(200), c.ended(201),  # latest call, no disposition
    ])
    lf = f.leads[1]
    assert lf.current_disposition == "payment_link_sent"
    assert lf.last_call_us == 200 * 60_000_000
    assert (len(lf.calls), lf.connected_calls) == (3, 0)


def test_duplicate_initiated_for_same_call_counts_once(config):
    c = Ev()
    f = run(config, [lead()], [c.initiated(5), c.initiated(3)])
    assert len(f.calls) == 1 and f.calls[0].initiated_us == 3 * 60_000_000
