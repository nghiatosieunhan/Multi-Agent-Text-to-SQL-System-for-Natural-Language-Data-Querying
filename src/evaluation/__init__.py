"""Evaluation helpers for reproducible Text-to-SQL experiments."""

from src.evaluation.profiles import get_profile_options
from src.evaluation.telemetry import telemetry_run

__all__ = ["get_profile_options", "telemetry_run"]
