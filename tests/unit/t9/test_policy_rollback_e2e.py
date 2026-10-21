"""WU-7 exit: promote A, promote B, ``rollback`` -> the daemon's resolver picks A; ``pin builtin`` -> built-in.

No synth run, no parser, no daemon loop: the same ``resolve_policy(cfg)`` call that fills ``run_started.policy``
(``daemon/wiring.py``) reads the registry the CLI wrote.
"""

from __future__ import annotations

from collections.abc import Callable

from sae.core.policy import load_builtin
from sae.daemon.policy_resolve import resolve_policy

from .conftest import PolicyEnv


def test_promote_promote_rollback_pin(penv: PolicyEnv, tty: None, answer: Callable[[str], None]) -> None:
    builtin = load_builtin().sha256
    a, b = penv.policy(0.03), penv.policy(0.04)
    penv.chain(a)
    penv.chain(b)
    assert resolve_policy(penv.config()).ref.sha256 == builtin  # nothing promoted yet

    answer(a[:8])
    assert penv.run("promote", a)[0] == 0
    assert resolve_policy(penv.config()).ref.sha256 == a
    answer(b[:8])
    assert penv.run("promote", b)[0] == 0
    ref = resolve_policy(penv.config()).ref
    assert (ref.sha256, ref.source) == (b, "registry")

    answer(a[:8])
    assert penv.run("rollback")[0] == 0
    ref = resolve_policy(penv.config()).ref
    assert (ref.sha256, ref.source, ref.fallback_reason) == (a, "registry", None)

    answer(builtin[:8])
    code, out, _ = penv.run("pin", "builtin")
    assert code == 0 and 'policy.pin = "builtin"' in out
    ref = resolve_policy(penv.config()).ref
    assert ref.sha256 == builtin and ref.fallback_reason is None
