from tracking_checker import detect
from tracking_checker.detect import HIGH, LOW, MEDIUM, classify, scan_cell
from tracking_checker.selftest import fedex12, mod10, ups_number


def test_ups_known_valid_number():
    d = classify("1Z999AA10123456784")
    assert d.carriers == ["UPS"] and d.confidence == HIGH


def test_ups_bad_check_digit_is_medium():
    assert classify("1Z999AA10123456785").confidence == MEDIUM


def test_fedex_classic_example():
    d = classify("123456789012")
    assert d.carriers == ["FedEx"] and d.confidence == HIGH


def test_usps_impb_and_spaces():
    n = mod10("9400" + "1118992231000000"[:17].ljust(17, "3"))
    spaced = " ".join(n[i:i + 4] for i in range(0, len(n), 4))
    d = classify(spaced)
    assert d.number == n and d.carriers == ["USPS"] and d.confidence == HIGH


def test_usps_92_prefix_also_tries_fedex():
    n = mod10("92" + "1" * 19)
    assert classify(n).carriers == ["USPS", "FedEx"]


def test_usps_420_zip_prefix_is_stripped():
    inner = mod10("9400" + "5" * 17)
    d = classify("42006902" + inner)
    assert d.number == inner and "420" in d.reason


def test_s10_international():
    d = classify("EC123456785US")
    assert d.carriers == ["USPS"]


def test_ten_digit_is_low_and_filtered_by_default():
    assert classify("5552013344").confidence == LOW
    assert scan_cell("5552013344").detections == []


def test_generated_numbers_all_high():
    assert classify(ups_number("AB1234567890123")).confidence == HIGH
    assert classify(fedex12("27777777777")).confidence == HIGH


def test_scan_cell_finds_multiple_numbers_in_text():
    t = "boxes 1Z999AA10123456784 and 123456789012 shipped"
    nums = [d.number for d in scan_cell(t).detections]
    assert nums == ["1Z999AA10123456784", "123456789012"]


def test_scan_cell_numeric_values():
    assert scan_cell(123456789012).detections[0].carriers == ["FedEx"]
    assert scan_cell(123456789012.0).detections[0].number == "123456789012"


def test_precision_loss_is_reported():
    s = scan_cell(9.400111899223e21)
    assert not s.detections and "15th" in s.problem
    assert "15th" in scan_cell("9.40011E+21").problem


def test_carrier_hint_text():
    assert detect.carrier_from_text("Fed Ex Ground") == "FedEx"
    assert detect.carrier_from_text("UPS Next Day") == "UPS"
    assert detect.carrier_from_text("US Postal Service") == "USPS"
    assert detect.carrier_from_text("DHL") is None
