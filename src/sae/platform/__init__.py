"""sae.platform (owner: T5): sensors and OS controls."""

from .sensors import make_sensors
from .spawn import make_platform

__all__ = ["make_platform", "make_sensors"]
