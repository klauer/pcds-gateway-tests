#!/usr/bin/env python
import logging
import os
import time

import conftest
import pytest
from epics import ca, dbr

logger = logging.getLogger(__name__)


@pytest.mark.skipif(
    os.environ.get("BASE") == "3.14",
    reason="updates of CTRL structures are buggy in 3.14 PCAS",
)
@conftest.standard_test_environment_decorator
def test_ctrl_struct_value_monitor():
    """
    Testing structures going through the Gateway.

    Set up a connection directly and through the Gateway - change a
    property - check consistency of data

    Monitor PV (value events) through GW - change value and properties
    directly - check CTRL structure consistency
    """
    events_received_ioc = 0
    events_received_gw = 0
    ioc_struct = {}
    gw_struct = {}

    def on_change_ioc(pvname=None, **kws):
        nonlocal events_received_ioc
        nonlocal ioc_struct
        events_received_ioc += 1
        ioc_struct = kws
        logger.info(
            "New IOC Value for %s value=%s, kw=%s\n",
            pvname,
            str(kws["value"]),
            repr(kws),
        )

    def on_change_gw(pvname=None, **kws):
        nonlocal gw_struct
        nonlocal events_received_gw
        events_received_gw += 1
        gw_struct = kws
        logger.info(
            "New GW Value for %s value=%s, kw=%s\n",
            pvname,
            str(kws["value"]),
            repr(kws),
        )

    # gwcachetest is an ai record with full set of alarm limits: -100 -10 10 100
    gw = ca.create_channel("gateway:gwcachetest")
    connected = ca.connect_channel(gw, timeout=0.5)
    assert connected, "Could not connect to gateway channel " + ca.name(gw)
    (gw_cbref, gw_uaref, gw_eventid) = ca.create_subscription(
        gw, mask=dbr.DBE_VALUE, use_ctrl=True, callback=on_change_gw
    )

    ioc = ca.create_channel("ioc:gwcachetest")
    connected = ca.connect_channel(ioc, timeout=0.5)
    assert connected, "Could not connect to ioc channel " + ca.name(ioc)
    (ioc_cbref, ioc_uaref, ioc_eventid) = ca.create_subscription(
        ioc, mask=dbr.DBE_VALUE, use_ctrl=True, callback=on_change_ioc
    )

    # set value on IOC
    ioc_value = ca.create_channel("ioc:gwcachetest")
    ca.put(ioc_value, 10.0, wait=True)
    time.sleep(0.1)

    assert events_received_ioc == events_received_gw, (
        f"After setting value, no. of received updates differ: "
        f"GW {events_received_gw}, IOC {events_received_ioc}"
    )

    differences = conftest.compare_structures(gw_struct, ioc_struct)
    assert not differences, (
        f"At update {events_received_ioc} (change value), "
        f"received structure updates differ:\n\t{differences}"
    )

    # set property on IOC
    ioc_hihi = ca.create_channel("ioc:gwcachetest.HIHI")
    ca.put(ioc_hihi, 123.0, wait=True)
    ca.put(ioc_value, 11.0, wait=True)  # trigger update
    time.sleep(0.1)

    assert events_received_ioc == events_received_gw, (
        f"After setting property, no. of received updates differ: "
        f"GW {events_received_gw}, IOC {events_received_ioc}"
    )

    differences = conftest.compare_structures(gw_struct, ioc_struct)
    assert not differences, (
        f"At update {events_received_ioc} (change property), received structure "
        f"updates differ:\n\t{differences}"
    )
