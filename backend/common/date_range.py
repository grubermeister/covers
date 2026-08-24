"""
Single implementation of the marking date-range computation (issue #59 fix).

A marking's displayed use range is the min/max over two evidence sources:
  1. DateSeen rows attached directly to the marking (subject_type='MARKING')
  2. DateSeen rows attached to covers that bear the marking via CoverMarking
     (subject_type='COVER'); CoverMarking.review_status is deliberately NOT
     consulted, matching the retired ``with_date_range()`` annotation.

Tie policy: when both sources share a boundary date, the direct MARKING row
supplies the granularity, and within a source the lowest pk wins. That rule
(inherited from the retired annotation) now applies when ``_fold`` collapses
rows that share a stored date, rather than as a component of the comparison.

Boundary semantics live in ``_resolve`` and nowhere else -- read its docstring
before changing what a marking displays as its range (issue #121).

These helpers deliberately take model classes as keyword arguments so the same
code serves the live models, the data migration's historical models (which
lack custom managers/methods), and the recompute management command.

Column writes go through ``QuerySet.update()`` / ``bulk_update()`` only: no
``save()``, so no signals fire (no recursion) and django-reversion records no
Version churn for these derived-value refreshes.
"""
import calendar
import threading
from contextlib import contextmanager
from datetime import date as date_cls

_state = threading.local()

# Only DAY/MONTH/YEAR rows carry a stored ``date`` and so reach this module
# (see DateSeen.generated_date_for_parts). Lower rank = more precise.
GRANULARITY_RANK = {'DAY': 0, 'MONTH': 1, 'YEAR': 2}
_COARSEST_RANK = 3


def _rank(granularity):
    """Precision of a granularity; unknown values sort as coarsest."""
    return GRANULARITY_RANK.get(granularity, _COARSEST_RANK)


def _interval(d, granularity):
    """The (first_day, last_day) span a stored (date, granularity) pair covers.

    ``DateSeen.generated_date_for_parts`` stores the FLOOR of the span -- YEAR
    -> Jan 1, MONTH -> the 1st, DAY -> the day itself -- so ``d`` is always the
    span's start and only the end varies. That floor is why a bare year used to
    beat a real date in the same year (issue #121).
    """
    if granularity == 'YEAR':
        return date_cls(d.year, 1, 1), date_cls(d.year, 12, 31)
    if granularity == 'MONTH':
        last = calendar.monthrange(d.year, d.month)[1]
        return date_cls(d.year, d.month, 1), date_cls(d.year, d.month, last)
    return d, d


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
    """Collect one DateSeen row as a boundary candidate for one marking.

    bounds[key] = {stored_date: (source, pk, granularity)} -- at most one
    candidate per distinct stored date.

    Which row survives a same-date collision IS the inherited tie policy,
    unchanged: source 0 (direct MARKING row) beats source 1 (cover-derived),
    then the lowest pk. The retired keys agreed on that winner at both ends --
    earliest minimised (date, source, pk) and latest maximised
    (date, -source, -pk), and both select source 0 / lowest pk -- so one
    shared per-date representative is behaviour-preserving.
    """
    per_date = bounds.setdefault(key, {})
    current = per_date.get(date)
    if current is None or (source, pk) < (current[0], current[1]):
        per_date[date] = (source, pk, granularity)


def _boundary_key(item, *, latest):
    """Sort key for one candidate ``(date, granularity, source, pk)``."""
    d, gran, source, pk = item
    start, end = _interval(d, gran)
    if latest:
        # maximised: latest span END, then most precise, then latest date.
        return (end, -_rank(gran), d, -source, -pk)
    # minimised: earliest span START (== the stored date), then most precise.
    return (start, _rank(gran), d, source, pk)


def _resolve(candidates, *, latest):
    """Pick a marking's boundary, then descend into the most precise evidence
    lying inside it.

    ISSUE #121 POLICY POINT -- the only place boundary semantics are decided.
    No caller, signal, migration or command knows anything about ranking.

    Step 1 -- chronology first (OPEN QUESTION 1, cross-year). The boundary is
    the candidate whose covered span starts earliest / ends latest. A coarse
    row that is genuinely earlier still wins: YEAR 1855 beats DAY 1856-03-12 on
    the earliest end. To make precision beat chronology instead, drop the
    leading start/end component of ``_boundary_key``; nothing else moves.

    Step 2 -- the #121 fix. A YEAR row is a span, not a day. If another
    candidate's whole span sits inside the winner's and carries a DIFFERENT
    stored date, it is more precise evidence about the same boundary and
    replaces it: 1856 (YEAR, stored 1856-01-01) + 1856-03-12 (DAY) reports
    1856-03-12 / DAY. Repeat until nothing narrows further.

    "Different stored date" is load-bearing: same-date candidates were already
    settled by the source rule in ``_fold`` (the direct MARKING row supplies
    the granularity) and must not be re-litigated here.

    OPEN QUESTION 2 (latest end: DAY 1860-03-05 vs MONTH 1860-11). Ordering by
    span END keeps 1860-11 as the latest -- the month-only row is genuinely
    later evidence, and March never enters November's span, so step 2 cannot
    drag the boundary backwards. If the finest row should instead win anywhere
    within the year, widen the containment test below to same-year.

    Because the stored date is always the span's start, step 1 reproduces the
    retired ``min``/``max`` by date exactly and step 2 can only move a boundary
    FORWARD inside the winning span. No boundary's year can change, so the year
    filters and the range endpoint are unaffected.
    """
    items = [(d, gran, source, pk) for d, (source, pk, gran) in candidates.items()]
    pick = max if latest else min
    winner = pick(items, key=lambda i: _boundary_key(i, latest=latest))
    while True:
        w_start, w_end = _interval(winner[0], winner[1])
        inside = []
        for item in items:
            if item[0] == winner[0]:
                continue
            i_start, i_end = _interval(item[0], item[1])
            if w_start <= i_start and i_end <= w_end:
                inside.append(item)
        if not inside:
            return winner[0], winner[1]
        # Each step moves to a strictly narrower span, so this terminates.
        winner = pick(inside, key=lambda i: _boundary_key(i, latest=latest))


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
        subject_type="MARKING",
        subject_id__in=marking_ids,
        date__isnull=False,
    ).values_list("subject_id", "date", "granularity", "pk"):
        _fold(bounds, sid, date, gran, pk, source=0)

    cover_to_markings = {}
    for marking_id, cover_id in CoverMarking.objects.filter(
        marking_id__in=marking_ids
    ).values_list("marking_id", "cover_id"):
        cover_to_markings.setdefault(cover_id, []).append(marking_id)
    if cover_to_markings:
        for cid, date, gran, pk in DateSeen.objects.filter(
            subject_type="COVER",
            subject_id__in=list(cover_to_markings),
            date__isnull=False,
        ).values_list("subject_id", "date", "granularity", "pk"):
            for marking_id in cover_to_markings[cid]:
                _fold(bounds, marking_id, date, gran, pk, source=1)

    result = {}
    for marking_id in marking_ids:
        candidates = bounds.get(marking_id)
        if not candidates:
            result[marking_id] = (None, None, None, None)
        else:
            e_date, e_gran = _resolve(candidates, latest=False)
            l_date, l_gran = _resolve(candidates, latest=True)
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
