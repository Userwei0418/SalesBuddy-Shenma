"""Dedicated work-report generation; business schemas are owned by the backend."""

from .providers import GenerationProviders
from .service import generate_work_report

__all__ = ["GenerationProviders", "generate_work_report"]
