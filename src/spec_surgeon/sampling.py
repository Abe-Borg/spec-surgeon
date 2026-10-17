"""Representative evidence selection; full-batch verification remains separate."""
from __future__ import annotations

from typing import Callable, Hashable, Iterable, List, Optional, TypeVar

from .resources import DEFAULT_SAMPLE_FILES, positive_integer

T = TypeVar("T")


def representative_samples(items: Iterable[T], *, pattern_key: Callable[[T], Hashable],
                           count: Optional[int] = None) -> List[T]:
    """Cover structural patterns first, then fill from their remaining members.

    Automatic selection targets the shared allowance and expands to include every
    pattern. An explicit positive count is honored, even when smaller than the
    number of patterns. Largest groups go first; ties and members retain input
    order. This chooses investigation/review evidence, never delivery eligibility.
    """
    if count is not None:
        positive_integer("sample_files", count)
    grouped = {}
    for item in items:
        grouped.setdefault(pattern_key(item), []).append(item)
    groups = sorted(grouped.values(), key=lambda group: -len(group))
    target = max(DEFAULT_SAMPLE_FILES, len(groups)) if count is None else count
    selected = []
    index = 0
    while len(selected) < target:
        more = [group[index] for group in groups if index < len(group)]
        if not more:
            break
        selected.extend(more[:target - len(selected)])
        index += 1
    return selected
