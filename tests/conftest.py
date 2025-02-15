"""
CA Gateway test configuration.


Environment variables used:

    EPICS_BASE
    EPICS_HOST_ARCH
    GATEWAY_PVLIST
    GATEWAY_ROOT
    IOC_EPICS_BASE or EPICS_BASE
    VERBOSE
    VERBOSE_GATEWAY

"""

import contextlib
import functools
import logging
import os
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from typing import ContextManager, Optional

import epics
import pytest

logger = logging.getLogger(__name__)

libca_so = os.path.join(
    os.environ["EPICS_BASE"], "lib", os.environ["EPICS_HOST_ARCH"], "libca.so"
)
if "PYEPICS_LIBCA" not in os.environ and os.path.exists(libca_so):
    os.environ["PYEPICS_LIBCA"] = libca_so

# CA ports to use
default_ioc_port = 12782
default_gw_port = 12783
default_access = os.environ.get("GATEWAY_ACCESS", "default_access.txt")
default_pvlist = os.environ.get("GATEWAY_PVLIST", "pvlist_bre.txt")

verbose = os.environ.get("VERBOSE", "").lower().startswith("y")

# Do we want debug logging from the gateway
if "VERBOSE_GATEWAY" not in os.environ:
    verbose_gateway = False
    gateway_debug_level = 10
else:
    verbose_gateway = True
    try:
        gateway_debug_level = int(os.environ["VERBOSE_GATEWAY"])
    except ValueError:
        gateway_debug_level = 10


hostArch = os.environ.get("EPICS_HOST_ARCH", os.environ.get("T_A"))
if hostArch is None:
    raise RuntimeError("EPICS_HOST_ARCH and T_A not set")

try:
    gateway_executable = os.path.join(
        os.environ["GATEWAY_ROOT"], "bin", hostArch, "gateway"
    )
except KeyError:
    raise RuntimeError("Set GATEWAY_ROOT to point to the gateway to test") from None

if not os.path.exists(gateway_executable):
    raise RuntimeError(
        f"Gateway executable {gateway_executable} does not exist; set GW_SITE_TOP."
    )

if "IOC_EPICS_BASE" in os.environ:
    ioc_executable = os.path.join(
        os.environ["IOC_EPICS_BASE"], "bin", hostArch, "softIoc"
    )
elif "EPICS_BASE" in os.environ:
    ioc_executable = os.path.join(os.environ["EPICS_BASE"], "bin", hostArch, "softIoc")
else:
    ioc_executable = shutil.which("softIoc")

if not ioc_executable or not os.path.exists(ioc_executable):
    raise RuntimeError(f"softIoc path {ioc_executable} does not exist")


@contextlib.contextmanager
def run_process(
    cmd: list[str],
    env: dict[str, str],
    verbose: bool = False,
    interactive: bool = False,
    startup_time: float = 0.5,
):
    """Run ``cmd`` and yield a subprocess.POpen instance."""
    logger.info("Running: %s (verbose=%s)", " ".join(cmd), verbose)

    with open(os.devnull, "wb") as dev_null:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=dev_null if not verbose else None,
            stderr=subprocess.STDOUT,
        )
        # Arbitrary startup time
        time.sleep(startup_time)
        try:
            yield proc
        finally:
            if interactive:
                logger.debug("Exiting interactive process %s", cmd[0])
                proc.stdin.close()
            else:
                logger.debug("Terminating non-interactive process %s", cmd[0])
                proc.terminate()
            proc.wait()


@contextlib.contextmanager
def run_ioc(
    *arglist: str,
    startup_time: float = 0.5,
    db_file: Optional[str] = "test.db",
    dbd_file: Optional[str] = None,
    ioc_port: int = default_ioc_port,
) -> ContextManager[subprocess.Popen]:
    """Starts a test IOC."""
    env = dict(os.environ)
    env["EPICS_CA_SERVER_PORT"] = str(ioc_port)
    env["EPICS_CA_ADDR_LIST"] = "localhost"
    env["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
    # IOC_ environment overrides
    for v in list(os.environ.keys()):
        if v.startswith("IOC_"):
            env[v.replace("IOC_", "", 1)] = os.environ[v]

    cmd = [ioc_executable]
    if dbd_file is not None:
        cmd.extend(["-D", dbd_file])

    if db_file is not None:
        cmd.extend(["-d", db_file])

    cmd.extend(arglist)

    with run_process(cmd, env, verbose=verbose, interactive=True) as proc:
        yield proc


@contextlib.contextmanager
def run_gateway(
    *extra_args: str,
    access: str = default_access,
    pvlist: str = default_pvlist,
    ioc_port: int = default_ioc_port,
    gateway_port: int = default_gw_port,
    verbose: bool = verbose_gateway,
    stats_prefix: str = "gwtest",
) -> ContextManager[subprocess.Popen]:
    """Starts the gateway."""
    cmd = [
        gateway_executable,
        "-sip", "localhost",
        "-sport", str(gateway_port),
        "-cip", "localhost",
        "-cport", str(ioc_port),
        "-access", access,
        "-pvlist", pvlist,
        "-archive",
        "-prefix", stats_prefix,
    ]
    cmd.extend(extra_args)

    if verbose:
        cmd.extend(["-debug", str(gateway_debug_level)])

    with run_process(cmd, os.environ, verbose=verbose, interactive=False) as proc:
        yield proc


@contextlib.contextmanager
def local_channel_access(
    *ports: int
):
    if not len(ports):
        ports = [default_ioc_port, default_gw_port]

    address_list = [f"localhost:{port}" for port in ports]
    os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
    os.environ["EPICS_CA_ADDR_LIST"] = " ".join(address_list)
    epics.ca.initialize_libca()
    try:
        yield
    finally:
        # This may lead to instability - probably should only run one test per
        # process
        epics.ca.clear_cache()
        epics.ca.finalize_libca()
        time.sleep(0.5)


@contextlib.contextmanager
def standard_test_environment(
    access: str = default_access,
    pvlist: str = default_pvlist,
    db_file: str = "test.db",
    dbd_file: Optional[str] = None,
):
    with run_gateway(access=access, pvlist=pvlist):
        with run_ioc(db_file=db_file, dbd_file=dbd_file):
            with local_channel_access():
                yield


def standard_test_environment_decorator(
    func=None,
    access: str = default_access,
    pvlist: str = default_pvlist,
    db_file: str = "test.db",
    dbd_file: Optional[str] = None,
):
    def wrapper(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            with standard_test_environment(
                access=access, pvlist=pvlist, db_file=db_file, dbd_file=dbd_file
            ):
                return func(*args, **kwargs)
        return wrapped

    if func is not None:
        return wrapper(func)

    return wrapper


def get_pv_pair(
    pvname: str, *,
    ioc_prefix: str = "ioc:",
    gateway_prefix: str = "gateway:",
    ioc_callback: Optional[callable] = None,
    gateway_callback: Optional[callable] = None,
    **kwargs
) -> tuple[epics.PV, epics.PV]:
    """Get a PV pair - a direct PV and a gateway PV."""
    ioc_pv = epics.PV(ioc_prefix + pvname, **kwargs)
    if ioc_callback is not None:
        ioc_pv.add_callback(ioc_callback)
    ioc_pv.wait_for_connection()

    gateway_pv = epics.PV(gateway_prefix + pvname, **kwargs)
    if gateway_callback is not None:
        gateway_pv.add_callback(gateway_callback)
    gateway_pv.wait_for_connection()
    return (ioc_pv, gateway_pv)


class GatewayStats:
    vctotal: Optional[int] = None
    pvtotal: Optional[int] = None
    connected: Optional[int] = None
    active: Optional[int] = None
    inactive: Optional[int] = None

    def __init__(self, prefix="gwtest:"):
        self._vctotal = epics.ca.create_channel(f"{prefix}vctotal")
        self._pvtotal = epics.ca.create_channel(f"{prefix}pvtotal")
        self._connected = epics.ca.create_channel(f"{prefix}connected")
        self._active = epics.ca.create_channel(f"{prefix}active")
        self._inactive = epics.ca.create_channel(f"{prefix}inactive")
        self.update()

    def update(self):
        """Update gateway statistics."""
        self.vctotal = epics.ca.get(self._vctotal)
        self.pvtotal = epics.ca.get(self._pvtotal)
        self.connected = epics.ca.get(self._connected)
        self.active = epics.ca.get(self._active)
        self.inactive = epics.ca.get(self._inactive)


def get_prop_support():
    """Is DBE_PROPERTY supported?"""
    events_received_ioc = 0

    def on_change_ioc(**kwargs):
        nonlocal events_received_ioc
        events_received_ioc += 1

    with standard_test_environment():
        ioc = epics.PV("ioc:passive0", auto_monitor=epics.dbr.DBE_PROPERTY)
        ioc.add_callback(on_change_ioc)
        ioc.get()

        pvhigh = epics.PV("ioc:passive0.HIGH", auto_monitor=None)
        pvhigh.put(18.0, wait=True)
        time.sleep(0.05)

    return events_received_ioc == 2


@pytest.fixture(scope="module")
def prop_supported() -> bool:
    """Is DBE_PROPERTY supported?"""
    with ProcessPoolExecutor() as exec:
        future = exec.submit(get_prop_support)

    return future.result()


def compare_structures(gw_struct, ioc_struct) -> str:
    differences = []
    for key, ioc_value in ioc_struct.items():
        gateway_value = gw_struct[key]
        if key != "chid" and ioc_value != gateway_value:
            differences.append(
                f"Element '{key}' : GW has '{gateway_value}', IOC has '{ioc_value}'"
            )
    return "\n\t".join(differences)
