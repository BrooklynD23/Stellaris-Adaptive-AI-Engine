"""Active policy (C10 section 4): the behaviour constants the engine runs with, derived from a ``PolicyVersion``.

Pure apart from reading the packaged built-in file. The built-in policy 0.1.0 is generated from today's code
constants (``LikelihoodModel()``, the 0.02/3.0/0.45 detector literals, ``COUNTER_TABLE``, ``DEFAULT_EXPANSIONS``,
``outcome.WEIGHTS``); a test asserts the packaged JSON equals them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from typing import Final

from sae.contracts.c5_bus import PolicyRef
from sae.contracts.c10_profile import (
    DetectorParams,
    FixedParams,
    LikelihoodParams,
    PolicyVersion,
    TemplateSpec,
    TuningManifest,
    policy_sha256,
)
from sae.contracts.common import COUNTER_TABLE, Strategy
from sae.core.belief import LikelihoodModel
from sae.core.expansion import DEFAULT_EXPANSIONS, Template
from sae.core.outcome import WEIGHTS

__all__ = ["BUILTIN_FILE", "ActivePolicy", "builtin_policy_doc", "from_doc", "load_builtin", "template_of"]

log = logging.getLogger(__name__)
BUILTIN_FILE: Final = "builtin-0.1.0.json"

@dataclass(frozen=True)
class ActivePolicy:
    """A resolved policy: what the engine uses plus what the bus records (``ref``)."""

    ref: PolicyRef
    doc: PolicyVersion
    likelihood: LikelihoodModel
    templates: Mapping[Strategy, Template]
    weights: Mapping[str, float]

    @property
    def sha256(self) -> str:
        """The policy identity (C10 section 4)."""
        return self.ref.sha256

    @property
    def game_version_pin(self) -> str:
        """The ``vX.Y.*`` pin checked against the first observed game version."""
        return self.doc.game_version_pin

def template_of(spec: TemplateSpec) -> Template:
    """``TemplateSpec`` (C10) to the engine's ``Template``."""
    return Template(spec.econ, spec.posture, spec.reason_key)

def builtin_policy_doc() -> PolicyVersion:
    """Built-in policy 0.1.0 as derived from today's code constants (the source of the packaged JSON)."""
    m = LikelihoodModel()
    likelihood = LikelihoodParams(border=dict(m.border), expansion=dict(m.expansion), fleet=dict(m.fleet),
                                  tech=dict(m.tech), war_now=dict(m.war_now))
    return PolicyVersion(
        version="0.1.0", created_at=datetime(2026, 10, 19, tzinfo=UTC), game_version_pin="v4.4.*",
        parent_sha256=None,
        detector=DetectorParams(rho=0.02, likelihood_clip=3.0, belief_threshold=0.45,
                                likelihood=likelihood),
        fixed=FixedParams(
            counter_table=dict(COUNTER_TABLE),
            templates={s: TemplateSpec(econ=t.econ, posture=t.posture, reason_key=t.reason_key)
                       for s, t in DEFAULT_EXPANSIONS.items()},
            outcome_weights=dict(WEIGHTS)),
        tuning=TuningManifest(method="builtin", likelihood_source="synth_aligned", tuner_version="none"),
    )

def from_doc(doc: PolicyVersion, ref: PolicyRef) -> ActivePolicy:
    """Build the engine-facing tables from a validated ``PolicyVersion``."""
    lk = doc.detector.likelihood
    model = LikelihoodModel(
        border={a: (g[0], g[1]) for a, g in lk.border.items()},
        expansion={a: (g[0], g[1]) for a, g in lk.expansion.items()},
        fleet={a: tuple(c) for a, c in lk.fleet.items()},
        tech={a: tuple(c) for a, c in lk.tech.items()},
        war_now=dict(lk.war_now))
    return ActivePolicy(
        ref=ref, doc=doc, likelihood=model,
        templates={s: template_of(t) for s, t in doc.fixed.templates.items()},
        weights={k: float(v) for k, v in doc.fixed.outcome_weights.items()})

def load_builtin(fallback_reason: str | None = None) -> ActivePolicy:
    """The built-in policy: the packaged file, or (if packaging dropped it) the code constants, with a warning."""
    try:
        text = (resources.files("sae") / "policies" / BUILTIN_FILE).read_text(encoding="utf-8")
        doc = PolicyVersion.model_validate_json(text)
    except (OSError, ValueError) as exc:  # ValidationError is a ValueError
        log.warning("packaged built-in policy unusable (%s); using code constants", type(exc).__name__)
        doc = builtin_policy_doc()
    ref = PolicyRef(version=doc.version, sha256=policy_sha256(doc), source="builtin", fallback_reason=fallback_reason)
    return from_doc(doc, ref)

