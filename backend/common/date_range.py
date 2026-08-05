"""
Single implementation of the marking date-range computation (issue #59 fix).

A marking's displayed use range is the min/max over two evidence sources:
  1. DateSeen rows attached directly to the marking (subject_type='MARKING')
  2. DateSeen rows attached to covers that bear the marking via CoverMarking
     (subject_type='COVER'); CoverMarking.review_status is deliberately NOT
     consulted, matching the retired ``with_date_range()`` annotation.

Tie policy (must match the retired annotation exactly): when both sources
share a boundary date, the direct MARKING row supplies the granularity. Within
a source, the boundary row is chosen by (date, pk) ascending for earliest and
(date, pk) descending for latest.

These helpers deliberately take model classes as keyword arguments so the same
code serves the live models, the data migration's historical models (which
lack custom managers/methods), and the recompute management command.

Column writes go through ``QuerySet.update()`` / ``bulk_update()`` only: no
``save()``, so no signals fire (no recursion) and django-reversion records no
Version churn for these derived-value refreshes.
"""
import threading
from contextlib import contextmanager

_state = threading.local()


def recompute_suppressed():
    return getattr(_state, "suppressed", False)


@contextmanager
def suppress_date_range_recompute():
    """Silence the DateSeen/CoverMarking signal receivers for bulk operations.

    Callers own the follow-up: run a set-based recompute over the affected
    markings (or --all) before relying on the columns again.
    """
    prior = recompute_suppressed()
    _state.suppressed = True
    try:
        yield
    finally:
        _state.suppressed = prior


def _default_models():
    from common import models
    return models.Marking, models.DateSeen, models.CoverMarking


def markings_affected_by_date_seen(subject_type, subject_id, *, CoverMarking=None):
    """Marking ids whose range depends on the given DateSeen subject."""
    if subject_id is None:
        return set()
    if subject_type == "MARKING":
        return {subject_id}
    if subject_type == "COVER":
        if CoverMarking is None:
            _, _, CoverMarking = _default_models()
        return set(
            CoverMarking.objects.filter(cover_id=subject_id)
            .values_list("marking_id", flat=True)
        )
    return set()


def _fold(bounds, key, date, granularity, pk, source):
    """Track earliest/latest candidates for one marking.

    bounds[key] = [ (date, pk, gran, source) earliest, (date, -pk...) latest ]
    Source 0 = direct MARKING row, 1 = cover-derived; on an equal boundary
    date the direct row must win, so source is the second sort component for
    date-ties and pk only breaks ties within a source.
    """
    entry = bounds.setdefault(key, [None, None])
    earliest_key = (date, source, pk)
    latest_key = (date, -source, -pk)
    if entry[0] is None or earliest_key < entry[0][0]:
        entry[0] = (earliest_key, date, granularity)
    if entry[1] is None or latest_key > entry[1][0]:
        entry[1] = (latest_key, date, granularity)


def compute_marking_date_ranges(marking_ids, *, DateSeen=None, CoverMarking=None):
    """Return {marking_id: (earliest, e_gran, latest, l_gran)} for every id.

    Ids with no evidence map to (None, None, None, None) so callers can clear
    stale columns after the last date is deleted.
    """
    if DateSeen is None or CoverMarking is None:
        _, DateSeen, CoverMarking = _default_models()
    marking_ids = list(marking_ids)
    bounds = {}

    for sid, date, gran, pk in DateSeen.objects.filter(
        subject_type="MARKING", subject_id__in=marking_ids
    ).values_list("subject_id", "date", "granularity", "pk"):
        _fold(bounds, sid, date, gran, pk, source=0)

    cover_to_markings = {}
    for marking_id, cover_id in CoverMarking.objects.filter(
        marking_id__in=marking_ids
    ).values_list("marking_id", "cover_id"):
        cover_to_markings.setdefault(cover_id, []).append(marking_id)
    if cover_to_markings:
        for cid, date, gran, pk in DateSeen.objects.filter(
            subject_type="COVER", subject_id__in=list(cover_to_markings)
        ).values_list("subject_id", "date", "granularity", "pk"):
            for marking_id in cover_to_markings[cid]:
                _fold(bounds, marking_id, date, gran, pk, source=1)

    result = {}
    for marking_id in marking_ids:
        entry = bounds.get(marking_id)
        if entry is None:
            result[marking_id] = (None, None, None, None)
        else:
            (_, e_date, e_gran), (_, l_date, l_gran) = entry[0], entry[1]
            result[marking_id] = (e_date, e_gran, l_date, l_gran)
    return result


DATE_RANGE_FIELDS = [
    "earliest_seen",
    "earliest_seen_granularity",
    "latest_seen",
    "latest_seen_granularity",
]


def refresh_marking_date_ranges(
    marking_ids, *, Marking=None, DateSeen=None, CoverMarking=None, batch_size=1000
):
    """Recompute and store the four columns for the given markings.

    Uses the unfiltered manager so recycle-binned markings stay fresh; ids of
    deleted markings are naturally skipped by bulk_update (0-row UPDATEs).
    Returns the number of markings written.
    """
    if Marking is None:
        Marking, DateSeen, CoverMarking = _default_models()
    marking_ids = [m for m in set(marking_ids) if m is not None]
    if not marking_ids:
        return 0
    manager = getattr(Marking, "all_objects", Marking._default_manager)
    computed = compute_marking_date_ranges(
        marking_ids, DateSeen=DateSeen, CoverMarking=CoverMarking
    )
    existing_ids = set(
        manager.filter(pk__in=marking_ids).values_list("pk", flat=True)
    )
    updates = [
        Marking(
            pk=marking_id,
            earliest_seen=earliest,
            earliest_seen_granularity=e_gran,
            latest_seen=latest,
            latest_seen_granularity=l_gran,
        )
        for marking_id, (earliest, e_gran, latest, l_gran) in computed.items()
        if marking_id in existing_ids
    ]
    if updates:
        manager.bulk_update(updates, DATE_RANGE_FIELDS, batch_size=batch_size)
    return len(updates)
