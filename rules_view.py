"""What the engine actually runs, for wallets (bounty 02, #42).

``application_rules`` is append-only text, but pointwise revision is not: a
later unit displaces whatever part of an earlier one it contradicts. The running
spec is read from the engine itself -- a throwaway interpreter rebuilt from the
committed rule history the way the node rebuilds its own, in a child process --
so a displaced fragment is absent. It is a pure function of the rule history,
cached by the history's hash: every node at the same tip, and a replay from
block 0, return the same text and hash.
"""
import hashlib
import logging
import threading
from collections import OrderedDict

logger = logging.getLogger(__name__)

_CACHE_SIZE = 8
_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def history_sha256(rules_text: str) -> str:
    return hashlib.sha256((rules_text or "").encode("utf-8")).hexdigest()


def running_spec(rules_text: str, *, timeout: float = 30.0) -> dict:
    """{"status": "ok", text, sha256, spec_revision, history_sha256} or
    {"status": "unavailable", reason, history_sha256}. Failures are not cached."""
    key = history_sha256(rules_text)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return dict(hit)
    try:
        import tau_native
        spec = tau_native.running_spec_subprocess(rules_text, timeout)
    except Exception as exc:  # native absent, timeout, unreplayable history
        logger.info("running spec unavailable: %s", exc)
        return {"status": "unavailable", "reason": str(exc)[:300], "history_sha256": key}
    out = {
        "status": "ok", "text": spec["text"], "sha256": spec["sha256"],
        "spec_revision": spec.get("spec_revision"), "history_sha256": key,
    }
    with _lock:
        _cache[key] = out
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return dict(out)


def clear_cache():
    with _lock:
        _cache.clear()
