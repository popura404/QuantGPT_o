"""WorldQuant BRAIN API client.

Wraps the WQ BRAIN REST API for alpha simulation, quality checks, and
formal submission. Credentials are read from environment variables
WQ_BRAIN_EMAIL and WQ_BRAIN_PASSWORD.
"""

import logging
import os
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

API_BASE = "https://api.worldquantbrain.com"

SUBMIT_THRESHOLDS = {
    "sharpe": 1.25,
    "fitness": 1.0,
    "turnover_max": 0.7,
    "turnover_min": 0.01,
}

_POLL_INTERVAL = 10
_POLL_MAX_ATTEMPTS = 36
_CONCURRENT_BACKOFF = 30
_MAX_RETRIES = 5


_ACCOUNT_ENV = {
    "primary": ("WQ_BRAIN_EMAIL", "WQ_BRAIN_PASSWORD"),
    "alt": ("WQ_BRAIN_ALT_EMAIL", "WQ_BRAIN_ALT_PASSWORD"),
}


def is_configured(account: str | None = None) -> bool:
    if account:
        env_email, env_pwd = _ACCOUNT_ENV.get(account, _ACCOUNT_ENV["primary"])
        return bool(os.environ.get(env_email) and os.environ.get(env_pwd))
    return any(
        bool(os.environ.get(e) and os.environ.get(p))
        for e, p in _ACCOUNT_ENV.values()
    )


def configured_accounts() -> list[str]:
    return [
        name for name, (e, p) in _ACCOUNT_ENV.items()
        if os.environ.get(e) and os.environ.get(p)
    ]


def get_client(account: str = "primary") -> "WQBrainClient":
    env_email, env_pwd = _ACCOUNT_ENV.get(account, _ACCOUNT_ENV["primary"])
    return WQBrainClient(
        email=os.environ.get(env_email, ""),
        password=os.environ.get(env_pwd, ""),
    )


class WQBrainClient:
    def __init__(self, email: str | None = None, password: str | None = None):
        self.email = email or os.environ.get("WQ_BRAIN_EMAIL", "")
        self.password = password or os.environ.get("WQ_BRAIN_PASSWORD", "")
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.trust_env = False
            retry = Retry(total=3, connect=0, backoff_factor=1,
                          allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}), status_forcelist=[502, 503, 504])
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        return self._session

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    def authenticate(self, _max_retries: int = 5) -> bool:
        s = self._get_session()
        for attempt in range(_max_retries):
            r = s.post(
                f"{API_BASE}/authentication",
                auth=(self.email, self.password),
                timeout=(10, 30), allow_redirects=False,
            )
            if r.status_code == 429:
                retry = int(r.headers.get("Retry-After", "60"))
                logger.info(f"WQ auth rate-limited, waiting {retry}s (attempt {attempt + 1}/{_max_retries})")
                time.sleep(retry + 1)
                continue

            if r.status_code not in (200, 201):
                logger.error(f"WQ auth failed: HTTP {r.status_code}")
                return False

            data = r.json()
            if "inquiry" in data:
                logger.error("WQ auth requires biometric verification — log in via browser first")
                return False

            logger.info("WQ BRAIN authenticated")
            return True

        logger.error(f"WQ auth failed: rate-limited {_max_retries} times")
        return False

    def get_user_info(self) -> dict:
        r = self._get_session().get(f"{API_BASE}/users/self")
        return r.json() if r.status_code == 200 else {}

    @staticmethod
    def simulation_payload(expression: str, region: str = "USA", universe: str = "TOP3000",
                           delay: int = 1, decay: int = 0, neutralization: str = "SUBINDUSTRY",
                           truncation: float = 0.08) -> dict:
        return {"type": "REGULAR", "regular": expression, "settings": {
            "instrumentType": "EQUITY", "region": region, "universe": universe,
            "delay": delay, "decay": decay, "neutralization": neutralization,
            "truncation": truncation, "pasteurization": "ON", "unitHandling": "VERIFY",
            "nanHandling": "OFF", "language": "FASTEXPR", "visualization": False,
        }}

    @staticmethod
    def simulation_url(reference: str) -> str:
        url = urljoin(API_BASE + "/", reference)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != urlsplit(API_BASE).netloc
                or not parsed.path.startswith("/simulations/") or parsed.query or parsed.fragment):
            raise ValueError("Invalid WQ simulation reference")
        return url

    def start_simulation(self, expression: str, region: str = "USA", universe: str = "TOP3000",
                         delay: int = 1, decay: int = 0, neutralization: str = "SUBINDUSTRY",
                         truncation: float = 0.08) -> dict:
        """One POST. An uncertain response never authorizes another submission."""
        payload = self.simulation_payload(expression, region, universe, delay, decay, neutralization, truncation)
        try:
            response = self._get_session().post(f"{API_BASE}/simulations", json=payload,
                                                timeout=(10, 60), allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout) as exc:
            return {"ok": False, "status": "remote_outcome_unknown", "retryable": False,
                    "error": str(exc), "next_action": "reconcile_remote_request"}
        if response.status_code not in (200, 201, 202):
            unknown = response.status_code >= 500 or 300 <= response.status_code < 400
            return {"ok": False, "status": "remote_outcome_unknown" if unknown else "failed",
                    "retryable": response.status_code == 429, "status_code": response.status_code,
                    "error": f"HTTP {response.status_code}: {response.text[:300]}",
                    "next_action": "reconcile_remote_request" if unknown else "inspect_platform_rejection"}
        try:
            location = response.headers.get("Location", "")
            if not location:
                raise ValueError("No Location header in accepted response")
            url = self.simulation_url(location)
        except ValueError as exc:
            return {"ok": False, "status": "remote_outcome_unknown", "retryable": False,
                    "error": str(exc), "next_action": "reconcile_remote_request"}
        return {"ok": True, "status": "remote_pending", "remote_run_ref": url,
                "simulation_id": url.rsplit("/", 1)[-1]}

    def simulate(self, expression: str, region: str = "USA", universe: str = "TOP3000",
                 delay: int = 1, decay: int = 0, neutralization: str = "SUBINDUSTRY",
                 truncation: float = 0.08, progress_callback: Callable[[int, str], None] | None = None,
                 accepted_callback: Callable[[dict], None] | None = None,
                 cancel_check: Callable[[], bool] | None = None) -> dict:
        accepted = self.start_simulation(expression, region, universe, delay, decay, neutralization, truncation)
        if not accepted.get("ok"):
            return accepted
        if accepted_callback:
            accepted_callback(accepted)  # Persist the reference before the first GET.
        return self.poll_simulation(accepted["remote_run_ref"], expression=expression,
                                    progress_callback=progress_callback, cancel_check=cancel_check)

    def poll_simulation(self, remote_run_ref: str, *, expression: str = "",
                        progress_callback: Callable[[int, str], None] | None = None,
                        cancel_check: Callable[[], bool] | None = None,
                        max_polls: int | None = None) -> dict:
        """Resume using GET only; a timeout retains the reference for later reconciliation."""
        url = self.simulation_url(remote_run_ref)
        for i in range(_POLL_MAX_ATTEMPTS if max_polls is None else max_polls):
            if cancel_check and cancel_check():
                return {"ok": False, "status": "local_wait_cancelled", "remote_run_ref": url,
                        "remote_cancel_confirmed": False, "retryable": False, "error": "Local wait cancelled"}
            try:
                response = self._get_session().get(url, timeout=(10, 30), allow_redirects=False)
                data = response.json() if response.status_code == 200 else {}
                if not isinstance(data, dict):
                    data = {}
            except (requests.RequestException, ValueError):
                data = {}
            status = str(data.get("status", "")).upper()
            if progress_callback:
                progress = data.get("progress", 0)
                try:
                    pct = int(progress * 100) if isinstance(progress, float) and progress <= 1 else int(progress or 0)
                except (ValueError, TypeError, OverflowError):
                    pct = 0
                progress_callback(min(pct, 99), f"模拟进行中 ({pct}%)")
            if status in ("DONE", "COMPLETE"):
                alpha_raw = data.get("alpha", "")
                alpha_id = str(alpha_raw).split("/")[-1] if alpha_raw else None
                is_data, oos_data = data.get("is", {}), data.get("oos", {})
                alpha_detail = {}
                if alpha_id and not is_data:
                    alpha_detail = self._fetch_alpha(alpha_id)
                    is_data, oos_data = alpha_detail.get("is", {}), alpha_detail.get("oos", {})
                if progress_callback:
                    progress_callback(100, "模拟完成")
                return {"ok": True, "status": "completed", "expression": expression,
                        "is": is_data, "oos": oos_data, "settings": data.get("settings", {}),
                        "alpha_id": alpha_id, "simulation_id": data.get("id") or url.rsplit("/", 1)[-1],
                        "remote_run_ref": url, "raw_platform_result": data, "raw_alpha_result": alpha_detail}
            if status in ("ERROR", "FAILED"):
                return {"ok": False, "status": "failed", "remote_run_ref": url,
                        "raw_platform_result": data, "error": f"WQ simulation failed: {data.get('message', status)}"}
            if status in ("CANCELLED", "CANCELED"):
                return {"ok": False, "status": "remote_cancel_confirmed", "remote_run_ref": url,
                        "remote_cancel_confirmed": True, "raw_platform_result": data,
                        "error": "Platform reports cancellation"}
            if i + 1 < (_POLL_MAX_ATTEMPTS if max_polls is None else max_polls):
                time.sleep(_POLL_INTERVAL)
        return {"ok": False, "status": "remote_outcome_unknown", "retryable": False,
                "remote_run_ref": url, "error": "WQ simulation polling timeout", "next_action": "resume_polling"}

    def _fetch_alpha(self, alpha_id: str) -> dict:
        try:
            r = self._get_session().get(f"{API_BASE}/alphas/{alpha_id}", timeout=(10, 30))
        except requests.RequestException:
            return {}
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                logger.warning(f"Empty/invalid JSON from /alphas/{alpha_id}")
                return {}
        return {}

    def check_alpha_status(self, alpha_id: str) -> dict:
        """Fetch actual platform-side alpha status including submission state."""
        data = self._fetch_alpha(alpha_id)
        if not data:
            return {"ok": False, "error": f"Alpha {alpha_id} not found"}
        return {
            "ok": True,
            "alpha_id": alpha_id,
            "status": data.get("status"),
            "dateSubmitted": data.get("dateSubmitted"),
            "dateCreated": data.get("dateCreated"),
            "grade": data.get("grade"),
            "color": data.get("color"),
            "hidden": data.get("hidden"),
            "is": data.get("is", {}),
            "checks": data.get("checks", {}),
        }

    def submit_alpha(self, alpha_id: str) -> dict:
        """Submit once. Only a platform ACTIVE result confirms success."""
        try:
            response = self._get_session().post(f"{API_BASE}/alphas/{alpha_id}/submit",
                                                timeout=(10, 60), allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout) as exc:
            return {"status_code": 0, "ok": False, "detail": str(exc),
                    "status": "remote_outcome_unknown", "retryable": False,
                    "remote_run_ref": f"alpha:{alpha_id}", "next_action": "check_alpha_status"}
        if response.status_code not in (200, 201, 202):
            unknown = response.status_code >= 500 or 300 <= response.status_code < 400
            return {"status_code": response.status_code, "ok": False, "detail": response.text[:500],
                    "status": "remote_outcome_unknown" if unknown else "failed",
                    "retryable": response.status_code == 429, "remote_run_ref": f"alpha:{alpha_id}"}
        return self._poll_alpha_submission(alpha_id)

    def _poll_alpha_submission(self, alpha_id: str, max_polls: int = 12, interval: int = 10) -> dict:
        """Poll alpha status until platform confirms submission or SC check completes."""
        s = self._get_session()
        status, sc_result = "UNKNOWN", "MISSING"
        for i in range(max_polls):
            time.sleep(interval)
            try:
                r = s.get(f"{API_BASE}/alphas/{alpha_id}", timeout=(10, 30))
            except (requests.ConnectionError, requests.Timeout):
                logger.warning(f"Submit poll {alpha_id}: connection error at poll #{i}")
                continue
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue

            status = data.get("status", "").upper()
            is_data = data.get("is", {})
            checks = is_data.get("checks", [])

            sc_check = next((c for c in checks if c.get("name") == "SELF_CORRELATION"), None)
            sc_result = sc_check.get("result", "PENDING") if sc_check else "MISSING"

            logger.info(f"Submit poll {alpha_id} #{i}: status={status}, SC={sc_result}")

            if status == "ACTIVE":
                logger.info(f"Submit {alpha_id}: confirmed ACTIVE on platform")
                return {
                    "status_code": 200,
                    "ok": True,
                    "detail": f"submitted and ACTIVE, SC={sc_result}",
                    "platform_status": status,
                }
            elif sc_result == "FAIL" and sc_check is not None:
                sc_value = sc_check.get("value", "?")
                sc_limit = sc_check.get("limit", "?")
                logger.warning(f"Submit {alpha_id}: SC FAIL (value={sc_value}, limit={sc_limit})")
                return {
                    "status_code": 200,
                    "ok": False,
                    "detail": f"SC FAIL: value={sc_value} > limit={sc_limit}",
                    "platform_status": status,
                    "sc_value": sc_value,
                    "sc_limit": sc_limit,
                }

        return {
            "status_code": 200,
            "ok": False,
            "detail": f"submission polling timeout ({max_polls * interval}s), last status={status}, SC={sc_result}",
            "platform_status": "TIMEOUT",
            "status": "remote_outcome_unknown", "retryable": False,
            "remote_run_ref": f"alpha:{alpha_id}", "next_action": "check_alpha_status",
        }

    def delete_alpha(self, alpha_id: str) -> dict:
        """Delete/retire an alpha from the platform."""
        s = self._get_session()
        r = s.delete(f"{API_BASE}/alphas/{alpha_id}")
        if r.status_code in (200, 204):
            return {"ok": True, "detail": f"Alpha {alpha_id} deleted"}
        if r.status_code == 405:
            r2 = s.patch(f"{API_BASE}/alphas/{alpha_id}", json={"hidden": True})
            if r2.status_code in (200, 204):
                return {"ok": True, "detail": f"Alpha {alpha_id} hidden via PATCH"}
            return {"ok": False, "detail": f"DELETE 405, PATCH also failed: {r2.status_code} {r2.text[:200]}"}
        return {"ok": False, "detail": f"DELETE failed: {r.status_code} {r.text[:200]}"}

    def unhide_alpha(self, alpha_id: str) -> dict:
        """Restore a hidden alpha."""
        s = self._get_session()
        r = s.patch(f"{API_BASE}/alphas/{alpha_id}", json={"hidden": False})
        if r.status_code in (200, 204):
            return {"ok": True, "detail": f"Alpha {alpha_id} restored"}
        return {"ok": False, "detail": f"Unhide failed: {r.status_code} {r.text[:200]}"}

