from datetime import date, datetime, timedelta

from tracking_checker import models as M
from tracking_checker.checks import rules
from tracking_checker.checks import run as run_checks
from tracking_checker.config import Settings

TODAY = date(2026, 9, 18)


def res(status=M.DELIVERED, delivered=datetime(2026, 9, 5, 14, 0), signed="J SMITH", left="Front Door",
        dest="Dallas, TX 75261", est="", attempts=0, events=None, ship=datetime(2026, 9, 1)):
    r = M.TrackingResult("1Z999AA10123456784", M.UPS, status=status, delivered_at=delivered if status == M.DELIVERED else None,
                         destination=dest, estimated_delivery=est, attempts=attempts, ship_date=ship)
    r.events = events or [M.TrackingEvent(datetime(2026, 9, 5, 14, 0), "Delivered", "Dallas, TX")]
    if status == M.DELIVERED:
        r.pod = M.ProofOfDelivery(signed_by=signed, left_at=left, address=dest)
    return r


def cols(text):
    rs, unknown = rules.interpret(text, TODAY)
    return {r.column: r for r in rs}, unknown


def test_dates_and_not_delivered():
    c, unknown = cols("Flag anything not delivered yet. Delivered after 9/1/2026. Delivered between Sept 1 and Sept 3.")
    assert not unknown
    assert c["Not Yet Delivered"].fn(res(status=M.IN_TRANSIT)) == "Yes"
    assert c["Delivered After 2026-09-01"].fn(res()) == "Yes"
    assert c["Delivered 2026-09-01 to 2026-09-03"].fn(res()) == "No"


def test_signed_by_and_no_signature():
    c, _ = cols("signed by smith; no signature")
    assert c["Signed By Contains 'smith'"].fn(res()) == "Yes"
    assert c["No Signature Name"].fn(res(signed="")) == "Yes"


def test_destination_state_name_and_left_at():
    c, _ = cols("delivered to Texas. left at the front door")
    assert c["Destination Is TX"].fn(res()) == "Yes"
    assert c["Destination Is TX"].fn(res(dest="Denver, CO 80249")) == "No"
    assert c["Left At Contains 'front door'"].fn(res()) == "Yes"


def test_stale_and_transit_and_attempts():
    old = [M.TrackingEvent(datetime.now() - timedelta(days=6), "Arrived at facility", "Memphis, TN")]
    c, _ = cols("no update in 3 days. in transit more than 5 days. more than 1 attempt")
    assert c["No Update in 3+ Days"].fn(res(status=M.IN_TRANSIT, events=old)) == "Yes"
    assert c["Transit Over 5 Days"].fn(res(ship=datetime(2026, 8, 20))) == "Yes"
    assert c["Delivery Attempts > 1"].fn(res(attempts=2)) == "Yes"


def test_late_vs_estimate():
    c, _ = cols("anything delivered late?")
    assert c["Late vs Estimate"].fn(res(est="2026-09-03")) == "Yes"
    assert c["Late vs Estimate"].fn(res()) == "Unknown"


def test_unrecognised_is_reported():
    _, unknown = cols("what colour was the box")
    assert unknown == ["what colour was the box"]


def test_run_checks_adds_match_columns():
    s = Settings()
    s.claude_enabled = False
    rs = [res(), res(status=M.IN_TRANSIT)]
    out = run_checks("not delivered yet. what colour was the box", rs, s)
    assert out.columns[-2:] == ["Matches Your Check", "Check Notes"]
    assert [r.extra["Matches Your Check"] for r in rs] == ["No", "Yes"]
    assert any("colour" in n for n in out.notes)
