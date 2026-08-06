###################################################################################################
## WoCo Commons - Model Filters
## MPC: 2025/11/15
###################################################################################################
from datetime import date

import django_filters
from django.db.models import Q
from rest_framework import filters

from .models import Citation, CoverMarking, Marking, MarkingType


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
        if not value or not str(value).strip():
            return queryset
        value = str(value).strip()
        return queryset.filter(
            Q(post_office__post_office_regions__region__name__iexact=value)
            | Q(post_office__post_office_regions__region__abbrev__iexact=value)
        ).distinct()

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
        if not value:
            return queryset
        value = str(value).strip()
        return queryset.filter(
            Q(post_office__post_office_regions__region__name__iexact=value)
            | Q(post_office__post_office_regions__region__abbrev__iexact=value)
        ).distinct()

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


###################################################################################################
