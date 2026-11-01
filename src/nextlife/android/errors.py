"""Error types of the ANDROID bridge (TZ M1 part B).

Semantics (TZ M1 B.2/B.3):
- offline cable / phone reboot / logged-out account — standard events that
  put the bridge into BLOCKED with a reason and a notification (B.2);
- a result without evidence is a failure (B.3);
- an unexpected screen gives BLOCKED, the bridge never tries to pass it
  (B.3 property 3);
- daily caps come from the config (Owner confirmation 8) (B.3 property 4).
"""

from nextlife.core.exceptions import NextLifeError


class AndroidBridgeError(NextLifeError):
    """Base error for the Android bridge."""


class AndroidDeviceUnavailableError(AndroidBridgeError):
    """Phone/adb node unavailable (cable unplugged, reboot, offline).

    Standard event per TZ M1 B.2: the bridge transitions to BLOCKED with a
    reason and an owner notification, it does NOT retry silently.
    """


class BridgeDisabledError(AndroidBridgeError):
    """The ANDROID channel is disabled in config (no dedicated phone yet)."""


class BridgeTransportMissingError(AndroidBridgeError):
    """No ADB transport provided (TZ M1 B.2: BLOCKED-EXTERNAL until the
    dedicated phone and the adb node on Tailscale are provided,
    owner confirmations 5 and 6)."""


class BridgeEvidenceMissingError(AndroidBridgeError):
    """A bridge call returned without the mandatory evidence (B.4):
    missing screenshot / hierarchy dump / version / timestamps — FAIL."""


class UnknownScreenError(AndroidBridgeError):
    """Unexpected screen (captcha, identity check, update prompt, consent
    dialog, unknown window) — BLOCKED, not scenario continuation (B.3)."""


class BridgeMutexTimeoutError(AndroidBridgeError):
    """Could not acquire the global `android` queue mutex within the wait
    window (B.3 property 1)."""


class DailyCapExceededError(AndroidBridgeError):
    """Daily action cap for the marketplace exhausted or not configured
    (B.2/B.3 property 4; caps are set by the Owner, confirmation 8)."""


# --------------------------------------------------------------------- #
# Broker task statuses (TZ Marketplaces): the broker answers with a task
# status + reason, NOT a connector error. The transport maps each HTTP
# status to a distinct, named outcome so the caller can react (retry /
# reschedule / block) instead of treating every non-200 as "device down".
# --------------------------------------------------------------------- #


class BrokerTaskStatusError(AndroidBridgeError):
    """Base for a broker task-status response (423/403/409/503)."""


class BrokerMaintenanceError(BrokerTaskStatusError):
    """Broker 423: the owner is servicing the phone (maintenance mode).
    Retry later — a task status, not a connector failure."""


class BrokerLimitExceededError(BrokerTaskStatusError):
    """Broker 403: the platform's rate limit (min interval / daily leases)
    is hit. Reschedule the task — a task status, not a connector failure."""


class BrokerAppNotInstalledError(BrokerTaskStatusError):
    """Broker 409: the platform's app is not installed on the phone.
    BLOCKED with a reason — a task status, not a connector failure."""
