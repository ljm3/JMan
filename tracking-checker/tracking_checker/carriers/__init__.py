from __future__ import annotations

import threading

from .. import config
from ..models import FEDEX, UPS, USPS
from .base import Carrier, CarrierError, NotConfigured, NotFound
from .demo import DEMO_SOURCE, DemoCarrier
from .fedex import FedExCarrier
from .ups import UPSCarrier
from .usps import USPSCarrier

CLASSES = {UPS: UPSCarrier, FEDEX: FedExCarrier, USPS: USPSCarrier}


def _web_class(carrier):
    from .web.fedex_web import FedExWeb
    from .web.ups_web import UPSWeb
    from .web.usps_web import USPSWeb
    return {UPS: UPSWeb, FEDEX: FedExWeb, USPS: USPSWeb}.get(carrier)


def build(carrier: str, settings, demo: bool = False, cancel: threading.Event | None = None,
          browser=None, capture_pod: bool = True):
    """Return an API client, a website client, a DemoCarrier, or raise NotConfigured.

    browser: a carriers.web.LazyBrowser shared by every website client in the run.
    """
    if demo:
        return DemoCarrier(carrier)
    if carrier not in CLASSES:
        raise NotConfigured(f"{carrier} is not a supported carrier (UPS, FedEx and USPS are).")
    cs = settings.carrier(carrier)
    if not cs.enabled:
        raise NotConfigured(f"{carrier} is turned off in Settings.")
    if config.lookup_method(carrier, settings) == config.WEBSITE:
        if browser is None:
            raise NotConfigured(f"{carrier} is set to website lookups but no browser is available.")
        return _web_class(carrier)(browser, settings, capture_pod, cancel)
    cid, secret = config.carrier_credentials(carrier)
    return CLASSES[carrier](cid, secret, cs, cancel)


def tracking_url(carrier: str, number: str) -> str:
    cls = CLASSES.get(carrier)
    return cls.tracking_url(number) if cls else ""


__all__ = ["build", "tracking_url", "Carrier", "CarrierError", "NotConfigured", "NotFound", "DEMO_SOURCE", "DemoCarrier"]
