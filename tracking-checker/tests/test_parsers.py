"""Carrier response parsing, using payloads shaped like each carrier's documented responses."""
import base64

import pytest

from tracking_checker import models as M
from tracking_checker.carriers.base import NotFound, normalize_status
from tracking_checker.carriers.fedex import parse_fedex_batch
from tracking_checker.carriers.ups import parse_ups
from tracking_checker.carriers.usps import parse_usps

GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"

UPS_DELIVERED = {"trackResponse": {"shipment": [{"inquiryNumber": "1Z999AA10123456784", "package": [{
    "trackingNumber": "1Z999AA10123456784",
    "service": {"description": "UPS Ground"},
    "pickupDate": "20260901",
    "currentStatus": {"description": "Delivered", "code": "011"},
    "deliveryDate": [{"type": "DEL", "date": "20260905"}],
    "deliveryTime": {"type": "DEL", "endTime": "143200"},
    "deliveryInformation": {"location": "Front Door", "receivedBy": "SMITH",
                            "signature": {"image": base64.b64encode(GIF).decode()},
                            "pod": {"content": base64.b64encode(b"<html>POD</html>").decode()}},
    "packageAddress": [{"type": "ORIGIN", "address": {"city": "ATLANTA", "stateProvince": "GA", "countryCode": "US"}},
                       {"type": "DESTINATION", "address": {"city": "HARTFORD", "stateProvince": "CT",
                                                           "postalCode": "06101", "countryCode": "US"}}],
    "weight": {"unitOfMeasurement": "LBS", "weight": "12.50"},
    "activity": [
        {"location": {"address": {"city": "Hartford", "stateProvince": "CT", "countryCode": "US"}},
         "status": {"type": "D", "description": "DELIVERED", "code": "FS"}, "date": "20260905", "time": "143200"},
        {"location": {"address": {"city": "Hartford", "stateProvince": "CT", "countryCode": "US"}},
         "status": {"type": "I", "description": "Out For Delivery Today", "code": "OT"}, "date": "20260905", "time": "080100"},
        {"location": {"address": {"city": "Atlanta", "stateProvince": "GA", "countryCode": "US"}},
         "status": {"type": "P", "description": "Pickup Scan", "code": "PU"}, "date": "20260901", "time": "170000"},
    ]}]}]}}


def test_ups_delivered_with_pod():
    r = parse_ups("1Z999AA10123456784", UPS_DELIVERED)
    assert r.status == M.DELIVERED and r.carrier == "UPS" and r.service == "UPS Ground"
    assert r.delivered_at.strftime("%Y-%m-%d %H:%M") == "2026-09-05 14:32"
    assert r.origin == "Atlanta, GA" and r.destination == "Hartford, CT 06101"
    assert r.weight == "12.50 lbs" and len(r.events) == 3
    assert r.pod.signed_by == "SMITH" and r.pod.left_at == "Front Door"
    assert r.pod.signature_image == GIF and r.pod.document.startswith(b"<html>")
    assert r.days_in_transit() == 4.6          # pickup date 9/1 (no time) -> delivered 9/5 14:32


def test_ups_warning_without_package_is_not_found():
    data = {"trackResponse": {"shipment": [{"warnings": [{"code": "TW0001", "message": "Tracking Information Not Found"}]}]}}
    with pytest.raises(NotFound):
        parse_ups("1Z999AA10123456784", data)


FEDEX = {"output": {"completeTrackResults": [
    {"trackingNumber": "123456789012", "trackResults": [{
        "trackingNumberInfo": {"trackingNumber": "123456789012"},
        "latestStatusDetail": {"code": "DL", "derivedCode": "DL", "statusByLocale": "Delivered", "description": "Delivered",
                               "scanLocation": {"city": "MEMPHIS", "stateOrProvinceCode": "TN"}},
        "dateAndTimes": [{"type": "ACTUAL_DELIVERY", "dateTime": "2026-09-03T10:15:00-05:00"},
                         {"type": "SHIP", "dateTime": "2026-09-01T00:00:00-06:00"}],
        "serviceDetail": {"description": "FedEx Priority Overnight"},
        "deliveryDetails": {"receivedByName": "J.DOE", "deliveryAttempts": "1", "locationDescription": "FRONT_DESK",
                            "actualDeliveryAddress": {"city": "MEMPHIS", "stateOrProvinceCode": "TN", "countryCode": "US"}},
        "shipperInformation": {"address": {"city": "DENVER", "stateOrProvinceCode": "CO", "countryCode": "US"}},
        "recipientInformation": {"address": {"city": "MEMPHIS", "stateOrProvinceCode": "TN", "countryCode": "US"}},
        "packageDetails": {"weightAndDimensions": {"weight": [{"value": "3.0", "unit": "LB"}, {"value": "1.36", "unit": "KG"}]}},
        "scanEvents": [
            {"date": "2026-09-03T10:15:00-05:00", "eventDescription": "Delivered", "derivedStatusCode": "DL",
             "scanLocation": {"city": "MEMPHIS", "stateOrProvinceCode": "TN", "countryCode": "US"}},
            {"date": "2026-09-03T08:00:00-05:00", "eventDescription": "On FedEx vehicle for delivery",
             "derivedStatusCode": "OD", "scanLocation": {"city": "MEMPHIS", "stateOrProvinceCode": "TN"}},
        ]}]},
    {"trackingNumber": "999999999999", "trackResults": [{
        "error": {"code": "TRACKING.TRACKINGNUMBER.NOTFOUND", "message": "Tracking number cannot be found."}}]},
]}}


def test_fedex_batch():
    out = parse_fedex_batch(FEDEX, ["123456789012", "999999999999", "111111111111"])
    r = out["123456789012"]
    assert r.status == M.DELIVERED and r.service == "FedEx Priority Overnight"
    assert r.delivered_at.strftime("%Y-%m-%d %H:%M") == "2026-09-03 10:15"
    assert r.pod.signed_by == "J.DOE" and r.pod.left_at == "Front Desk" and r.attempts == 1
    assert r.origin == "Denver, CO" and r.weight == "3.0 lb"
    assert isinstance(out["999999999999"], NotFound)
    assert isinstance(out["111111111111"], NotFound)


USPS = {
    "trackingNumber": "9400111899223100000007", "statusCategory": "Delivered", "status": "Delivered, In/At Mailbox",
    "statusSummary": "Your item was delivered in or at the mailbox at 1:05 pm on September 4, 2026 in DALLAS, TX 75261.",
    "mailClass": "USPS Ground Advantage", "originCity": "PHOENIX", "originState": "AZ", "originZIP": "85034",
    "destinationCity": "DALLAS", "destinationState": "TX", "destinationZIP": "75261",
    "trackingEvents": [
        {"eventType": "Delivered, In/At Mailbox", "eventTimestamp": "2026-09-04T13:05:00", "eventCity": "DALLAS",
         "eventState": "TX", "eventZIP": "75261", "eventCountry": "", "name": "", "eventCode": "01"},
        {"eventType": "Out for Delivery", "eventTimestamp": "2026-09-04T07:10:00", "eventCity": "DALLAS",
         "eventState": "TX", "eventZIP": "75261", "eventCode": "OF"},
        {"eventType": "USPS in possession of item", "eventTimestamp": "2026-08-31T16:00:00", "eventCity": "PHOENIX",
         "eventState": "AZ", "eventZIP": "85034", "eventCode": "03"},
    ]}


def test_usps_delivered():
    r = parse_usps("9400111899223100000007", USPS)
    assert r.status == M.DELIVERED and r.service == "USPS Ground Advantage"
    assert r.delivered_at.strftime("%Y-%m-%d %H:%M") == "2026-09-04 13:05"
    assert r.pod.left_at == "Mailbox" and r.destination == "Dallas, TX 75261"
    assert r.ship_date.strftime("%Y-%m-%d") == "2026-08-31"


@pytest.mark.parametrize("text,code,want", [
    ("Delivered", "", M.DELIVERED), ("On FedEx vehicle for delivery", "", M.OUT_FOR_DELIVERY),
    ("Delivery attempted - no access", "", M.EXCEPTION), ("Shipment information sent to FedEx", "", M.LABEL_CREATED),
    ("Arrived at Facility", "", M.IN_TRANSIT), ("Returned to sender", "", M.RETURNED),
    ("Available for Pickup", "", M.PICKUP_READY), ("", "DL", M.DELIVERED), ("Pre-Shipment", "", M.LABEL_CREATED),
    ("Not delivered - business closed", "", M.EXCEPTION),
])
def test_normalize_status(text, code, want):
    assert normalize_status(text, code) == want
