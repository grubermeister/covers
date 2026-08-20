###################################################################################################
## WoCo Commons - Model Filters
## MPC: 2025/11/15
###################################################################################################
from datetime import date

import django_filters
from django.db.models import Q
from rest_framework import filters

from .models import Citation, Contribution, CoverMarking, Marking, MarkingType, Region, Shape


def regions_matching_state_term(value):
    """Regions a user means when they type `value` into a "state" field.

    Name matches anything -- "Accomack" is a perfectly good thing to look up.
    An *abbreviation* only ever means a primary jurisdiction: the VPHC ingest
    gave all 141 county rows their state's abbrev (issue #103), so a bare
    `?state=VA` matched Virginia AND every Virginia county, put two junction
    rows behind each VA marking, and returned 2,309 rows for 1,947 markings
    with 362 of them unreachable -- measured on woco.dev, with .distinct()
    already applied and unable to help (the ordered region column was in the
    SELECT list, so the two rows were not duplicates).

    Negation is safe here because these are Region's own columns; the same
    predicate written across the post_office_regions junction would become a
    NOT EXISTS and stop meaning "this one region row".

    Now a thin wrapper over Region.matching_state_term. The predicate moved onto
    the model when the WRITE paths turned out to need it too: they had their own
    unscoped copy, which resolved "VA" to Accomack County and created duplicate
    post offices on approval. One definition, so the read and write paths cannot
    drift apart again.
    """
    return Region.matching_state_term(value)


def filter_markings_by_state_term(queryset, value):
    """Shared body of the `state` filter on both marking FilterSets.

    A blank term is a no-op, not "match nothing" -- django-filter calls the
    method for an empty `?state=` and the list must stay whole.
    """
    if not str(value or "").strip():
        return queryset
    return queryset.filter(
        post_office__post_office_regions__region__in=regions_matching_state_term(value),
    ).distinct()


class AliasedOrderingFilter(filters.OrderingFilter):
    """OrderingFilter that rewrites retired `?ordering=` keys to live ones.

    Needed because a view's `ordering_fields` list is matched verbatim by
    DRF (`get_valid_fields`), so simply leaving a retired key in the list
    hands it straight back to `order_by()`. Issue #103 retired the
    post_office_regions ordering paths in favour of the primary_region_*
    annotations; bookmarked search URLs still carry the old spelling, and
    honouring them literally would reintroduce the fan-out the annotations
    exist to remove.

    Aliases are declared on the view as `ordering_aliases`.

    It also guarantees a unique final sort key. A view's `ordering` default can
    end in `id`, but DRF REPLACES that default wholesale when the request
    carries `?ordering=`, so the tiebreak silently vanishes exactly when a user
    sorts by something non-unique -- and then LIMIT/OFFSET serves one row twice
    and another never (DRF #6886). Caught by the issue #109 page walk:
    `?ordering=town` over rows sharing a town repeated rows between pages.
    Appending it here fixes it for every client rather than trusting each one
    to remember.
    """

    def remove_invalid_fields(self, queryset, fields, view, request):
        aliases = getattr(view, "ordering_aliases", None) or {}
        rewritten = [self._rewrite(term, aliases) for term in fields]
        return super().remove_invalid_fields(queryset, rewritten, view, request)

    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering
        if any(str(term).lstrip("-") == "id" for term in ordering):
            return list(ordering)
        return [*ordering, "id"]

    @staticmethod
    def _rewrite(term, aliases):
        text = str(term or "").strip()
        prefix, key = ("-", text[1:]) if text.startswith("-") else ("", text)
        return prefix + aliases.get(key, key)


def marking_ids_cited_by_reference_work_query(value):
    text = str(value or "").strip()
    if not text:
        return Citation.objects.none().values_list("subject_id", flat=True)
    return Citation.objects.filter(
        subject_type="MARKING",
    ).filter(
        Q(reference_work__code__icontains=text)
        | Q(reference_work__title__icontains=text)
    ).values_list("subject_id", flat=True)


class CitationAwareMarkingSearchFilter(filters.SearchFilter):
    """
    Extend DRF's normal marking search with citation reference-work matches.

    The normal search still uses MarkingViewSet.search_fields. This class adds
    an OR branch for markings cited to any ReferenceWork whose public code or
    title contains the user's top Search text. Citation is polymorphic, so the
    match must go through Citation.subject_type/subject_id rather than a Django
    FK from Citation to Marking.
    """

    def filter_queryset(self, request, queryset, view):
        searched = super().filter_queryset(request, queryset, view)
        terms = self.get_search_terms(request)
        if not terms:
            return searched

        citation_query = Q()
        for term in terms:
            citation_query &= Q(
                pk__in=marking_ids_cited_by_reference_work_query(term),
            )
        if not citation_query:
            return searched
        return (searched | queryset.filter(citation_query)).distinct()


class MarkingListFilter(django_filters.FilterSet):
    """
    List-view filters for Marking.
    onto the unified Marking model. The Phase 2 API rewrite will wire this
    into MarkingViewSet and add the `type` discriminator filter that the
    frontend already passes.
    """

    type = django_filters.ChoiceFilter(
        field_name='type',
        choices=MarkingType.choices,
        label='Marking type',
    )
    is_manuscript = django_filters.CharFilter(method='filter_is_manuscript', label='Is manuscript')
    reviewed = django_filters.CharFilter(method='filter_reviewed', label='Reviewed/confirmed by a state editor')
    color = django_filters.CharFilter(method='filter_by_color', label='Color (name)')
    state = django_filters.CharFilter(method='filter_by_state_name', label='State (name or abbreviation)')
    town = django_filters.CharFilter(
        field_name='post_office__name',
        lookup_expr='icontains',
        label='Town (post office name contains)',
    )
    # Exact office, not a name match. `town` is icontains and would pull in
    # every office whose name contains the search text ("Richmond" also matches
    # "New Richmond"), which is wrong for callers that already know the office
    # and need its markings exhaustively -- the marking detail page's
    # "move image to another marking" picker, where a missing candidate means
    # the editor cannot complete the move (issue #104 / C3).
    post_office = django_filters.NumberFilter(
        field_name='post_office_id',
        lookup_expr='exact',
        label='Post office id (exact)',
    )
    shape = django_filters.NumberFilter(
        field_name='shape',
        lookup_expr='exact',
        label='Shape id',
    )
    has_images = django_filters.CharFilter(method='filter_has_images', label='Has images')
    institutional = django_filters.CharFilter(
        method='filter_institutional',
        label='Has an institutionally owned cover',
    )
    reference_work_code = django_filters.CharFilter(
        method='filter_by_reference_work_code',
        label='Reference work code',
    )
    earliest_use_year_min = django_filters.NumberFilter(
        method='filter_earliest_use_year_min',
        label='Earliest observed year is at least',
    )
    latest_use_year_max = django_filters.NumberFilter(
        method='filter_latest_use_year_max',
        label='Latest observed year is at most',
    )
    # Dimension exact-match filters. Stored values are Decimal(mm) with two
    # decimal places, so Decimal("25") and Decimal("25.00") compare equal here.
    height = django_filters.NumberFilter(
        field_name='height',
        lookup_expr='exact',
        label='Height in mm (exact)',
    )
    width = django_filters.NumberFilter(
        field_name='width',
        lookup_expr='exact',
        label='Width in mm (exact)',
    )

    class Meta:
        model = Marking
        fields = []

    @staticmethod
    def filter_earliest_use_year_min(queryset, name, value):
        if value is None:
            return queryset
        year = int(value)
        return queryset.filter(earliest_seen__gte=date(year, 1, 1))

    @staticmethod
    def filter_latest_use_year_max(queryset, name, value):
        if value is None:
            return queryset
        year = int(value)
        return queryset.filter(latest_seen__lte=date(year, 12, 31))

    @staticmethod
    def filter_is_manuscript(queryset, name, value):
        if not value or not str(value).strip():
            return queryset
        raw = str(value).strip().lower()
        if raw == 'true':
            return queryset.filter(is_manuscript=True)
        if raw == 'false':
            return queryset.exclude(is_manuscript=True)
        return queryset

    @staticmethod
    def filter_reviewed(queryset, name, value):
        if not value or not str(value).strip():
            return queryset
        raw = str(value).strip().lower()
        if raw == 'true':
            return queryset.filter(is_reviewed=True)
        if raw == 'false':
            return queryset.exclude(is_reviewed=True)
        return queryset

    @staticmethod
    def filter_by_color(queryset, name, value):
        if not value or not str(value).strip():
            return queryset
        return queryset.filter(color__name__iexact=str(value).strip())

    @staticmethod
    def filter_by_state_name(queryset, name, value):
        return filter_markings_by_state_term(queryset, value)

    @staticmethod
    def filter_has_images(queryset, name, value):
        if not value or str(value).strip().lower() != 'true':
            return queryset
        from .models import Image
        marking_ids_with_images = Image.objects.filter(
            subject_type=Image.SUBJECT_MARKING,
        ).values_list('subject_id', flat=True)
        return queryset.filter(pk__in=marking_ids_with_images)

    @staticmethod
    def filter_institutional(queryset, name, value):
        # "Institutional" lives on Cover (is_institutional); a marking counts as
        # institutional when at least one of its covers is institutionally
        # owned. Route through Cover.objects (the default manager) so
        # recycle-binned covers are excluded -- an FK traversal like
        # cover__is_institutional=True would use base_manager_name='all_objects'
        # and wrongly include removed covers. (issue #29)
        if not value or str(value).strip().lower() != 'true':
            return queryset
        from .models import Cover, CoverMarking
        institutional_marking_ids = CoverMarking.objects.filter(
            cover__in=Cover.objects.filter(is_institutional=True),
        ).values_list('marking_id', flat=True)
        return queryset.filter(pk__in=institutional_marking_ids)

    @staticmethod
    def filter_by_reference_work_code(queryset, name, value):
        code = str(value or "").strip()
        if not code:
            return queryset
        cited_marking_ids = Citation.objects.filter(
            subject_type="MARKING",
            reference_work__code__iexact=code,
        ).values_list("subject_id", flat=True)
        return queryset.filter(pk__in=cited_marking_ids)


class MarkingFilter(django_filters.FilterSet):
    """Advanced filters for Marking objects."""

    q = django_filters.CharFilter(method="filter_q", label="Search (code, catalog/inscription text)")
    type = django_filters.ChoiceFilter(field_name='type', choices=MarkingType.choices, label='Marking type')
    state = django_filters.CharFilter(method="filter_by_state", label="Region (name or abbreviation)")
    color = django_filters.CharFilter(field_name="color__name", lookup_expr="iexact", label="Color")
    has_images = django_filters.BooleanFilter(method="filter_has_images", label="Has Images")

    class Meta:
        model = Marking
        fields = ["type", "is_manuscript", "shape", "lettering", "color", "date_fmt"]

    def filter_q(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(code__icontains=value)
            | Q(catalog_txt__icontains=value)
            | Q(inscription_txt__icontains=value)
            | Q(desc__icontains=value)
        )

    def filter_by_state(self, queryset, name, value):
        return filter_markings_by_state_term(queryset, value)

    def filter_has_images(self, queryset, name, value):
        if value is None:
            return queryset
        from .models import Image
        marking_ids_with_images = Image.objects.filter(
            subject_type=Image.SUBJECT_MARKING,
        ).values_list('subject_id', flat=True)
        if value:
            return queryset.filter(pk__in=marking_ids_with_images)
        return queryset.exclude(pk__in=marking_ids_with_images)


class CoverMarkingFilter(django_filters.FilterSet):
    """Filters for CoverMarking list/detail.

    `marking` and `cover` are plain id NumberFilters, NOT the ModelChoiceFilters
    that `fields = ["marking", "cover", ...]` would auto-build. Those auto-filters
    scope their choices to the default Marking.objects / Cover.objects managers,
    which hide recycle-binned (removed) rows; filtering by a removed marking's or
    removed cover's id then fails validation with HTTP 400. That breaks the
    Associated Covers panel on a removed marking's record-detail page (marking),
    and the Associated Markings panel on a removed cover's detail page (cover).
    Region/permission scoping for these params lives in
    CoverMarkingViewSet.get_queryset.
    """

    marking = django_filters.NumberFilter(field_name="marking_id")
    cover = django_filters.NumberFilter(field_name="cover_id")

    class Meta:
        model = CoverMarking
        fields = ["cover", "marking", "is_backstamp", "placement", "review_status"]


class ContributionListFilter(django_filters.FilterSet):
    """List filters for the editor review queue (issue #109).

    These exist because the dashboard used to filter the page it had already
    fetched. Pagination is server side, so at 2,440 queued contributions the
    search box was looking at one page in twenty-five: searching "farm" on
    woco.dev returned nothing on page 1 and Farmville turned up on page 16. A
    lookup that finds nothing does not read as a broken filter, it reads as
    "the record isn't there", which is why this ranked above the cosmetic queue
    issues before the 2026-08-29 VPHS review session.

    Town, shape and colour live only inside `submitted_data`, so every one of
    these is a JSON key transform. That is already the shipped pattern for
    `state` (ContributionViewSet.get_queryset). No index is possible -- MariaDB
    10.11 has no functional indexes -- so each of these is a full scan of the
    Contributions table. Measured on the dev copy at 2,062 rows: 13 ms to
    count, 21 ms to serve a 100-row page. Revisit around 100k rows, where the
    answer is denormalised columns rather than a cleverer query.

    `state` is deliberately NOT here: it also matches across the collection's
    Region relation and only applies to the editor/archived lenses. It stays in
    get_queryset. DjangoFilterBackend runs after get_queryset, so the two
    compose.
    """

    q = django_filters.CharFilter(method="filter_q", label="Search")
    town = django_filters.CharFilter(method="filter_by_town", label="Town (contains)")
    shape = django_filters.CharFilter(method="filter_by_shape", label="Shape (name)")
    color = django_filters.CharFilter(method="filter_by_color", label="Color (name)")
    submitted_from = django_filters.DateFilter(
        field_name="created_date",
        lookup_expr="date__gte",
        label="Submitted on or after",
    )
    submitted_to = django_filters.DateFilter(
        field_name="created_date",
        lookup_expr="date__lte",
        label="Submitted on or before",
    )
    source = django_filters.ChoiceFilter(
        method="filter_by_source",
        label="Submission source",
        choices=(
            ("vphc", "VPHC ingest"),
            ("human", "Submitted by a person"),
            ("all", "All"),
        ),
    )

    class Meta:
        model = Contribution
        # Preserves the viewset's previous `filterset_fields = ["status"]`
        # exactly -- an auto-built ChoiceFilter validated against STATUS_CHOICES.
        fields = ["status"]

    @staticmethod
    def filter_q(queryset, name, value):
        """Free text across the fields an editor would actually type.

        The VPHC code and a bare entry number are in here because they are what
        gets said out loud in a review session -- "pull up the Lynchburg one",
        "look at 8640". Neither worked before: the old client-side haystack was
        display name, town, state, shape and contributor username, one page deep.

        Django escapes % and _ in icontains patterns, so wildcards in user text
        are literal. On MySQL icontains compiles to LOWER(x) LIKE LOWER(%s), so
        the match is case-insensitive regardless of column collation. An absent
        JSON key yields SQL NULL, so LIKE yields NULL and the row simply does
        not match -- no error, no special case.
        """
        text = str(value or "").strip()
        if not text:
            return queryset
        predicate = (
            Q(submitted_data__town__icontains=text)
            | Q(submitted_data__state__icontains=text)
            | Q(submitted_data__type__icontains=text)
            | Q(submitted_data__inscription_txt__icontains=text)
            | Q(submitted_data__vphc__vphc_code__icontains=text)
            | Q(contributor__username__icontains=text)
        )
        # An all-digit query is also an entry number. Bounded so a 40-digit
        # string cannot reach the INT column comparison and raise.
        if text.isdigit() and len(text) <= 9:
            predicate |= Q(pk=int(text))
        return queryset.filter(predicate)

    @staticmethod
    def filter_by_source(queryset, name, value):
        """Ingest rows vs rows a person submitted (issue #101).

        Bulk approve defaults to `vphc`, and this is what makes that default
        mean something. The queue is LIVE: on 2026-08-16 it held 312 pending
        edits where every plan assumed 310, and the two extra were real human
        submissions that a "delete all pending" would have destroyed. A later
        census found eight pending human rows, not two. The standing rule that
        came out of that (LEFT_OFF section B1) is to select on the `vphc` key,
        never on status -- so that is what this does, rather than inferring a
        source from the contributor or the date.

        `has_key` compiles to JSON_CONTAINS_PATH on MySQL, so an ingest row is
        identified by the key EXISTING, not by its contents. A row whose blob
        is empty or malformed is still an ingest row.
        """
        choice = str(value or "").strip().lower()
        if choice == "vphc":
            return queryset.filter(submitted_data__has_key="vphc")
        if choice == "human":
            return queryset.exclude(submitted_data__has_key="vphc")
        return queryset

    @staticmethod
    def filter_by_town(queryset, name, value):
        # Contains, not exact: the queue's town box is a search field, and
        # contribution towns are free text that has not been resolved to a
        # PostOffice yet (that happens at approval).
        text = str(value or "").strip()
        if not text:
            return queryset
        return queryset.filter(submitted_data__town__icontains=text)

    @staticmethod
    def filter_by_shape(queryset, name, value):
        """Shape by NAME, because that is what submitted_data holds.

        Censused over the queued rows: `submitted_data.shape` is the full
        Shape.name verbatim ("C - Circle"), never an id. A bare integer is
        accepted anyway and resolved through Shape.name, so a dashboard URL
        bookmarked while the filter emitted ids (?e_shape=68) still works.

        Note ~60% of queued rows carry no `shape` key at all -- the VPHC applier
        writes it conditionally -- and those correctly drop out of any shape
        filter rather than matching a blank.
        """
        text = str(value or "").strip()
        if not text:
            return queryset
        if text.isdigit():
            text = Shape.objects.filter(pk=int(text)).values_list(
                "name", flat=True,
            ).first() or text
        return queryset.filter(submitted_data__shape__iexact=text)

    @staticmethod
    def filter_by_color(queryset, name, value):
        # By name, mirroring MarkingListFilter.filter_by_color -- the submission
        # form and the VPHC applier both store the colour name, and color_id is
        # only present when the typed name happened to match a Color row.
        text = str(value or "").strip()
        if not text:
            return queryset
        return queryset.filter(submitted_data__color__iexact=text)


###################################################################################################
