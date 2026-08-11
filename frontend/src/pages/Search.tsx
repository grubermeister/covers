import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Pagination, PaginationContent, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious, PaginationEllipsis } from "@/components/ui/pagination";
import { Grid3x3, List, Search as SearchIcon, SlidersHorizontal, Loader2, Plus, ArrowUp, ArrowDown } from "lucide-react";
import { useState, useEffect, useRef, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import imageNotAvailable from "@/assets/image-not-available.jpg";
import {
  getMarkingsPage,
  type MarkingRecord,
  type MarkingTypeValue,
} from "@/services/markings";
import { getReferenceWorkOptions } from "@/services/referenceWorks";
import { buildCatalogSearchRow } from "@/lib/catalogRecordDisplay";
import { CatalogRecordFields } from "@/components/CatalogRecordFields";
import { useToast } from "@/hooks/use-toast";
import { useFilterOptions } from "@/hooks/useFilterOptions";
import { useDebounce } from "@/hooks/useDebounce";
import { useMarkingYearRange } from "@/hooks/useMarkingYearRange";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isTrueCircleShapeName } from "@/lib/shapeDisplay";

const DEBOUNCE_MS = 800;

type SubmissionQueueSortOption = "newest" | "oldest";

/**
 * Catalog sort is an ordered list of (field, direction) entries. Insertion
 * order = priority: the first entry is the primary sort, the second is the
 * tiebreaker, and so on. Clicking an arrow on a filter label appends/modifies
 * the entry for that field; clicking the same-direction arrow again removes
 * the entry (toggle off).
 */
type SortField = "state" | "town" | "type" | "shape" | "color" | "earliest" | "latest";
type SortDir = "asc" | "desc";
type SortEntry = { field: SortField; dir: SortDir };

const SORT_FIELD_COLUMN: Record<SortField, string> = {
  state: "post_office__post_office_regions__region__name",
  town: "post_office__name",
  type: "type",
  shape: "shape__name",
  color: "color__name",
  earliest: "earliest_seen",
  latest: "latest_seen",
};

const DEFAULT_SORT: SortEntry[] = [
  { field: "state", dir: "asc" },
  { field: "town", dir: "asc" },
  { field: "earliest", dir: "asc" },
];

function parseSortParam(raw: string | null): SortEntry[] {
  if (raw === null) return [...DEFAULT_SORT];
  if (raw === "none" || raw === "") return [];
  const entries: SortEntry[] = [];
  const seen = new Set<SortField>();
  for (const token of raw.split(",")) {
    const t = token.trim();
    if (!t) continue;
    const m = t.match(/^(state|town|type|shape|color|earliest|latest)_(asc|desc)$/);
    if (!m) continue;
    const field = m[1] as SortField;
    if (seen.has(field)) continue;
    seen.add(field);
    entries.push({ field, dir: m[2] as SortDir });
  }
  return entries;
}

function serializeSort(entries: SortEntry[]): string {
  return entries.map((e) => `${e.field}_${e.dir}`).join(",");
}

function isDefaultSort(entries: SortEntry[]): boolean {
  return (
    entries.length === DEFAULT_SORT.length &&
    entries.every((e, i) => e.field === DEFAULT_SORT[i].field && e.dir === DEFAULT_SORT[i].dir)
  );
}

/**
 * Build the DRF `?ordering=` value from the entries. Appends sensible
 * tiebreakers (region, post office name) and finally `id` for determinism,
 * skipping any that the user already explicitly picked.
 */
function orderingParamForSort(entries: SortEntry[]): string {
  if (entries.length === 0) return "id";
  if (isDefaultSort(entries)) {
    return [
      "post_office__post_office_regions__region__name",
      "is_manuscript",
      "post_office__name",
      "earliest_seen",
      "id",
    ].join(",");
  }
  const cols: string[] = [];
  for (const e of entries) {
    const col = SORT_FIELD_COLUMN[e.field];
    cols.push((e.dir === "desc" ? "-" : "") + col);
  }
  cols.push("id");
  return cols.join(",");
}

function validateYearString(raw: string, minYear: number, maxYear: number): string | null {
  const v = raw.trim();
  if (!v) return null;
  if (v.length !== 4) return null; // wait until user types 4 digits
  const n = Number(v);
  if (Number.isNaN(n)) return "Year must be a number";
  if (n < minYear || n > maxYear) {
    return `Year must be between ${minYear} and ${maxYear}`;
  }
  return null;
}

/** Read a single search param with default */
function getSearchParam(params: URLSearchParams, key: string, defaultValue: string): string {
  const v = params.get(key);
  return v ?? defaultValue;
}

const noImageClassName = "w-full h-full min-w-0 min-h-0 object-cover bg-muted";

/** Placeholder when image is missing or fails to load. Shows fallback artwork instead of text. */
function ImageOrPlaceholder({
  src,
  alt,
  className,
}: {
  src: string | null;
  alt: string;
  className?: string;
}) {
  const [error, setError] = useState(false);
  if (error) {
    return (
      <img
        src={imageNotAvailable}
        alt="No image available"
        className={cn(noImageClassName, className)}
      />
    );
  }
  if (!src) {
    return (
      <img
        src={imageNotAvailable}
        alt="No image available"
        className={cn(noImageClassName, className)}
      />
    );
  }
  return <img src={src} alt={alt} className={className} onError={() => setError(true)} />;
}

/** Build compact page numbers for pagination (handles 500+ pages) */
function getPaginationPages(currentPage: number, totalPages: number): (number | "ellipsis")[] {
  const delta = 2;
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const pages: (number | "ellipsis")[] = [1];
  if (currentPage > delta + 2) pages.push("ellipsis");
  const start = Math.max(2, currentPage - delta);
  const end = Math.min(totalPages - 1, currentPage + delta);
  for (let i = start; i <= end; i++) pages.push(i);
  if (currentPage < totalPages - delta - 1) pages.push("ellipsis");
  pages.push(totalPages);
  return pages;
}

/**
 * Filter label with hover-revealed up/down arrows that drive the multi-column
 * catalogSort list. The active direction is visible but inert; clicking the
 * opposite arrow flips the direction and makes this field primary.
 */
function SortableLabel({
  htmlFor,
  label,
  field,
  currentSort,
  onToggle,
}: {
  htmlFor?: string;
  label: string;
  field: SortField;
  currentSort: SortEntry[];
  onToggle: (field: SortField, dir: SortDir) => void;
}) {
  const entry = currentSort.find((e) => e.field === field) ?? null;
  const isAsc = entry?.dir === "asc";
  const isDesc = entry?.dir === "desc";
  return (
    <div className="group flex items-center gap-1">
      <Label htmlFor={htmlFor}>{label}</Label>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={`Sort by ${label} ascending`}
            aria-pressed={isAsc}
            disabled={isAsc}
            onClick={() => onToggle(field, "asc")}
            className={cn(
              "p-0.5 rounded transition-opacity disabled:cursor-default disabled:hover:bg-transparent",
              isAsc
                ? "text-foreground opacity-100"
                : "text-muted-foreground opacity-0 hover:bg-muted group-hover:opacity-100 focus:opacity-100",
            )}
          >
            <ArrowUp className="h-3 w-3" />
          </button>
        </TooltipTrigger>
        <TooltipContent>Sort by Ascending</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={`Sort by ${label} descending`}
            aria-pressed={isDesc}
            disabled={isDesc}
            onClick={() => onToggle(field, "desc")}
            className={cn(
              "p-0.5 rounded transition-opacity disabled:cursor-default disabled:hover:bg-transparent",
              isDesc
                ? "text-foreground opacity-100"
                : "text-muted-foreground opacity-0 hover:bg-muted group-hover:opacity-100 focus:opacity-100",
            )}
          >
            <ArrowDown className="h-3 w-3" />
          </button>
        </TooltipTrigger>
        <TooltipContent>Sort by Descending</TooltipContent>
      </Tooltip>
    </div>
  );
}

const Search = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [viewMode, setViewMode] = useState<"gallery" | "list">("list");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const navigate = useNavigate();
  const { toast } = useToast();
  const authUser = useAuth();

  // Fetch filter options from API (colors, marking shapes, states)
  const { colorOptions, shapeOptions, stateOptions, isLoading: isLoadingFilters, error: filterError } =
    useFilterOptions();

  // Catalog's earliest/latest observed year -- used for input placeholders and validation bounds.
  const { earliestYear: minYear, latestYear: maxYear } = useMarkingYearRange();

  // Filter states - initialize from URL so filters persist when navigating back from detail
  const [keywordSearch, setKeywordSearch] = useState(() => getSearchParam(searchParams, "q", ""));
  const [stateFilter, setStateFilter] = useState(() => getSearchParam(searchParams, "state", "all"));
  const [townFilter, setTownFilter] = useState(() => getSearchParam(searchParams, "town", ""));
  const [referenceWorkFilter, setReferenceWorkFilter] = useState(() =>
    getSearchParam(searchParams, "referenceWork", "all"),
  );
  const [beginYear, setBeginYear] = useState(() => getSearchParam(searchParams, "from", ""));
  const [endYear, setEndYear] = useState(() => getSearchParam(searchParams, "to", ""));
  const [heightFilter, setHeightFilter] = useState(() => getSearchParam(searchParams, "height", ""));
  const [widthFilter, setWidthFilter] = useState(() => getSearchParam(searchParams, "width", ""));
  const [shapeFilter, setShapeFilter] = useState(() =>
    getSearchParam(searchParams, "shape", "") || getSearchParam(searchParams, "type", "all"),
  );
  const [typeFilter, setTypeFilter] = useState(() => getSearchParam(searchParams, "markType", "all"));
  const [colorFilter, setColorFilter] = useState(() => getSearchParam(searchParams, "color", "all"));
  const [valuationFilter, setValuationFilter] = useState("all");
  const [manuscriptFilter, setManuscriptFilter] = useState<"both" | "only" | "none">(() => {
    const raw = getSearchParam(searchParams, "manuscripts", "");
    if (raw === "only" || raw === "none" || raw === "both") return raw;
    if (getSearchParam(searchParams, "noManuscripts", "") === "true") return "none";
    return "both";
  });
  const [imagesOnly, setImagesOnly] = useState(() => getSearchParam(searchParams, "images", "") === "true");
  const [institutionalOnly, setInstitutionalOnly] = useState(
    () => getSearchParam(searchParams, "institutional", "") === "true",
  );
  const [reviewedFilter, setReviewedFilter] = useState<"all" | "reviewed" | "unreviewed">(() => {
    const raw = getSearchParam(searchParams, "reviewed", "");
    return raw === "reviewed" || raw === "unreviewed" ? raw : "all";
  });
  const [submissionQueueSort, setSubmissionQueueSort] = useState<SubmissionQueueSortOption>(
    () => (getSearchParam(searchParams, "sort", "newest") === "oldest" ? "oldest" : "newest"),
  );
  const [catalogSort, setCatalogSort] = useState<SortEntry[]>(() =>
    parseSortParam(searchParams.get("order")),
  );

  // Apply an up/down arrow click on a filter label. The clicked field becomes
  // the primary sort; clicking the already-active direction leaves it alone.
  const toggleSort = (field: SortField, dir: SortDir) => {
    setCatalogSort((prev) => {
      const idx = prev.findIndex((e) => e.field === field);
      if (idx === -1) return [{ field, dir }, ...prev];
      if (prev[idx].dir === dir) return prev;
      return [{ field, dir }, ...prev.filter((_, i) => i !== idx)];
    });
  };

  const catalogSortKey = useMemo(() => serializeSort(catalogSort), [catalogSort]);

  // Debounced values for text inputs - API called only after user stops typing
  const debouncedKeywordSearch = useDebounce(keywordSearch, DEBOUNCE_MS);
  const debouncedTownFilter = useDebounce(townFilter, DEBOUNCE_MS);
  const debouncedBeginYear = useDebounce(beginYear, DEBOUNCE_MS);
  const debouncedEndYear = useDebounce(endYear, DEBOUNCE_MS);
  const debouncedHeightFilter = useDebounce(heightFilter, DEBOUNCE_MS);
  const debouncedWidthFilter = useDebounce(widthFilter, DEBOUNCE_MS);

  // Pagination - 10 records per page from api/markings/
  const [currentPage, setCurrentPage] = useState(() => {
    const p = searchParams.get("page");
    const n = p ? parseInt(p, 10) : 1;
    return Number.isNaN(n) || n < 1 ? 1 : n;
  });
  const [goToPageInput, setGoToPageInput] = useState("");
  const [itemsPerPage, setItemsPerPage] = useState(() => {
    const raw = searchParams.get("pageSize") || searchParams.get("page_size") || "";
    const n = raw ? parseInt(raw, 10) : 10;
    if (n === 25 || n === 50 || n === 100) return n;
    return 10;
  });

  const prevKeywordRef = useRef(debouncedKeywordSearch);
  const prevShapeFilterRef = useRef(shapeFilter);
  const prevColorFilterRef = useRef(colorFilter);
  const prevStateFilterRef = useRef(stateFilter);
  const prevTownFilterRef = useRef(debouncedTownFilter);
  const prevReferenceWorkFilterRef = useRef(referenceWorkFilter);
  const prevBeginYearRef = useRef(debouncedBeginYear.trim().length === 4 ? debouncedBeginYear.trim() : "");
  const prevEndYearRef = useRef(debouncedEndYear.trim().length === 4 ? debouncedEndYear.trim() : "");
  const prevHeightFilterRef = useRef("");
  const prevWidthFilterRef = useRef("");
  const prevImagesOnlyRef = useRef(imagesOnly);
  const prevInstitutionalOnlyRef = useRef(institutionalOnly);
  const prevManuscriptFilterRef = useRef(manuscriptFilter);
  const prevReviewedFilterRef = useRef(reviewedFilter);
  const prevTypeFilterRef = useRef(typeFilter);
  const prevSortRef = useRef(submissionQueueSort);
  const prevCatalogSortRef = useRef(catalogSortKey);

  const beginYearError = useMemo(
    () => validateYearString(beginYear, minYear, maxYear),
    [beginYear, minYear, maxYear],
  );
  const endYearError = useMemo(
    () => validateYearString(endYear, minYear, maxYear),
    [endYear, minYear, maxYear],
  );

  // Manuscripts have null shape, so the two filters are mutually exclusive:
  // - manuscripts=Only -> Shape field is cleared and hidden (no shape to filter on).
  // - shape selected  -> "Only" option in the manuscripts dropdown is disabled
  //   (and snapped back to "both" if somehow already on "only" via URL state).
  useEffect(() => {
    if (manuscriptFilter === "only" && shapeFilter !== "all") {
      setShapeFilter("all");
    }
  }, [manuscriptFilter, shapeFilter]);

  useEffect(() => {
    if (shapeFilter !== "all" && manuscriptFilter === "only") {
      setManuscriptFilter("both");
    }
  }, [shapeFilter, manuscriptFilter]);

  // Circle-family shapes whose width == height == diameter. Matched by the
  // shape code prefix (the part before " - " in the label) so the UI collapses
  // height/width into a single Diameter input. Explicit allow-list because
  // other curved shapes (ARC - Arc or Semi-circle) are not true circles.
  const isCircleShape = useMemo(() => {
    if (shapeFilter === "all") return false;
    const opts = Array.isArray(shapeOptions) ? shapeOptions : [];
    const selected = opts.find((s) => s.value === shapeFilter);
    if (!selected) return false;
    return isTrueCircleShapeName(selected.label);
  }, [shapeFilter, shapeOptions]);
  // While the circle UI is showing, keep widthFilter mirrored to heightFilter so
  // the API sees height == width and the URL persistence stays symmetric.
  useEffect(() => {
    if (isCircleShape && widthFilter !== heightFilter) {
      setWidthFilter(heightFilter);
    }
  }, [isCircleShape, heightFilter, widthFilter]);

  // Reset page to 1 when filters change
  useEffect(() => {
    const currentNormalizedBegin = debouncedBeginYear.trim().length === 4 ? debouncedBeginYear.trim() : "";
    const currentNormalizedEnd = debouncedEndYear.trim().length === 4 ? debouncedEndYear.trim() : "";

    const searchJustChanged = prevKeywordRef.current !== debouncedKeywordSearch;
    const shapeFilterJustChanged = prevShapeFilterRef.current !== shapeFilter;
    const colorFilterJustChanged = prevColorFilterRef.current !== colorFilter;
    const stateFilterJustChanged = prevStateFilterRef.current !== stateFilter;
    const townFilterJustChanged = prevTownFilterRef.current !== debouncedTownFilter;
    const referenceWorkFilterJustChanged = prevReferenceWorkFilterRef.current !== referenceWorkFilter;
    const beginYearJustChanged = prevBeginYearRef.current !== currentNormalizedBegin;
    const endYearJustChanged = prevEndYearRef.current !== currentNormalizedEnd;
    // Inline normalization to avoid forward-referencing normalizedHeight /
    // normalizedWidth, which are declared later (TDZ).
    const normalizeDim = (raw: string): string => {
      const t = raw.trim();
      if (!t) return "";
      const n = Number(t);
      if (!Number.isFinite(n) || n <= 0) return "";
      return t;
    };
    const currentNormalizedHeight = normalizeDim(debouncedHeightFilter);
    const currentNormalizedWidth = normalizeDim(debouncedWidthFilter);
    const heightFilterJustChanged = prevHeightFilterRef.current !== currentNormalizedHeight;
    const widthFilterJustChanged = prevWidthFilterRef.current !== currentNormalizedWidth;
    const imagesOnlyJustChanged = prevImagesOnlyRef.current !== imagesOnly;
    const institutionalOnlyJustChanged = prevInstitutionalOnlyRef.current !== institutionalOnly;
    const manuscriptFilterJustChanged = prevManuscriptFilterRef.current !== manuscriptFilter;
    const reviewedFilterJustChanged = prevReviewedFilterRef.current !== reviewedFilter;
    const typeFilterJustChanged = prevTypeFilterRef.current !== typeFilter;
    const sortJustChanged = prevSortRef.current !== submissionQueueSort;
    const catalogSortJustChanged = prevCatalogSortRef.current !== catalogSortKey;
    if (searchJustChanged) prevKeywordRef.current = debouncedKeywordSearch;
    if (shapeFilterJustChanged) prevShapeFilterRef.current = shapeFilter;
    if (colorFilterJustChanged) prevColorFilterRef.current = colorFilter;
    if (stateFilterJustChanged) prevStateFilterRef.current = stateFilter;
    if (townFilterJustChanged) prevTownFilterRef.current = debouncedTownFilter;
    if (referenceWorkFilterJustChanged) prevReferenceWorkFilterRef.current = referenceWorkFilter;
    if (beginYearJustChanged) prevBeginYearRef.current = currentNormalizedBegin;
    if (endYearJustChanged) prevEndYearRef.current = currentNormalizedEnd;
    if (heightFilterJustChanged) prevHeightFilterRef.current = currentNormalizedHeight;
    if (widthFilterJustChanged) prevWidthFilterRef.current = currentNormalizedWidth;
    if (imagesOnlyJustChanged) prevImagesOnlyRef.current = imagesOnly;
    if (institutionalOnlyJustChanged) prevInstitutionalOnlyRef.current = institutionalOnly;
    if (manuscriptFilterJustChanged) prevManuscriptFilterRef.current = manuscriptFilter;
    if (reviewedFilterJustChanged) prevReviewedFilterRef.current = reviewedFilter;
    if (typeFilterJustChanged) prevTypeFilterRef.current = typeFilter;
    if (sortJustChanged) prevSortRef.current = submissionQueueSort;
    if (catalogSortJustChanged) prevCatalogSortRef.current = catalogSortKey;

    const anyFilterChanged =
      searchJustChanged ||
      shapeFilterJustChanged ||
      colorFilterJustChanged ||
      stateFilterJustChanged ||
      townFilterJustChanged ||
      referenceWorkFilterJustChanged ||
      beginYearJustChanged ||
      endYearJustChanged ||
      heightFilterJustChanged ||
      widthFilterJustChanged ||
      imagesOnlyJustChanged ||
      institutionalOnlyJustChanged ||
      manuscriptFilterJustChanged ||
      reviewedFilterJustChanged ||
      typeFilterJustChanged ||
      sortJustChanged ||
      catalogSortJustChanged;
    if (anyFilterChanged) {
      setCurrentPage(1);
    }
  }, [debouncedKeywordSearch, shapeFilter, stateFilter, debouncedTownFilter, referenceWorkFilter, debouncedBeginYear, debouncedEndYear, debouncedHeightFilter, debouncedWidthFilter, imagesOnly, institutionalOnly, colorFilter, manuscriptFilter, reviewedFilter, typeFilter, submissionQueueSort, catalogSortKey]);

  // Treat years as active filters only when they are valid and 4 digits.
  const normalizedBeginYear = useMemo(() => {
    return validateYearString(debouncedBeginYear, minYear, maxYear)
      ? ""
      : (debouncedBeginYear.trim().length === 4 ? debouncedBeginYear.trim() : "");
  }, [debouncedBeginYear, minYear, maxYear]);
  const normalizedEndYear = useMemo(() => {
    return validateYearString(debouncedEndYear, minYear, maxYear)
      ? ""
      : (debouncedEndYear.trim().length === 4 ? debouncedEndYear.trim() : "");
  }, [debouncedEndYear, minYear, maxYear]);
  // Dimensions: only treat as active when the input parses to a positive number.
  const normalizeDimensionInput = (raw: string): string => {
    const t = raw.trim();
    if (!t) return "";
    const n = Number(t);
    if (!Number.isFinite(n) || n <= 0) return "";
    return t;
  };
  const normalizedHeight = useMemo(
    () => normalizeDimensionInput(debouncedHeightFilter),
    [debouncedHeightFilter],
  );
  const normalizedWidth = useMemo(
    () => normalizeDimensionInput(debouncedWidthFilter),
    [debouncedWidthFilter],
  );

  // Map UI typeFilter ("all" | "townmark" | "ratemark" | "auxmark") to API value.
  const typeFilterApi: MarkingTypeValue | "all" =
    typeFilter === "townmark"
      ? "TOWNMARK"
      : typeFilter === "ratemark"
        ? "RATEMARK"
        : typeFilter === "auxmark"
          ? "AUXMARK"
          : "all";

  const {
    data: referenceWorkOptionsRaw = [],
    isLoading: referenceWorkOptionsLoading,
    error: referenceWorkOptionsError,
  } = useQuery({
    queryKey: ["reference-work-options"],
    queryFn: getReferenceWorkOptions,
    staleTime: 5 * 60 * 1000,
  });

  const referenceWorkOptions = useMemo(
    () =>
      referenceWorkOptionsRaw.map((option) => ({
        value: option.code,
        label: option.label,
      })),
    [referenceWorkOptionsRaw],
  );

  // Fetch markings with React Query - cached so Back shows previous results immediately.
  // The unified /markings/ endpoint already returns one row per marking with its
  // own type discriminator, so no client-side fan-out is needed.
  const {
    data: queryData,
    isLoading: queryLoading,
    isFetching: queryFetching,
    error: queryError,
  } = useQuery({
    queryKey: [
      "markings",
      currentPage,
      debouncedKeywordSearch,
      shapeFilter,
      stateFilter,
      debouncedTownFilter,
      referenceWorkFilter,
      normalizedBeginYear,
      normalizedEndYear,
      normalizedHeight,
      normalizedWidth,
      imagesOnly,
      institutionalOnly,
      colorFilter,
      manuscriptFilter,
      reviewedFilter,
      typeFilterApi,
      itemsPerPage,
      catalogSortKey,
    ],
    queryFn: async () => {
      const normalizedFrom = normalizedBeginYear || undefined;
      const normalizedTo = normalizedEndYear || undefined;

      const { results, count, count_capped } = await getMarkingsPage(
        currentPage,
        itemsPerPage,
        {
          search: debouncedKeywordSearch.trim() || undefined,
          shapeId: shapeFilter !== "all" ? shapeFilter : undefined,
          type: typeFilterApi,
          manuscripts: manuscriptFilter,
          color: colorFilter !== "all" ? colorFilter : undefined,
          state: stateFilter !== "all" ? stateFilter : undefined,
          town: debouncedTownFilter.trim() || undefined,
          referenceWorkCode: referenceWorkFilter !== "all" ? referenceWorkFilter : undefined,
          beginYear: normalizedFrom,
          endYear: normalizedTo,
          height: normalizedHeight || undefined,
          width: normalizedWidth || undefined,
          hasImages: imagesOnly,
          institutional: institutionalOnly,
          reviewed: reviewedFilter !== "all" ? reviewedFilter : undefined,
          ordering: orderingParamForSort(catalogSort),
        }
      );
      return { records: results, count, count_capped };
    },
    // Always refetch when the user navigates to a different page (or changes
    // any other filter / sort). Without this, going back to a previously
    // visited page returned the cached result and skipped the API call,
    // which made it look like pagination "stopped working" when navigating
    // to lower page numbers.
    staleTime: 0,
    // Keep the previous page's records on screen during the refetch instead
    // of flashing the empty/loading state when paginating.
    placeholderData: keepPreviousData,
  });

  const catalogRecords: MarkingRecord[] = queryData?.records ?? [];
  const totalCount = queryData?.count ?? 0;
  const countCapped = queryData?.count_capped ?? false;
  // Show loading only when we have no data; when we have cached data, show it (no spinner)
  const loading = queryLoading || (queryFetching && catalogRecords.length === 0);
  const refreshing = queryFetching && !loading;
  const filtersDisabled = loading;
  // The reviewed/confirmed filter is a state-editor workflow tool (Issue #22);
  // only surface it to editors/admins. (The flag itself is in the public
  // serializer, so this is a UI affordance, not a security boundary.)
  const isEditor =
    !!authUser &&
    (authUser.role === "editor" ||
      authUser.role === "administrator" ||
      authUser.is_superuser === true);

  useEffect(() => {
    if (queryError) {
      toast({
        title: "Error loading catalog",
        description: (queryError as Error).message,
        variant: "destructive",
      });
    }
  }, [queryError, toast]);

  // Persist filters to URL so they survive navigation (Back from detail page)
  useEffect(() => {
    const params = new URLSearchParams();
    if (debouncedKeywordSearch.trim()) params.set("q", debouncedKeywordSearch.trim());
    if (stateFilter !== "all") params.set("state", stateFilter);
    if (debouncedTownFilter.trim()) params.set("town", debouncedTownFilter.trim());
    if (referenceWorkFilter !== "all") params.set("referenceWork", referenceWorkFilter);
    // Only persist valid years (normalized values) to the URL
    if (normalizedBeginYear) params.set("from", normalizedBeginYear);
    if (normalizedEndYear) params.set("to", normalizedEndYear);
    if (normalizedHeight) params.set("height", normalizedHeight);
    if (normalizedWidth) params.set("width", normalizedWidth);
    if (shapeFilter !== "all") params.set("shape", shapeFilter);
    if (typeFilter !== "all") params.set("markType", typeFilter);
    if (colorFilter !== "all") params.set("color", colorFilter);
    if (manuscriptFilter !== "both") params.set("manuscripts", manuscriptFilter);
    if (imagesOnly) params.set("images", "true");
    if (institutionalOnly) params.set("institutional", "true");
    if (reviewedFilter !== "all") params.set("reviewed", reviewedFilter);
    if (submissionQueueSort !== "newest") params.set("sort", submissionQueueSort);
    // Empty list (user toggled off all sorts) -> persist as the sentinel
    // "none" so a page reload distinguishes that intent from "no param".
    if (!isDefaultSort(catalogSort)) params.set("order", catalogSortKey || "none");
    if (itemsPerPage !== 10) params.set("pageSize", String(itemsPerPage));
    if (currentPage > 1) params.set("page", String(currentPage));
    const next = params.toString();
    const current = searchParams.toString();
    if (next !== current) {
      setSearchParams(next ? params : {}, { replace: true });
    }
  }, [currentPage, debouncedKeywordSearch, stateFilter, debouncedTownFilter, referenceWorkFilter, normalizedBeginYear, normalizedEndYear, normalizedHeight, normalizedWidth, shapeFilter, typeFilter, colorFilter, manuscriptFilter, imagesOnly, institutionalOnly, reviewedFilter, submissionQueueSort, catalogSort, catalogSortKey, itemsPerPage, searchParams, setSearchParams]);

  const totalPages = Math.ceil(totalCount / itemsPerPage) || 1;
  const pageStart = (currentPage - 1) * itemsPerPage;
  const pageEnd = Math.min(currentPage * itemsPerPage, totalCount);
  // Render exactly what the server returned. Sorting is fully server-side via
  // the `?ordering=` param built in orderingParamForSort(catalogSort); a prior
  // client-side re-sort by id was silently overriding the user's chosen
  // "Sort Results" column.
  const paginatedResults = catalogRecords;

  useEffect(() => {
    if (!queryLoading && currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, queryLoading, totalPages]);

  // Clear all filters and URL params
  const handleClearAllFilters = () => {
    setKeywordSearch("");
    setStateFilter("all");
    setTownFilter("");
    setReferenceWorkFilter("all");
    setBeginYear("");
    setEndYear("");
    setHeightFilter("");
    setWidthFilter("");
    setShapeFilter("all");
    setTypeFilter("all");
    setColorFilter("all");
    setValuationFilter("all");
    setManuscriptFilter("both");
    setImagesOnly(false);
    setInstitutionalOnly(false);
    setReviewedFilter("all");
    setSubmissionQueueSort("newest");
    setCatalogSort([...DEFAULT_SORT]);
    setCurrentPage(1);
    setItemsPerPage(10);
    setSearchParams("", { replace: true });
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />

      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {/* Header */}
          <div className="mb-8">
            <h1 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-2">
              Catalog Search
            </h1>
            <p className="text-muted-foreground">
              Search and filter through our comprehensive collection of American stampless postal markings.
            </p>
          </div>

          <div className="flex flex-col lg:flex-row gap-6">
            {/* Filters Sidebar */}
            <aside className={`lg:w-80 space-y-6 ${filtersOpen ? 'block' : 'hidden lg:block'}`}>
              <Card className="shadow-archival-md">
                <CardContent className="pt-6 space-y-4">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="font-heading text-lg font-semibold">Filters</h2>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-auto p-0 text-sm font-normal text-muted-foreground hover:text-foreground"
                        onClick={handleClearAllFilters}
                        disabled={filtersDisabled}
                      >
                        Clear filters
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="lg:hidden"
                        onClick={() => setFiltersOpen(false)}
                      >
                        Close
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="keyword-search">Search</Label>
                    <div className="relative">
                      <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="keyword-search"
                        type="search"
                        placeholder="Search records, citations..."
                        value={keywordSearch}
                        onChange={(e) => setKeywordSearch(e.target.value)}
                        className="pl-9"
                        aria-label="Search records by code, inscription text, town, state, shape, lettering, color, or reference work code or name"
                        disabled={filtersDisabled}
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="state"
                      label="State"
                      field="state"
                      currentSort={catalogSort}
                      onToggle={toggleSort}
                    />
                    <SearchableSelect
                      id="state"
                      value={stateFilter}
                      onValueChange={setStateFilter}
                      placeholder="All States"
                      allOption={{ value: "all", label: "All States" }}
                      options={Array.isArray(stateOptions) ? stateOptions : []}
                      loading={isLoadingFilters}
                      error={!!filterError}
                      errorMessage="Failed to load states"
                      searchPlaceholder="Search states..."
                      emptyMessage="No state found."
                      aria-label="Filter by state"
                      disabled={filtersDisabled}
                    />
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="town"
                      label="Town"
                      field="town"
                      currentSort={catalogSort}
                      onToggle={toggleSort}
                    />
                    <Input
                      id="town"
                      placeholder="Enter town name..."
                      value={townFilter}
                      onChange={(e) => setTownFilter(e.target.value)}
                      disabled={filtersDisabled}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="citation-reference">Citation</Label>
                    <SearchableSelect
                      id="citation-reference"
                      value={referenceWorkFilter}
                      onValueChange={setReferenceWorkFilter}
                      placeholder="All References"
                      allOption={{ value: "all", label: "All References" }}
                      options={referenceWorkOptions}
                      loading={referenceWorkOptionsLoading}
                      error={!!referenceWorkOptionsError}
                      errorMessage="Failed to load references"
                      searchPlaceholder="Search reference code or name..."
                      emptyMessage="No reference found."
                      aria-label="Filter by citation reference work"
                      disabled={filtersDisabled}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="beginYear"
                        label="Begin Year"
                        field="earliest"
                        currentSort={catalogSort}
                        onToggle={toggleSort}
                      />
                      <Input
                        id="beginYear"
                        type="number"
                        placeholder={String(minYear)}
                        inputMode="numeric"
                        value={beginYear}
                        onChange={(e) => {
                          const v = e.target.value.replace(/\D/g, "").slice(0, 4);
                          setBeginYear(v);
                        }}
                        disabled={filtersDisabled}
                        className={cn(
                          "[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none",
                          beginYearError ? "border-destructive" : ""
                        )}
                      />
                      {beginYearError && (
                        <p className="text-xs text-destructive">{beginYearError}</p>
                      )}
                    </div>
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="endYear"
                        label="End Year"
                        field="latest"
                        currentSort={catalogSort}
                        onToggle={toggleSort}
                      />
                      <Input
                        id="endYear"
                        type="number"
                        placeholder={String(maxYear)}
                        inputMode="numeric"
                        value={endYear}
                        onChange={(e) => {
                          const v = e.target.value.replace(/\D/g, "").slice(0, 4);
                          setEndYear(v);
                        }}
                        disabled={filtersDisabled}
                        className={cn(
                          "[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none",
                          endYearError ? "border-destructive" : ""
                        )}
                      />
                      {endYearError && (
                        <p className="text-xs text-destructive">{endYearError}</p>
                      )}
                    </div>
                  </div>

                  {manuscriptFilter !== "only" && (
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="shape"
                        label="Shape"
                        field="shape"
                        currentSort={catalogSort}
                        onToggle={toggleSort}
                      />
                      <SearchableSelect
                        id="shape"
                        disabled={filtersDisabled}
                        value={shapeFilter}
                        onValueChange={setShapeFilter}
                        placeholder="All Shapes"
                        allOption={{ value: "all", label: "All Shapes" }}
                        options={Array.isArray(shapeOptions) ? shapeOptions : []}
                        loading={isLoadingFilters}
                        error={!!filterError}
                        errorMessage="Failed to load shapes"
                        searchPlaceholder="Search shapes..."
                        emptyMessage="No shape found."
                        aria-label="Filter by shape"
                      />
                    </div>
                  )}

                  {isCircleShape ? (
                    <div className="space-y-2">
                      <Label htmlFor="diameterFilter">Diameter (mm)</Label>
                      <Input
                        id="diameterFilter"
                        type="text"
                        placeholder="Any"
                        inputMode="decimal"
                        value={heightFilter}
                        onChange={(e) => {
                          let v = e.target.value.replace(/[^0-9.]/g, "");
                          const firstDot = v.indexOf(".");
                          if (firstDot !== -1) {
                            v = v.slice(0, firstDot + 1) + v.slice(firstDot + 1).replace(/\./g, "");
                          }
                          setHeightFilter(v);
                          setWidthFilter(v);
                        }}
                        disabled={filtersDisabled}
                        className="[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                        aria-label="Filter by diameter in millimeters"
                      />
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="heightFilter">Height (mm)</Label>
                        <Input
                          id="heightFilter"
                          type="text"
                          placeholder="Any"
                          inputMode="decimal"
                          value={heightFilter}
                          onChange={(e) => {
                            let v = e.target.value.replace(/[^0-9.]/g, "");
                            const firstDot = v.indexOf(".");
                            if (firstDot !== -1) {
                              v = v.slice(0, firstDot + 1) + v.slice(firstDot + 1).replace(/\./g, "");
                            }
                            setHeightFilter(v);
                          }}
                          disabled={filtersDisabled}
                          className="[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                          aria-label="Filter by height in millimeters"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="widthFilter">Width (mm)</Label>
                        <Input
                          id="widthFilter"
                          type="text"
                          placeholder="Any"
                          inputMode="decimal"
                          value={widthFilter}
                          onChange={(e) => {
                            let v = e.target.value.replace(/[^0-9.]/g, "");
                            const firstDot = v.indexOf(".");
                            if (firstDot !== -1) {
                              v = v.slice(0, firstDot + 1) + v.slice(firstDot + 1).replace(/\./g, "");
                            }
                            setWidthFilter(v);
                          }}
                          disabled={filtersDisabled}
                          className="[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                          aria-label="Filter by width in millimeters"
                        />
                      </div>
                    </div>
                  )}

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="mark-type"
                      label="Type"
                      field="type"
                      currentSort={catalogSort}
                      onToggle={toggleSort}
                    />
                    <Select value={typeFilter} onValueChange={setTypeFilter} disabled={filtersDisabled}>
                      <SelectTrigger id="mark-type">
                        <SelectValue placeholder="All Types" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Types</SelectItem>
                        <SelectItem value="townmark">Townmark</SelectItem>
                        <SelectItem value="ratemark">Ratemark</SelectItem>
                        <SelectItem value="auxmark">Auxmark</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="color"
                      label="Color"
                      field="color"
                      currentSort={catalogSort}
                      onToggle={toggleSort}
                    />
                    <SearchableSelect
                      id="color"
                      value={colorFilter}
                      onValueChange={setColorFilter}
                      placeholder="All Colors"
                      allOption={{ value: "all", label: "All Colors" }}
                      options={Array.isArray(colorOptions) ? colorOptions : []}
                      loading={isLoadingFilters}
                      error={!!filterError}
                      errorMessage="Failed to load colors"
                      searchPlaceholder="Search colors..."
                      emptyMessage="No color found."
                      aria-label="Filter by color"
                      disabled={filtersDisabled}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="manuscripts">Show Manuscripts</Label>
                    <Select
                      value={manuscriptFilter}
                      onValueChange={(v) => setManuscriptFilter(v as "both" | "only" | "none")}
                      disabled={filtersDisabled}
                    >
                      <SelectTrigger id="manuscripts" aria-label="Show manuscripts">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="both">Both (Default)</SelectItem>
                        <SelectItem value="only" disabled={shapeFilter !== "all"}>Only</SelectItem>
                        <SelectItem value="none">None</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {isEditor && (
                    <div className="space-y-2">
                      <Label htmlFor="reviewed">Review Status</Label>
                      <Select
                        value={reviewedFilter}
                        onValueChange={(v) =>
                          setReviewedFilter(v as "all" | "reviewed" | "unreviewed")
                        }
                        disabled={filtersDisabled}
                      >
                        <SelectTrigger id="reviewed" aria-label="Filter by review status">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">All (Default)</SelectItem>
                          <SelectItem value="reviewed">Reviewed</SelectItem>
                          <SelectItem value="unreviewed">Unreviewed</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  )}

                  <div className="space-y-3 pt-2">
                    <div className="flex items-center space-x-2">
                      <Checkbox
                        disabled={filtersDisabled}
                        id="imagesOnly"
                        checked={imagesOnly}
                        onCheckedChange={(checked) => setImagesOnly(checked as boolean)}
                      />
                      <label
                        htmlFor="imagesOnly"
                        className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                      >
                        Images Only
                      </label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <Checkbox
                        disabled={filtersDisabled}
                        id="institutionalOnly"
                        checked={institutionalOnly}
                        onCheckedChange={(checked) => setInstitutionalOnly(checked as boolean)}
                      />
                      <label
                        htmlFor="institutionalOnly"
                        className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                      >
                        Institutional Only
                      </label>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </aside>

            {/* Results */}
            <main className="flex-1 space-y-4">
              {/* Results Header */}
              <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-card p-4 rounded-lg border border-border shadow-archival-sm">
                <div className="flex items-center gap-3">
                  <Button
                    variant="outline"
                    size="sm"
                    className="lg:hidden"
                    onClick={() => setFiltersOpen((open) => !open)}
                  >
                    <SlidersHorizontal className="h-4 w-4 mr-2" />
                    Filters
                  </Button>
                  <p className="text-sm text-muted-foreground">
                    {totalCount === 0 ? (
                      "0 markings"
                    ) : (
                      <>
                        Showing <span className="font-semibold text-foreground">{pageStart + 1}-{pageEnd}</span> of <span className="font-semibold text-foreground">{countCapped ? `${totalCount.toLocaleString()}+` : totalCount.toLocaleString()}</span> markings
                      </>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {refreshing && (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                    </div>
                  )}
                  <div className="flex border border-border rounded-md" aria-label="Catalog result view mode">
                    <Button
                      type="button"
                      variant={viewMode === "list" ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setViewMode("list")}
                      className="rounded-r-none"
                      aria-label="Show results as list"
                      aria-pressed={viewMode === "list"}
                    >
                      <List className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant={viewMode === "gallery" ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setViewMode("gallery")}
                      className="rounded-l-none"
                      aria-label="Show results as gallery"
                      aria-pressed={viewMode === "gallery"}
                    >
                      <Grid3x3 className="h-4 w-4" />
                    </Button>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => navigate("/contribute", { state: { from: "/search" } })}
                    className="shrink-0 bg-green-800 hover:bg-green-900 text-white"
                  >
                    <Plus className="mr-2 h-4 w-4" />
                    Submit New Marking
                  </Button>
                </div>
              </div>

              {/* Results Grid/List */}
              {loading ? (
                <div className="flex flex-col justify-center items-center gap-3 py-12 text-muted-foreground">
                  <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
                  <p className="text-muted-foreground">Loading catalog records...</p>
                </div>
              ) : catalogRecords.length === 0 ? (
                <div className="flex justify-center items-center py-12">
                  <p className="text-muted-foreground">No catalog records found.</p>
                </div>
              ) : viewMode === "list" ? (
                <div className="space-y-4">
                  {paginatedResults.map((record) => {
                    const row = buildCatalogSearchRow(record);
                    return (
                      <Card
                        key={row.cardId}
                        className="shadow-archival-md hover:shadow-archival-lg transition-shadow cursor-pointer"
                        onClick={() =>
                          navigate(`/record/${record.id}`, {
                            state: { fromSearch: true },
                          })
                        }
                      >
                        <CardContent className="p-4">
                          <div className="flex gap-6 md:flex-row flex-col">
                            <div className="md:w-32 md:h-32 w-full h-48 shrink-0 rounded border border-border bg-muted p-2 overflow-hidden">
                              <ImageOrPlaceholder
                                src={row.image}
                                alt={row.title}
                                className="h-full w-full object-contain"
                              />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-start justify-between gap-3 mb-2">
                                <h3 className="font-heading text-xl font-semibold text-foreground">
                                  {row.title}
                                </h3>
                              </div>
                              <CatalogRecordFields row={row} record={record} variant="list" />
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              ) : (
                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
                  {paginatedResults.map((record) => {
                    const row = buildCatalogSearchRow(record);
                    return (
                      <Card
                        key={row.cardId}
                        className="shadow-archival-md hover:shadow-archival-lg transition-shadow cursor-pointer overflow-hidden"
                        onClick={() =>
                          navigate(`/record/${record.id}`, {
                            state: { fromSearch: true },
                          })
                        }
                      >
                        {row.image2 ? (
                          <div className="grid grid-cols-2 gap-1 bg-muted">
                            <div className="h-48 border-r border-border p-2">
                              <ImageOrPlaceholder
                                src={row.image}
                                alt={row.title}
                                className="h-full w-full object-contain"
                              />
                            </div>
                            <div className="h-48 p-2">
                              <ImageOrPlaceholder
                                src={row.image2}
                                alt={`${row.title} (2)`}
                                className="h-full w-full object-contain"
                              />
                            </div>
                          </div>
                        ) : (
                          <div className="h-48 border-b border-border bg-muted p-2">
                            <ImageOrPlaceholder
                              src={row.image}
                              alt={row.title}
                              className="h-full w-full object-contain"
                            />
                          </div>
                        )}
                        <CardContent className="p-4">
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <h3 className="font-heading text-lg font-semibold text-foreground">
                              {row.title}
                            </h3>
                          </div>
                          <CatalogRecordFields row={row} record={record} variant="gallery" />
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}

              {/* Pagination - compact for 500+ pages */}
              {!loading && catalogRecords.length > 0 && (
                <div className="mt-8 flex flex-col items-center gap-4">
                  {totalPages > 1 && (
                    <Pagination>
                      <PaginationContent>
                        <PaginationItem>
                          <PaginationPrevious
                            onClick={() => {
                              setCurrentPage(p => Math.max(1, p - 1));
                            }}
                            className={currentPage === 1 ? "pointer-events-none opacity-50" : "cursor-pointer"}
                          />
                        </PaginationItem>

                        {getPaginationPages(currentPage, totalPages).map((p, i) =>
                          p === "ellipsis" ? (
                            <PaginationItem key={`ellipsis-${i}`}>
                              <PaginationEllipsis />
                            </PaginationItem>
                          ) : (
                            <PaginationItem key={p}>
                              <PaginationLink
                                onClick={() => {
                                  setCurrentPage(p);
                                }}
                                isActive={currentPage === p}
                                className="cursor-pointer"
                              >
                                {p}
                              </PaginationLink>
                            </PaginationItem>
                          )
                        )}

                        <PaginationItem>
                          <PaginationNext
                            onClick={() => {
                              setCurrentPage(p => Math.min(totalPages, p + 1));
                            }}
                            className={currentPage === totalPages ? "pointer-events-none opacity-50" : "cursor-pointer"}
                          />
                        </PaginationItem>
                      </PaginationContent>
                    </Pagination>
                  )}

                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">Records shown</span>
                    <Select
                      value={String(itemsPerPage)}
                      onValueChange={(v) => {
                        const n = parseInt(v, 10);
                        if (n === 10 || n === 25 || n === 50 || n === 100) {
                          if (n !== itemsPerPage) {
                            setCurrentPage(1);
                            setGoToPageInput("");
                          }
                          setItemsPerPage(n);
                        }
                      }}
                      disabled={filtersDisabled}
                    >
                      <SelectTrigger className="h-9 w-[80px]" aria-label="Records per page">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="10">10</SelectItem>
                        <SelectItem value="25">25</SelectItem>
                        <SelectItem value="50">50</SelectItem>
                        <SelectItem value="100">100</SelectItem>
                      </SelectContent>
                    </Select>
                    {totalPages > 1 && (
                      <>
                        <span className="text-sm text-muted-foreground">Go to page</span>
                        <Input
                          type="number"
                          min={1}
                          max={totalPages}
                          placeholder="Page"
                          value={goToPageInput}
                          onChange={(e) => {
                            const raw = e.target.value;
                            if (raw === "") {
                              setGoToPageInput("");
                              return;
                            }
                            const n = parseInt(raw, 10);
                            if (Number.isNaN(n)) return;
                            const clamped = Math.max(1, Math.min(totalPages, n));
                            setGoToPageInput(String(clamped));
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              const n = parseInt(goToPageInput, 10);
                              if (!Number.isNaN(n)) {
                                setCurrentPage(Math.max(1, Math.min(totalPages, n)));
                                setGoToPageInput("");
                              }
                            }
                          }}
                          className="h-9 w-16 text-center [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                          aria-label="Go to page number"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-9"
                          onClick={() => {
                            const n = parseInt(goToPageInput, 10);
                            if (!Number.isNaN(n)) {
                              setCurrentPage(Math.max(1, Math.min(totalPages, n)));
                              setGoToPageInput("");
                            }
                          }}
                        >
                          Go
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </main>
          </div>
        </div>
      </div>

      <Footer />
    </div>
  );
};

export default Search;
