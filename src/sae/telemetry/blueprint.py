"""The Engine Console layout (rerun.blueprint). Imports rerun lazily; sent once by the stream."""

from __future__ import annotations

from typing import Any


def engine_console() -> Any:
    """Header/status, belief series, strategy text, intent pipeline, decision audit log, health."""
    import rerun.blueprint as rrb

    return rrb.Blueprint(
        rrb.Vertical(
            rrb.TextDocumentView(origin="/status", name="Run"),
            rrb.Horizontal(
                rrb.TimeSeriesView(origin="/belief", name="Belief"),
                rrb.Vertical(
                    rrb.TextLogView(origin="/strategy", name="Strategy"),
                    rrb.TextLogView(origin="/alert", name="Alerts"),
                ),
            ),
            rrb.Horizontal(
                rrb.TimeSeriesView(origin="/intent", name="Intent pipeline"),
                rrb.TextLogView(origin="/audit", name="Decision audit"),
            ),
            rrb.Horizontal(
                rrb.TimeSeriesView(origin="/legal", name="Legal state"),
                rrb.TimeSeriesView(origin="/outcome", name="Outcome"),
                rrb.TextLogView(origin="/control", name="Control"),
                rrb.TimeSeriesView(origin="/god", name="God (debug only)"),
            ),
            rrb.Horizontal(
                rrb.TimeSeriesView(origin="/governor/spm", name="Governor: s/month (spm, med6, med24)"),
                rrb.TimeSeriesView(origin="/governor/parse", name="Governor: last parse cost"),
            ),
            rrb.Horizontal(
                rrb.TimeSeriesView(origin="/health", name="Health"),
                rrb.TextLogView(origin="/health", name="Health events"),
            ),
        ),
        collapse_panels=True,
    )
