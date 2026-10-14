"""Job registry. Add a job: write a module with a Job subclass, import it here, add to REGISTRY."""
from __future__ import annotations

from typing import Dict, Type

from .base import Job, JobException, Plan, BlockDiff
from .page_numbering import PageNumberingJob
from .tracked_replace import TrackedReplaceJob

REGISTRY: Dict[str, Type[Job]] = {
    PageNumberingJob.name: PageNumberingJob,
    TrackedReplaceJob.name: TrackedReplaceJob,
}


def get_job(name: str) -> Job:
    if name not in REGISTRY:
        raise KeyError(f"unknown job {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name]()


__all__ = ["Job", "JobException", "Plan", "BlockDiff", "REGISTRY", "get_job"]
