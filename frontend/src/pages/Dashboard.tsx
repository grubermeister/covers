import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { Archive, ArchiveRestore, ArrowDown, ArrowUp, Calendar, Loader2, Pencil, Plus, Search as SearchIcon, SlidersHorizontal } from "lucide-react";
import { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate, useLocation, useSearchParams } from "react-router-dom";
import { useToast } from "@/hooks/use-toast";
import { formatSizeFromSubmittedData } from "@/lib/dimensionsMm";
import {
  isCoverContributionData,
  materializedCoverIdFromContribution,
  parentMarkingIdFromContribution,
} from "@/lib/contributionDisplay";
import { useAuth } from "@/hooks/useAuth";
import imageNotAvailable from "@/assets/image-not-available.jpg";
import { cn } from "@/lib/utils";
import {
  normalizeImageUrl,
  getRecycleBinMarkings,
  restoreMarking,
  type MarkingRecord,
} from "@/services/markings";
import { getRecycleBinCovers, type RecycleBinCover } from "@/services/covers";
import {
  archiveContribution,
  listContributions,
  restoreContribution,
} from "@/services/contributions";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { useFilterOptions } from "@/hooks/useFilterOptions";
import {
  buildDashboardParams,
  dashboardHref,
  editorOrderingParam,
  parseDashboardParams,
  rememberDashboardLocation,
  type DashboardParams,
} from "@/lib/dashboardParams";
import { useDebounce } from "@/hooks/useDebounce";

const noImageClassName = "w-full h-full min-w-0 min-h-0 object-cover bg-muted";

/** Matches Catalog Search's DEBOUNCE_MS -- one round trip per pause, not per key. */
const EDITOR_DEBOUNCE_MS = 800;
/** Only a well-formed date goes to the API; see the editorFrom/editorTo note. */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function resolveSubmissionImageUrl(
  c: Record<string, unknown>,
  submittedData: Record<string, unknown>,
): string | null {
  const mainImage = c.mainImage as { imageUrl?: unknown } | string | null | undefined;
  const mainImageFromList =
    (mainImage && typeof mainImage === "object" && typeof mainImage.imageUrl === "string"
      ? mainImage.imageUrl
      : null) ??
    (typeof mainImage === "string" ? mainImage : null);

  const direct = normalizeImageUrl(
    mainImageFromList ??
      (typeof c.imageUrl === "string" ? c.imageUrl : null) ??
      (typeof c.image_url === "string" ? c.image_url : null) ??
      null,
  );
  if (direct) return direct;

  const asUrlArray = (raw: unknown): string[] => {
    if (!Array.isArray(raw)) return [];
    return raw
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (!item || typeof item !== "object") return "";
        const obj = item as Record<string, unknown>;
        const fromUrl = obj.url ?? obj.image_url ?? obj.imageUrl ?? obj.public_url ?? obj.publicUrl;
        return typeof fromUrl === "string" ? fromUrl.trim() : "";
      })
      .filter((url) => url.length > 0);
  };
  const categorized = asUrlArray(submittedData.marking_images ?? submittedData.markingImages);
  for (const url of categorized) {
    const normalized = normalizeImageUrl(url);
    if (normalized) return normalized;
  }

  const baseImageUrl = (import.meta.env.VITE_IMAGE_URL as string | undefined) ?? "";
  const imageRoot = baseImageUrl.replace(/\/+$/, "") || "/media";
  const fromMeta = (meta: unknown): string | null => {
    if (!meta || typeof meta !== "object") return null;
    const obj = meta as Record<string, unknown>;
    const sf = obj.storage_filename ?? obj.storageFilename;
    if (typeof sf !== "string" || !sf) return null;
    return normalizeImageUrl(`${imageRoot}/${sf.replace(/^\/+/, "")}`);
  };
  const metas = submittedData.image_metas ?? submittedData.imageMetas;
  if (Array.isArray(metas)) {
    for (const meta of metas) {
      const url = fromMeta(meta);
      if (url) return url;
    }
  }
  return fromMeta(submittedData.image_meta ?? submittedData.imageMeta);
}

function contributionTitleFromSubmittedData(
  submittedData: Record<string, unknown>,
  fallbackId: unknown,
): string {
  const town = String(submittedData.town ?? "").trim();
  const state = String(submittedData.state ?? "").trim();
  const inscription = String(
    submittedData.inscription_txt ??
      submittedData.inscriptionTxt ??
      "",
  ).trim();
  const location = [town, state].filter(Boolean).join(", ");
  if (location && inscription) return `${location} - "${inscription}"`;
  if (location) return location;
  if (inscription) return `"${inscription}"`;
  return `Submission #${fallbackId}`;
}

type DashboardTab = "submissions" | "editor";

interface DashboardItem {
  id: number;
  name: string;
  town: string;
  state: string;
  dateRange?: string;
  size?: string;
  shape?: string;
  color?: string;
  status: string;
  created_at: string;
  description?: string;
  image_url: string | null;
  marking_id?: number | null;
  cover_id?: number | null;
  /** True when this is an edit to an existing catalog entry. */
  isCatalogEdit?: boolean;
  /** True when this contribution is a cover (vs a marking); routes editing to CoverEdit. */
  isCover?: boolean;
  /** Parent marking id for a cover contribution; needed to build the CoverEdit route. */
  cover_parent_marking_id?: number | null;
}

/** Contribution row shown in the editor dashboard history list. */
interface EditorHistoryItem {
  id: number;
  contributor_username: string;
  display_name: string;
  state_display: string;
  town_display: string;
  shape_display: string;
  color_display: string;
  marking_id: number | null;
  cover_id: number | null;
  status: string;
  created_at: string;
  review_notes: string | null;
  image_url: string | null;
  isCover?: boolean;
  /** Issue #89 archive state; only populated on the archived (recycle bin) list. */
  is_archived?: boolean;
  archived_at?: string | null;
  archived_by_username?: string | null;
  archive_reason?: string;
}

type SortDir = "asc" | "desc";

type MySubmissionsSortField = "status" | "state" | "town" | "shape" | "color" | "submitted";
type EditorHistorySortField = "status" | "state" | "town" | "shape" | "color" | "submitted";

type SortEntry<F extends string> = { field: F; dir: SortDir };

function SortableLabel<F extends string>({
  htmlFor,
  label,
  field,
  currentSort,
  onToggle,
}: {
  htmlFor?: string;
  label: string;
  field: F;
  currentSort: SortEntry<F>[];
  onToggle: (field: F, dir: SortDir) => void;
}) {
  const entry = currentSort.find((e) => e.field === field) ?? null;
  const isAsc = entry?.dir === "asc";
  const isDesc = entry?.dir === "desc";
  return (
    <div className="group flex items-center gap-1">
      <Label htmlFor={htmlFor}>{label}</Label>
      <button
        type="button"
        aria-label={`Sort by ${label} ascending`}
        aria-pressed={isAsc}
        onClick={() => onToggle(field, "asc")}
        className={cn(
          "p-0.5 rounded hover:bg-muted transition-opacity",
          isAsc
            ? "text-foreground opacity-100"
            : "text-muted-foreground opacity-0 group-hover:opacity-100 focus:opacity-100",
        )}
      >
        <ArrowUp className="h-3 w-3" />
      </button>
      <button
        type="button"
        aria-label={`Sort by ${label} descending`}
        aria-pressed={isDesc}
        onClick={() => onToggle(field, "desc")}
        className={cn(
          "p-0.5 rounded hover:bg-muted transition-opacity",
          isDesc
            ? "text-foreground opacity-100"
            : "text-muted-foreground opacity-0 group-hover:opacity-100 focus:opacity-100",
        )}
      >
        <ArrowDown className="h-3 w-3" />
      </button>
    </div>
  );
}

/** Build compact page numbers for pagination (shared with Catalog Search) */
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

/** Placeholder when image is missing or fails to load. Matches Catalog Search. */
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

interface DashboardProps {
  initialTab?: DashboardTab;
}

const Dashboard = ({ initialTab = "submissions" }: DashboardProps) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { toast } = useToast();
  const user = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // Issue #87: the dashboard's view (tab, page, page size, sort, filters) is
  // backed by the URL so it survives the round trip to a detail screen, a
  // reload and the back button. Read once on mount -- after that this component
  // owns the state and writes it back out; re-reading would fight the user.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const initialParams = useMemo(() => parseDashboardParams(searchParams), []);

  const dashboardReturnState = () => ({
    fromDashboard: true,
    dashboardTab: activeTab,
  });

  // Concrete href back to the view currently on screen. Screens that take a
  // plain `from` string (CoverEdit, Contribute) navigate straight to it, so
  // handing them the query string is what preserves page size and filters (#87).
  const dashboardFrom = () => dashboardHref(searchParams.toString());

  // Resume a draft submission. Cover drafts edit through CoverEdit; marking drafts
  // through the Contribute form. A cover draft with no resolvable parent marking
  // falls back to the marking form rather than building a broken /record route.
  const goEditDraft = (s: DashboardItem) => {
    if (s.isCover && s.cover_parent_marking_id != null) {
      // Pass `from` so CoverEdit returns here (the dashboard) on save/back,
      // instead of dumping the user on the parent marking record.
      navigate(`/record/${s.cover_parent_marking_id}/cover/new?edit=${s.id}`, {
        state: { from: dashboardFrom() },
      });
      return;
    }
    navigate(`/contribute?edit=${s.id}`, { state: { from: dashboardFrom() } });
  };

  const goEditSubmission = (s: DashboardItem) => {
    const statusNorm = String(s.status || "").toLowerCase();
    const coverParentMarkingId = s.cover_parent_marking_id ?? s.marking_id ?? null;
    if (statusNorm === "pending" || statusNorm === "needs_revision" || statusNorm === "rejected") {
      if (s.isCover && coverParentMarkingId != null) {
        navigate(`/record/${coverParentMarkingId}/cover/new?edit=${s.id}`, {
          state: { from: dashboardFrom() },
        });
        return;
      }
      navigate(`/contribute?edit=${s.id}`, { state: { from: dashboardFrom() } });
      return;
    }

    if (s.isCover && coverParentMarkingId != null && s.cover_id != null) {
      // Approved cover submissions already have a materialized Cover row.
      // Edit that cover directly instead of sending an approved contribution id
      // through the draft/resubmission endpoint.
      navigate(`/record/${coverParentMarkingId}/cover/${s.cover_id}/edit`, {
        state: { from: dashboardFrom() },
      });
      return;
    }

    if (s.isCover && coverParentMarkingId != null) {
      // Returned cover contributions resume through CoverEdit with the
      // contribution id. Routing to /edit/:markingId would open the parent
      // marking editor instead.
      navigate(`/record/${coverParentMarkingId}/cover/new?edit=${s.id}`, {
        state: { from: dashboardFrom() },
      });
      return;
    }

    if (s.marking_id != null) {
      navigate(`/edit/${s.marking_id}`, {
        state: { from: dashboardFrom(), fromDashboard: true, fromDashboardDirect: true },
      });
    }
  };

  const canEditSubmission = (s: DashboardItem): boolean => {
    const statusNorm = String(s.status || "").toLowerCase();
    if (statusNorm === "draft") return true;
    if (statusNorm === "pending" || statusNorm === "needs_revision" || statusNorm === "rejected") {
      if (!s.isCover) return true;
      return (s.cover_parent_marking_id ?? s.marking_id ?? null) != null;
    }

    if (s.isCover) {
      const coverParentMarkingId = s.cover_parent_marking_id ?? s.marking_id ?? null;
      if (coverParentMarkingId == null) return false;
      return s.cover_id != null;
    }

    if (statusNorm === "approved" && s.marking_id != null) return true;
    return (isSuperuser || isEditor) && s.marking_id != null;
  };

  // Issue #89: clear a reviewed entry off the review queue without deleting it.
  // Only approved / rejected / needs-revision qualify -- a pending entry has to
  // be decided first, and the backend enforces that too.
  const canArchive = (item: EditorHistoryItem): boolean =>
    ["approved", "rejected", "needs_revision"].includes(
      String(item.status || "").toLowerCase(),
    );

  const handleArchiveConfirm = async () => {
    if (!archiveTarget || archiving) return;
    setArchiving(true);
    try {
      const res = await archiveContribution(archiveTarget.id, archiveReason.trim() || undefined);
      if (res.ok) {
        toast({
          title: "Entry archived",
          description: "It is in the Archived list and can be restored at any time.",
        });
        setArchiveTarget(null);
        setArchiveReason("");
        setSubmissionsRefetchKey((k) => k + 1);
      } else {
        toast({
          title: "Could not archive",
          description: "message" in res ? res.message : "Could not archive this entry.",
          variant: "destructive",
        });
      }
    } finally {
      setArchiving(false);
    }
  };

  // Restore a soft-removed catalog marking straight from the recycle-bin list.
  // Previously this needed a trip to each record's detail page (#89).
  const handleRestoreMarking = async (marking: MarkingRecord) => {
    if (restoringMarkingId != null) return;
    setRestoringMarkingId(marking.id);
    try {
      const res = await restoreMarking(marking.id);
      if (res.ok) {
        toast({ title: "Marking restored", description: "It is back in the catalog." });
        setSubmissionsRefetchKey((k) => k + 1);
      } else {
        toast({
          title: "Could not restore",
          description: "message" in res ? res.message : "Could not restore marking.",
          variant: "destructive",
        });
      }
    } finally {
      setRestoringMarkingId(null);
    }
  };

  const handleRestoreArchived = async (item: EditorHistoryItem) => {
    if (restoringId != null) return;
    setRestoringId(item.id);
    try {
      const res = await restoreContribution(item.id);
      if (res.ok) {
        toast({ title: "Entry restored", description: "It is back in the review queue." });
        setSubmissionsRefetchKey((k) => k + 1);
      } else {
        toast({
          title: "Could not restore",
          description: "message" in res ? res.message : "Could not restore this entry.",
          variant: "destructive",
        });
      }
    } finally {
      setRestoringId(null);
    }
  };

  const goOpenDashboardItem = (item: {
    id: number;
    status: string;
    marking_id?: number | null;
    cover_id?: number | null;
    isCover?: boolean;
  }) => {
    const statusNorm = String(item.status || "").toLowerCase();
    if (statusNorm === "draft") {
      goEditDraft(item as DashboardItem);
      return;
    }
    navigate(`/contribution/${item.id}`, { state: dashboardReturnState() });
  };
  // `?tab=` wins when present; otherwise fall back to the route's initialTab so
  // the Navigation menu's location-state links keep working unchanged.
  const [activeTab, setActiveTab] = useState<DashboardTab>(
    searchParams.has("tab") ? initialParams.tab : initialTab,
  );

  // When returning from contribution detail, switch to editor tab if requested
  useEffect(() => {
    const tab = (location.state as { tab?: DashboardTab } | null)?.tab;
    if (tab === "editor" || tab === "submissions") {
      setActiveTab(tab);
    }
  }, [location.state]);

  const [submissions, setSubmissions] = useState<DashboardItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [submissionsRefetchKey, setSubmissionsRefetchKey] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [currentPage, setCurrentPage] = useState(initialParams.submissions.page);
  const [goToPageInput, setGoToPageInput] = useState("");
  const [itemsPerPage, setItemsPerPage] = useState(initialParams.submissions.pageSize);

  // Editor tab: history of user contributions in assigned states, not full catalog.
  const [editorHistoryItems, setEditorHistoryItems] = useState<EditorHistoryItem[]>([]);
  // Recycle-bin markings shown when editorHistoryStatusFilter === "removed".
  // Kept separate from editorHistoryItems (contributions) because the rows are
  // markings and navigate to /record/:id instead of /contribution/:id.
  const [removedMarkings, setRemovedMarkings] = useState<MarkingRecord[]>([]);
  // Recycle-bin covers shown alongside removedMarkings when the filter is
  // "removed". Loaded once (first page) and navigate to /covers/:id to restore.
  const [removedCovers, setRemovedCovers] = useState<RecycleBinCover[]>([]);
  const [editorHistoryFetching, setEditorHistoryFetching] = useState(false);
  const [editorHistoryError, setEditorHistoryError] = useState<string | null>(null);
  // Issue #89: archiving a reviewed entry off the queue. `archiveTarget` doubles
  // as the dialog's open flag; `archivingId` disables the row being acted on.
  const [archiveTarget, setArchiveTarget] = useState<EditorHistoryItem | null>(null);
  const [archiveReason, setArchiveReason] = useState("");
  const [archiving, setArchiving] = useState(false);
  const [restoringId, setRestoringId] = useState<number | null>(null);
  const [restoringMarkingId, setRestoringMarkingId] = useState<number | null>(null);
  const [editorHistoryStatusFilter, setEditorHistoryStatusFilter] = useState(
    initialParams.editor.status,
  );
  const [editorStateFilter, setEditorStateFilter] = useState(initialParams.editor.state);
  const [editorSearchQuery, setEditorSearchQuery] = useState(initialParams.editor.q);
  const [editorTownFilter, setEditorTownFilter] = useState(initialParams.editor.town);
  const [editorShapeFilter, setEditorShapeFilter] = useState(initialParams.editor.shape);
  const [editorColorFilter, setEditorColorFilter] = useState(initialParams.editor.color);
  const [editorDateFrom, setEditorDateFrom] = useState(initialParams.editor.from);
  const [editorDateTo, setEditorDateTo] = useState(initialParams.editor.to);
  const [submissionQueueSort, setSubmissionQueueSort] = useState<SortEntry<EditorHistorySortField>[]>(
    initialParams.editor.sort,
  );
  const toggleEditorHistorySort = (field: EditorHistorySortField, dir: SortDir) => {
    // Single-column sort: clicking an arrow replaces the sort. Clicking the
    // already-active direction clears the sort (returns to API order). The
    // previous "stack" behavior left "submitted desc" pinned as the primary
    // key, so secondary fields never affected order because created_at is
    // unique per row.
    setSubmissionQueueSort((prev) => {
      const current = prev[0];
      if (current && current.field === field && current.dir === dir) return [];
      return [{ field, dir }];
    });
  };
  const [editorHistoryPage, setEditorHistoryPage] = useState(initialParams.editor.page);
  const [editorHistoryTotal, setEditorHistoryTotal] = useState<number | null>(null);
  const [editorHistoryGoToInput, setEditorHistoryGoToInput] = useState("");
  const [editorHistoryPageSize, setEditorHistoryPageSize] = useState(
    initialParams.editor.pageSize,
  );

  // Issue #109: the two free-text boxes are debounced, the selects are not --
  // a select is a single deliberate commit, so delaying it only adds latency.
  const debouncedEditorSearch = useDebounce(editorSearchQuery, EDITOR_DEBOUNCE_MS);
  const debouncedEditorTown = useDebounce(editorTownFilter, EDITOR_DEBOUNCE_MS);
  // The date boxes render as type="text" until focused, so free text reaches
  // this point. It used to be harmless -- `new Date("abc")` is Invalid Date and
  // every comparison against it is false, so a junk date silently filtered
  // nothing. Sent to the API's DateFilter it is an HTTP 400 and an error
  // banner over the whole queue, so only well-formed dates go on the wire.
  const editorFrom = ISO_DATE.test(editorDateFrom) ? editorDateFrom : "";
  const editorTo = ISO_DATE.test(editorDateTo) ? editorDateTo : "";
  // Guards against an older response landing after a newer one. Without it a
  // slow "farm" can overwrite the rows a later "farmville" already painted.
  const editorFetchSeq = useRef(0);
  // Loading vs refreshing, as Catalog Search does it. Every filter control is
  // disabled while loading; if that flag flipped on each debounced keystroke,
  // the search box would disable mid-word and drop focus.
  const editorHistoryLoading = editorHistoryFetching && editorHistoryItems.length === 0;
  const editorHistoryRefreshing = editorHistoryFetching && !editorHistoryLoading;

  // Assigned-collections header: clip to one line by default, expandable on
  // click. Overflow is measured against the truncated span so the "Show more"
  // toggle only appears when the text actually doesn't fit.
  const [assignedCollectionsExpanded, setAssignedCollectionsExpanded] = useState(false);
  const assignedCollectionsRef = useRef<HTMLSpanElement>(null);
  const [assignedCollectionsOverflowing, setAssignedCollectionsOverflowing] = useState(false);

  // Filter states (mirror Catalog Search)
  const [searchQuery, setSearchQuery] = useState(initialParams.submissions.q);
  const [statusFilter, setStatusFilter] = useState(initialParams.submissions.status);
  const [stateFilter, setStateFilter] = useState(initialParams.submissions.state);
  const [townFilter, setTownFilter] = useState(initialParams.submissions.town);
  const [shapeFilter, setShapeFilter] = useState(initialParams.submissions.shape);
  const [colorFilter, setColorFilter] = useState(initialParams.submissions.color);
  const [mySubmissionsSort, setMySubmissionsSort] = useState<SortEntry<MySubmissionsSortField>[]>(
    initialParams.submissions.sort,
  );
  const toggleMySubmissionsSort = (field: MySubmissionsSortField, dir: SortDir) => {
    // See toggleEditorHistorySort for the single-column rationale.
    setMySubmissionsSort((prev) => {
      const current = prev[0];
      if (current && current.field === field && current.dir === dir) return [];
      return [{ field, dir }];
    });
  };
  const [dateFrom, setDateFrom] = useState(initialParams.submissions.from);
  const [dateTo, setDateTo] = useState(initialParams.submissions.to);
  const dateFromInputRef = useRef<HTMLInputElement>(null);
  const dateToInputRef = useRef<HTMLInputElement>(null);
  const editorDateFromInputRef = useRef<HTMLInputElement>(null);
  const editorDateToInputRef = useRef<HTMLInputElement>(null);

  // Shared filter options (states, types, colors) - only states assigned to user
  const { colorOptions, shapeOptions, stateOptions, isLoading: isLoadingFilters, error: filterError } =
    useFilterOptions({ assignedStatesOnly: true, shapeValues: "name" });

  // Disable filters while submissions or filter options are loading
  const filtersDisabled = loading || isLoadingFilters;
  const isEditor =
    user?.role === "editor" || user?.role === "administrator" || !!user?.is_superuser;
  const isSuperuser = !!user?.is_superuser;

  // Contributors should always see submissions directly (no tab switching).
  useEffect(() => {
    if (!isEditor && activeTab !== "submissions") {
      setActiveTab("submissions");
    }
  }, [isEditor, activeTab]);

  // Prevent duplicate fetches during rapid re-renders / user rehydration.
  const submissionsInFlightKey = useRef<string | null>(null);
  // Fetch current user's contributions for "My Submissions".
  useEffect(() => {
    if (!user) {
      setSubmissions([]);
      setLoading(false);
      return;
    }

    const fetchKey = `${user.id}:${submissionsRefetchKey}`;
    if (submissionsInFlightKey.current === fetchKey) return;
    submissionsInFlightKey.current = fetchKey;

    const fetchSubmissions = async () => {
      setLoading(true);
      try {
        // Fetch all contributor-owned contributions so submissions and edit
        // drafts appear in one canonical My Submissions list.
        // rawItems carry dynamic camelCase-or-snake_case display fields the mapper reads positionally.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const list = (await listContributions()).rawItems as any[];
        if (!list.length) {
          setSubmissions([]);
          return;
        }
        const mapped: DashboardItem[] = list.map((c) => {
          const submittedData =
            c.submittedData && typeof c.submittedData === "object"
              ? c.submittedData
              : c.submitted_data && typeof c.submitted_data === "object"
                ? c.submitted_data
                : {};
          const state = (c.stateDisplay || c.state_display || submittedData.state || "").trim();
          const town = (c.townDisplay || c.town_display || submittedData.town || "").trim();

          const imageUrl = resolveSubmissionImageUrl(c, submittedData);

          // Cover contributions edit through CoverEdit (/record/:markingId/cover/new),
          // not the marking Contribute form. Detect by submitted_data and capture
          // the parent marking id needed to build that route.
          const sd = submittedData as Record<string, unknown>;
          const isCover = isCoverContributionData(sd);
          const coverParentMarkingId = isCover ? parentMarkingIdFromContribution(sd) : null;
          const coverId =
            typeof c.cover_id === "number"
              ? c.cover_id
              : typeof c.coverId === "number"
                ? c.coverId
                : materializedCoverIdFromContribution(sd);
          const displayName = isCover
            ? String(c.display_name || c.displayName || "").trim() || `Cover submission #${c.id}`
            : contributionTitleFromSubmittedData(submittedData, c.id);

          const dateRange =
            c.dateRange ||
            c.date_range ||
            submittedData.date_range ||
            submittedData.dateRange ||
            submittedData.first_seen ||
            (submittedData.firstSeen
              ? submittedData.lastSeen
                ? `${submittedData.firstSeen}-${submittedData.lastSeen}`
                : String(submittedData.firstSeen)
              : "");

          const markingId =
            typeof c.marking_id === "number"
              ? c.marking_id
              : typeof c.markingId === "number"
                ? c.markingId
                : typeof c.marking?.id === "number"
                  ? c.marking.id
                  : null;
          const isCatalogEdit = !!(markingId || submittedData.original_marking_id || submittedData.originalMarkingId);

          return {
            id: c.id,
            name: displayName,
            town,
            state,
            dateRange,
            size:
              c.sizeDisplay ||
              c.size ||
              formatSizeFromSubmittedData(submittedData as Record<string, unknown> | undefined) ||
              (submittedData as { dimensions?: string } | undefined)?.dimensions ||
              "",
            shape: c.shapeName || c.shapeDisplay || c.typeDisplay || c.shape || c.type || submittedData.shape || submittedData.type || "",
            color: c.colorDisplay || c.color || submittedData.color || "",
            status: String(c.status || "pending"),
            created_at: String(c.createdAt || c.created_at || ""),
            description: c.description || submittedData.description || "",
            image_url: imageUrl,
            marking_id: markingId ?? null,
            cover_id: coverId,
            isCatalogEdit,
            isCover,
            cover_parent_marking_id: coverParentMarkingId,
          } as DashboardItem;
        });
        setSubmissions(mapped);
      } catch (error: unknown) {
        toast({
          title: "Error loading submissions",
          description: error instanceof Error ? error.message : "Could not load submissions",
          variant: "destructive",
        });
      } finally {
        setLoading(false);
        if (submissionsInFlightKey.current === fetchKey) {
          submissionsInFlightKey.current = null;
        }
      }
    };

    fetchSubmissions();
  }, [user, toast, submissionsRefetchKey]);

  // Refetch My Submissions when user returns to the tab so status updates are visible
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible" && user && location.pathname === "/dashboard") {
        setSubmissionsRefetchKey((k) => k + 1);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [user, location.pathname]);

  // Load editor history for assigned states.
  useEffect(() => {
    if (!isEditor || activeTab !== "editor") return;
    setEditorHistoryError(null);
    setEditorHistoryFetching(true);
    // "Removed" swaps the data source to the recycle bin (markings), not the
    // contribution list. The endpoint is region-scoped server side, so the
    // state filter is not sent here.
    if (editorHistoryStatusFilter === "removed") {
      // Removed covers are loaded separately (first 50, no pagination); they
      // navigate to /covers/:id where the Restore button lives. A failure here
      // surfaces via the shared editor history error banner.
      getRecycleBinCovers(1, 50)
        .then((result) => setRemovedCovers(result.results))
        .catch((err) => {
          setEditorHistoryError(err instanceof Error ? err.message : "Could not load recycle bin.");
          setRemovedCovers([]);
        });
      getRecycleBinMarkings(editorHistoryPage, editorHistoryPageSize)
        .then((result) => {
          setRemovedMarkings(result.results);
          setEditorHistoryTotal(result.count);
        })
        .catch((err) => {
          setEditorHistoryError(err instanceof Error ? err.message : "Could not load recycle bin.");
          setRemovedMarkings([]);
          setEditorHistoryTotal(null);
        })
        .finally(() => setEditorHistoryFetching(false));
      return;
    }
    setRemovedCovers([]);
    const historyStatus =
      editorHistoryStatusFilter !== "all" &&
      ["pending", "approved", "rejected", "needs_revision"].includes(editorHistoryStatusFilter)
        ? editorHistoryStatusFilter
        : undefined;
    // "Archived" (Issue #89) is the same contribution list under a different
    // lens, so it reuses this whole mapping path rather than a parallel branch
    // the way "removed" (markings) has to.
    const isArchivedView = editorHistoryStatusFilter === "archived";
    const seq = ++editorFetchSeq.current;
    listContributions({
      mode: isArchivedView ? "archived" : "editor",
      status: historyStatus,
      state: editorStateFilter !== "all" ? editorStateFilter : undefined,
      // Issue #109: every one of these used to be applied to the fetched page
      // instead of being sent, so a search across 2,440 rows only ever looked
      // at the ~100 in front of it.
      q: debouncedEditorSearch.trim() || undefined,
      town: debouncedEditorTown.trim() || undefined,
      shape: editorShapeFilter !== "all" ? editorShapeFilter : undefined,
      color: editorColorFilter !== "all" ? editorColorFilter : undefined,
      submittedFrom: editorFrom || undefined,
      submittedTo: editorTo || undefined,
      ordering: editorOrderingParam(submissionQueueSort),
      page: editorHistoryPage,
      pageSize: editorHistoryPageSize,
    })
      .then(({ rawItems, count }) => {
        if (seq !== editorFetchSeq.current) return;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const list = rawItems as any[];
        setEditorHistoryTotal(count);
        const mapped = list.map((c) => {
          const submittedData = (c as { submitted_data?: Record<string, unknown>; submittedData?: Record<string, unknown> }).submitted_data
            ?? (c as { submittedData?: Record<string, unknown> }).submittedData
            ?? {};
          const sd = submittedData as Record<string, unknown>;
          const isCover = isCoverContributionData(sd);
          return {
            id: c.id,
            contributor_username: c.contributor_username ?? (c as { contributorUsername?: string }).contributorUsername ?? "",
            display_name: String((c as { displayName?: string }).displayName ?? (c as { display_name?: string }).display_name ?? "").trim(),
            state_display: c.state_display ?? (c as { stateDisplay?: string }).stateDisplay ?? "",
            town_display: c.town_display ?? (c as { townDisplay?: string }).townDisplay ?? "",
            shape_display:
              c.shape_display ??
              (c as { shapeDisplay?: string }).shapeDisplay ??
              c.type_display ??
              (c as { typeDisplay?: string }).typeDisplay ??
              "",
            color_display: String(
              c.color_display
                ?? (c as { colorDisplay?: string }).colorDisplay
                ?? c.color
                ?? (submittedData as { color?: string }).color
                ?? "",
            ),
            marking_id: c.marking_id ?? (c as { markingId?: number | null }).markingId ?? null,
            cover_id:
              c.cover_id ??
              (c as { coverId?: number | null }).coverId ??
              materializedCoverIdFromContribution(sd),
            isCover,
            status: String(c.status ?? "pending"),
            created_at: String(c.created_at ?? (c as { createdAt?: string }).createdAt ?? ""),
            review_notes: c.review_notes ?? (c as { reviewNotes?: string | null }).reviewNotes ?? null,
            image_url: resolveSubmissionImageUrl(c as Record<string, unknown>, submittedData as Record<string, unknown>),
            is_archived: Boolean(c.is_archived ?? (c as { isArchived?: boolean }).isArchived),
            archived_at:
              c.archived_at ?? (c as { archivedAt?: string | null }).archivedAt ?? null,
            archived_by_username:
              c.archived_by_username ??
              (c as { archivedByUsername?: string | null }).archivedByUsername ??
              null,
            archive_reason: String(
              c.archive_reason ?? (c as { archiveReason?: string }).archiveReason ?? "",
            ),
          };
        });
        // Render exactly what the server returned. Three post-fetch mutations
        // used to live here and all three broke the page contract (#109):
        //   - a draft filter, now `exclude(status=draft)` in get_queryset, so
        //     `count` and the rows finally describe the same set;
        //   - a defensive re-filter for needs_revision, guarding against "an
        //     older backend" that is this one, and silently hiding rows if it
        //     ever fired;
        //   - a per-page dedupe by marking_id:cover_id ending in a re-sort by
        //     created_at. The dedupe never engaged (a Contribution only gets a
        //     marking on approval, so a queued row has none) and the re-sort
        //     silently overrode whatever ?ordering= asked for -- the same
        //     regression Catalog Search records having already fixed once.
        setEditorHistoryItems(mapped);
      })
      .catch((err) => {
        if (seq !== editorFetchSeq.current) return;
        setEditorHistoryError(err instanceof Error ? err.message : "Could not load history.");
        setEditorHistoryItems([]);
        setEditorHistoryTotal(null);
      })
      .finally(() => {
        if (seq === editorFetchSeq.current) setEditorHistoryFetching(false);
      });
  }, [
    isEditor,
    activeTab,
    editorHistoryStatusFilter,
    editorStateFilter,
    // Issue #109: the omission of everything below this line WAS the defect --
    // these filters lived only in a useMemo over the page already fetched.
    debouncedEditorSearch,
    debouncedEditorTown,
    editorShapeFilter,
    editorColorFilter,
    editorFrom,
    editorTo,
    submissionQueueSort,
    editorHistoryPage,
    editorHistoryPageSize,
    submissionsRefetchKey,
  ]);

  // Reset editor pagination when changing history status filter or tab. Skipped
  // on the first run so a page restored from the URL survives mount (#87).
  const editorFiltersMounted = useRef(false);
  useEffect(() => {
    if (!isEditor || activeTab !== "editor") return;
    if (!editorFiltersMounted.current) {
      editorFiltersMounted.current = true;
      return;
    }
    setEditorHistoryPage(1);
  }, [
    isEditor,
    activeTab,
    editorHistoryStatusFilter,
    editorStateFilter,
    // The debounced/validated values, not the raw ones: otherwise the reset
    // fires on the keystroke and the fetch 800 ms later, so page 1 is
    // requested twice per search instead of once.
    debouncedEditorSearch,
    debouncedEditorTown,
    editorShapeFilter,
    // editorColorFilter was missing here. Harmless while filtering was
    // page-local; once it narrows the whole queue, changing colour on page 12
    // of a 1-page result set shows an empty list (#109).
    editorColorFilter,
    editorFrom,
    editorTo,
    submissionQueueSort,
    editorHistoryPageSize,
  ]);

  // Apply filters (mirror Catalog Search semantics on client side)
  const filteredSubmissions = useMemo(() => {
    return submissions.filter((submission) => {
      // Text search (name + description, mirroring Catalog Search)
      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase();
        const nameMatch = submission.name != null && String(submission.name).toLowerCase().includes(q);
        const descriptionMatch =
          submission.description != null &&
          String(submission.description).toLowerCase().includes(q);
        if (!nameMatch && !descriptionMatch) return false;
      }

      // Status filter (API uses "needs_revision"; filter value matches)
      if (statusFilter !== "all") {
        const statusNorm = String(submission.status || "").toLowerCase();
        const filterNorm = statusFilter.toLowerCase();
        if (statusNorm !== filterNorm) return false;
      }

      // State filter
      if (stateFilter !== "all" && submission.state !== stateFilter) return false;

      // Town filter
      if (townFilter.trim()) {
        const tq = townFilter.trim().toLowerCase();
        if (!submission.town || !submission.town.toLowerCase().includes(tq)) return false;
      }

      // Shape filter
      if (shapeFilter !== "all" && submission.shape !== shapeFilter) return false;

      // Color filter
      if (colorFilter !== "all" && submission.color !== colorFilter) return false;

      // Submission created date range filter
      if (dateFrom && new Date(submission.created_at) < new Date(dateFrom)) return false;
      if (dateTo && new Date(submission.created_at) > new Date(dateTo)) return false;

      return true;
    });
  }, [
    submissions,
    searchQuery,
    statusFilter,
    stateFilter,
    townFilter,
    shapeFilter,
    colorFilter,
    dateFrom,
    dateTo,
  ]);

  const filteredAndSortedSubmissions = useMemo(() => {
    const sorted = [...filteredSubmissions];
    const valueFor = (item: DashboardItem, field: MySubmissionsSortField): string | number => {
      switch (field) {
        case "status":
          return String(item.status || "").toLowerCase();
        case "state":
          return String(item.state || "").toLowerCase();
        case "town":
          return String(item.town || "").toLowerCase();
        case "shape":
          return String(item.shape || "").toLowerCase();
        case "color":
          return String(item.color || "").toLowerCase();
        case "submitted":
          return new Date(item.created_at).getTime();
      }
    };
    sorted.sort((a, b) => {
      for (const entry of mySubmissionsSort) {
        const av = valueFor(a, entry.field);
        const bv = valueFor(b, entry.field);
        if (av < bv) return entry.dir === "asc" ? -1 : 1;
        if (av > bv) return entry.dir === "asc" ? 1 : -1;
      }
      return 0;
    });
    return sorted;
  }, [filteredSubmissions, mySubmissionsSort]);

  const effectiveTotalCount = filteredAndSortedSubmissions.length;

  const computeDateBounds = (items: { created_at: string }[]) => {
    if (items.length === 0) return { earliest: "", latest: "" };
    let minTs = Infinity;
    let maxTs = -Infinity;
    for (const s of items) {
      const t = new Date(s.created_at).getTime();
      if (!Number.isFinite(t)) continue;
      if (t < minTs) minTs = t;
      if (t > maxTs) maxTs = t;
    }
    const fmt = (ts: number) => {
      if (!Number.isFinite(ts)) return "";
      const d = new Date(ts);
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");
      const yyyy = d.getFullYear();
      return mm + "/" + dd + "/" + yyyy;
    };
    return { earliest: fmt(minTs), latest: fmt(maxTs) };
  };

  const submissionDateBounds = useMemo(() => computeDateBounds(submissions), [submissions]);
  const editorSubmissionDateBounds = useMemo(
    () => computeDateBounds(editorHistoryItems),
    [editorHistoryItems],
  );

  const totalPages = Math.max(1, Math.ceil(effectiveTotalCount / itemsPerPage));

  let paginatedSubmissions: DashboardItem[] = [];
  let pageStart = 0;
  let pageEnd = 0;

  if (effectiveTotalCount === 0) {
    paginatedSubmissions = [];
    pageStart = 0;
    pageEnd = 0;
  } else {
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = Math.min(startIndex + itemsPerPage, filteredAndSortedSubmissions.length);
    paginatedSubmissions = filteredAndSortedSubmissions.slice(startIndex, endIndex);
    pageStart = startIndex + 1;
    pageEnd = endIndex;
  }

  const getStatusBadge = (status: string) => {
    switch (String(status || "").toLowerCase()) {
      case "draft":
        return (
          <Badge className="rounded-full border border-amber-900 bg-amber-800 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-amber-800">
            Draft
          </Badge>
        );
      case "approved":
        return (
          <Badge className="rounded-full border border-green-700 bg-green-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-green-600">
            Approved
          </Badge>
        );
      case "rejected":
        return (
          <Badge className="rounded-full border border-red-700 bg-red-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-red-600">
            Rejected
          </Badge>
        );
      case "needs_revision":
      case "revision":
        return (
          <Badge className="rounded-full border border-orange-600 bg-orange-500 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-orange-500">
            Needs Revision
          </Badge>
        );
      default:
        return (
          <Badge className="rounded-full border border-yellow-600 bg-yellow-500 px-3 py-1 text-xs font-semibold text-black shadow-sm hover:bg-yellow-500">
            Pending
          </Badge>
        );
    }
  };

  // Reset submissions pagination when filters or page size change. Skipped on
  // the first run so a page restored from the URL is not immediately clobbered
  // back to 1 (Issue #87).
  const submissionsFiltersMounted = useRef(false);
  useEffect(() => {
    if (!submissionsFiltersMounted.current) {
      submissionsFiltersMounted.current = true;
      return;
    }
    setCurrentPage(1);
  }, [searchQuery, statusFilter, stateFilter, townFilter, shapeFilter, colorFilter, mySubmissionsSort, dateFrom, dateTo, itemsPerPage]);

  // Issue #87: mirror the whole dashboard view into the URL, and remember it so
  // detail screens several navigations away can send the editor back here.
  useEffect(() => {
    const next: DashboardParams = {
      tab: activeTab,
      submissions: {
        q: searchQuery,
        status: statusFilter,
        state: stateFilter,
        town: townFilter,
        shape: shapeFilter,
        color: colorFilter,
        from: dateFrom,
        to: dateTo,
        sort: mySubmissionsSort,
        page: currentPage,
        pageSize: itemsPerPage,
      },
      editor: {
        q: editorSearchQuery,
        status: editorHistoryStatusFilter,
        state: editorStateFilter,
        town: editorTownFilter,
        shape: editorShapeFilter,
        color: editorColorFilter,
        from: editorDateFrom,
        to: editorDateTo,
        sort: submissionQueueSort,
        page: editorHistoryPage,
        pageSize: editorHistoryPageSize,
      },
    };
    const params = buildDashboardParams(next);
    const serialized = params.toString();
    rememberDashboardLocation(serialized);
    if (serialized !== searchParams.toString()) {
      setSearchParams(serialized ? params : {}, { replace: true });
    }
  }, [
    activeTab,
    searchQuery, statusFilter, stateFilter, townFilter, shapeFilter, colorFilter,
    dateFrom, dateTo, mySubmissionsSort, currentPage, itemsPerPage,
    editorSearchQuery, editorHistoryStatusFilter, editorStateFilter, editorTownFilter,
    editorShapeFilter, editorColorFilter, editorDateFrom, editorDateTo,
    submissionQueueSort, editorHistoryPage, editorHistoryPageSize,
    searchParams, setSearchParams,
  ]);

  // Measure whether the assigned-collections line is truncated. When expanded
  // we always treat it as "overflowing" so the user can still collapse it.
  useEffect(() => {
    const el = assignedCollectionsRef.current;
    if (!el) {
      setAssignedCollectionsOverflowing(false);
      return;
    }
    if (assignedCollectionsExpanded) {
      setAssignedCollectionsOverflowing(true);
      return;
    }
    const check = () => {
      if (!assignedCollectionsRef.current) return;
      const e = assignedCollectionsRef.current;
      setAssignedCollectionsOverflowing(e.scrollWidth > e.clientWidth + 1);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [assignedCollectionsExpanded, user?.assigned_collections]);

  // The editor recycle bin for entries (Issue #89). Unlike "removed" (which
  // swaps to markings/covers), this is the same contribution list, so it renders
  // through the normal row path with archive metadata and a Restore action.
  const isArchivedView = editorHistoryStatusFilter === "archived";

  // In "removed" mode the rows on the page come from removedMarkings, not the
  // contribution list, so the page-end count must read that length instead.
  const editorHistoryRowsOnPage =
    editorHistoryStatusFilter === "removed" ? removedMarkings.length : editorHistoryItems.length;
  const editorHistoryTotalCount = editorHistoryTotal ?? editorHistoryRowsOnPage;
  const editorHistoryTotalPages = Math.max(1, Math.ceil(editorHistoryTotalCount / editorHistoryPageSize));
  const editorHistoryPageStart =
    editorHistoryTotalCount === 0 ? 0 : (editorHistoryPage - 1) * editorHistoryPageSize + 1;
  const editorHistoryPageEnd =
    editorHistoryTotalCount === 0
      ? 0
      : Math.min((editorHistoryPage - 1) * editorHistoryPageSize + editorHistoryRowsOnPage, editorHistoryTotalCount);

  // The client-side filter/sort memo that used to sit here is gone (#109).
  // It filtered `editorHistoryItems`, which holds ONE server page, so at 2,440
  // queued rows an editor searching "farm" got nothing on page 1 of 25 while
  // Farmville sat on page 16 -- and the count banner went on reporting the
  // server's unfiltered total, so the numbers disagreed with the rows. Every
  // one of those filters, and the sort, is now a query param. Render what the
  // server sent.

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />

      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div className="min-w-0 md:flex-1">
              <h1 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-2">
                {isEditor ? "Editor Dashboard" : "Contributor Dashboard"}
              </h1>
              <p className="text-muted-foreground">
                {isEditor && activeTab === "editor"
                  ? "Review pending submissions and see contribution history in your assigned states."
                  : "View and track your submissions."}
              </p>
              {isEditor && user?.assigned_collections && user.assigned_collections.length > 0 && (
                <div className="text-muted-foreground text-sm mt-1 flex items-baseline gap-2 min-w-0 max-w-full">
                  <span
                    ref={assignedCollectionsRef}
                    className={cn(
                      "min-w-0 flex-1",
                      !assignedCollectionsExpanded && "overflow-hidden text-ellipsis whitespace-nowrap",
                    )}
                  >
                    Role: {user.is_superuser ? "Administrator" : "Editor"} - Assigned Collections: {user.assigned_collections.map((c) => c.name).join(", ")}
                  </span>
                  {assignedCollectionsOverflowing && (
                    <button
                      type="button"
                      onClick={() => setAssignedCollectionsExpanded((v) => !v)}
                      className="shrink-0 text-primary underline hover:no-underline"
                      aria-expanded={assignedCollectionsExpanded}
                    >
                      {assignedCollectionsExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
            </div>

            {isEditor && (
              <div className="inline-flex rounded-md border border-border bg-card p-1">
                <Button
                  type="button"
                  variant={activeTab === "submissions" ? "secondary" : "ghost"}
                  size="sm"
                  className="rounded-r-none"
                  onClick={() => setActiveTab("submissions")}
                >
                  My Submissions
                </Button>
                <Button
                  type="button"
                  variant={activeTab === "editor" ? "secondary" : "ghost"}
                  size="sm"
                  className="rounded-l-none"
                  onClick={() => setActiveTab("editor")}
                >
                  User Submissions
                </Button>
              </div>
            )}
          </div>

          {activeTab === "submissions" && (
            <div className="flex flex-col lg:flex-row gap-6">
              <aside className={`lg:w-80 space-y-6 ${filtersOpen ? "block" : "hidden lg:block"}`}>
              <Card className="shadow-archival-md">
                <CardContent className="pt-6 space-y-4">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="font-heading text-lg font-semibold">Filters</h2>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="lg:hidden"
                      onClick={() => setFiltersOpen(false)}
                    >
                      Close
                    </Button>
                  </div>

                  <div className="space-y-2">
                    <Label>Search</Label>
                    <div className="relative">
                      <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                      <Input
                        type="search"
                        placeholder="Search across fields..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="pl-9 bg-background"
                        aria-label="Search submissions by name or description"
                        disabled={filtersDisabled}
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      label="Status"
                      field="status"
                      currentSort={mySubmissionsSort}
                      onToggle={toggleMySubmissionsSort}
                    />
                    <Select value={statusFilter} onValueChange={setStatusFilter} disabled={filtersDisabled}>
                      <SelectTrigger className="bg-background">
                        <SelectValue placeholder="All Statuses" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All Statuses</SelectItem>
                        <SelectItem value="draft">Draft</SelectItem>
                        <SelectItem value="pending">Pending</SelectItem>
                        <SelectItem value="approved">Approved</SelectItem>
                        <SelectItem value="rejected">Rejected</SelectItem>
                        <SelectItem value="needs_revision">Needs Revision</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="state"
                      label="State"
                      field="state"
                      currentSort={mySubmissionsSort}
                      onToggle={toggleMySubmissionsSort}
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
                      currentSort={mySubmissionsSort}
                      onToggle={toggleMySubmissionsSort}
                    />
                    <Input
                      id="town"
                      placeholder="Enter town name..."
                      value={townFilter}
                      onChange={(e) => setTownFilter(e.target.value)}
                      className="bg-background"
                      disabled={filtersDisabled}
                    />
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="shape"
                      label="Shape"
                      field="shape"
                      currentSort={mySubmissionsSort}
                      onToggle={toggleMySubmissionsSort}
                    />
                    <SearchableSelect
                      id="shape"
                      value={shapeFilter}
                      onValueChange={setShapeFilter}
                      placeholder="All Shapes"
                      allOption={{ value: "all", label: "All Shapes" }}
                      options={Array.isArray(shapeOptions) ? shapeOptions : []}
                      loading={isLoadingFilters}
                      error={!!filterError}
                      errorMessage="Failed to load types"
                      searchPlaceholder="Search types..."
                      emptyMessage="No type found."
                      aria-label="Filter by marking type"
                      disabled={filtersDisabled}
                    />
                  </div>

                  <div className="space-y-2">
                    <SortableLabel
                      htmlFor="color"
                      label="Color"
                      field="color"
                      currentSort={mySubmissionsSort}
                      onToggle={toggleMySubmissionsSort}
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

                  <div className="grid grid-cols-1 gap-4">
                    <div className="space-y-2">
                      <SortableLabel
                        label="Submission Date From"
                        field="submitted"
                        currentSort={mySubmissionsSort}
                        onToggle={toggleMySubmissionsSort}
                      />
                      <div className="relative">
                        <Input
                          ref={dateFromInputRef}
                          type={dateFrom ? "date" : "text"}
                          value={dateFrom}
                          placeholder={submissionDateBounds.earliest}
                          onFocus={(e) => {
                            e.currentTarget.type = "date";
                            e.currentTarget.showPicker?.();
                          }}
                          onBlur={(e) => {
                            if (!e.currentTarget.value) e.currentTarget.type = "text";
                          }}
                          onChange={(e) => setDateFrom(e.target.value)}
                          className="bg-background pr-10 date-input-hide-native-icon"
                          disabled={filtersDisabled}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3 text-muted-foreground hover:bg-transparent hover:text-foreground"
                          onClick={() => {
                            const el = dateFromInputRef.current;
                            if (!el) return;
                            el.type = "date";
                            el.focus();
                            el.showPicker?.();
                          }}
                          disabled={filtersDisabled}
                          aria-label="Open date picker"
                        >
                          <Calendar className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <SortableLabel
                        label="Submission Date To"
                        field="submitted"
                        currentSort={mySubmissionsSort}
                        onToggle={toggleMySubmissionsSort}
                      />
                      <div className="relative">
                        <Input
                          ref={dateToInputRef}
                          type={dateTo ? "date" : "text"}
                          value={dateTo}
                          placeholder={submissionDateBounds.latest}
                          onFocus={(e) => {
                            e.currentTarget.type = "date";
                            e.currentTarget.showPicker?.();
                          }}
                          onBlur={(e) => {
                            if (!e.currentTarget.value) e.currentTarget.type = "text";
                          }}
                          onChange={(e) => setDateTo(e.target.value)}
                          className="bg-background pr-10 date-input-hide-native-icon"
                          disabled={filtersDisabled}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3 text-muted-foreground hover:bg-transparent hover:text-foreground"
                          onClick={() => {
                            const el = dateToInputRef.current;
                            if (!el) return;
                            el.type = "date";
                            el.focus();
                            el.showPicker?.();
                          }}
                          disabled={filtersDisabled}
                          aria-label="Open date picker"
                        >
                          <Calendar className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </div>

                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => {
                      setSearchQuery("");
                      setStatusFilter("pending");
                      setStateFilter("all");
                      setTownFilter("");
                      setShapeFilter("all");
                      setColorFilter("all");
                      setMySubmissionsSort([{ field: "submitted", dir: "desc" }]);
                      setDateFrom("");
                      setDateTo("");
                    }}
                    disabled={filtersDisabled}
                  >
                    Clear Filters
                  </Button>
                </CardContent>
              </Card>
              </aside>

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
                    {effectiveTotalCount === 0 ? (
                      "0 results"
                    ) : (
                      <>
                        Showing{" "}
                        <span className="font-semibold text-foreground">
                          {pageStart.toLocaleString()}-{pageEnd.toLocaleString()}
                        </span>{" "}
                        of{" "}
                        <span className="font-semibold text-foreground">
                          {effectiveTotalCount.toLocaleString()}
                        </span>{" "}
                        results
                      </>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => navigate("/contribute", { state: { from: dashboardFrom() } })}
                    className="shrink-0 bg-green-800 hover:bg-green-900 text-white"
                  >
                    <Plus className="mr-2 h-4 w-4" />
                    Submit New Marking
                  </Button>
                </div>
              </div>

              {/* Submissions List (backed by contributions; can manage linked catalog entry when present) */}
              {loading ? (
                 <div className="flex flex-col justify-center items-center gap-3 py-12 text-muted-foreground">
                 <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
                 <p className="text-muted-foreground">Loading submissions...</p>
               </div>
              ) : filteredAndSortedSubmissions.length === 0 ? (
                <Card className="flex-1 flex items-center justify-center min-h-[200px]">
                  <CardContent className="text-center">
                    <p className="text-muted-foreground">
                      {submissions.length === 0
                        ? "You haven't submitted anything yet."
                        : "No submissions found matching your filters."}
                    </p>
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-4">
                  {paginatedSubmissions.map((submission) => (
                    <Card
                      key={submission.id}
                      className="shadow-archival-md hover:shadow-archival-lg transition-shadow"
                    >
                      <CardContent className="p-6">
                        <div className="flex gap-6 md:flex-row flex-col">
                          <button
                            type="button"
                            onClick={() => goOpenDashboardItem(submission)}
                            className="md:w-32 md:h-32 w-full h-48 shrink-0 p-0 border-0 bg-transparent cursor-pointer rounded overflow-hidden focus:outline-none focus:ring-2 focus:ring-ring"
                            aria-label={`Open ${submission.name}`}
                          >
                            <ImageOrPlaceholder
                              src={submission.image_url}
                              alt={submission.name}
                              className="w-full h-full object-cover rounded border border-border hover:opacity-90 transition-opacity"
                            />
                          </button>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between gap-2 mb-2">
                              <div className="flex items-center gap-2 flex-wrap min-w-0">
                                <h3 className="font-heading text-xl font-semibold text-foreground">
                                  {submission.name}
                                </h3>
                              </div>
                              {getStatusBadge(submission.status)}
                            </div>

                            <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                            {submission.town && (
                                <div>
                                  <span className="text-muted-foreground">Town:</span>{" "}
                                  <span className="text-foreground">{submission.town}</span>
                                </div>
                              )}
                              {submission.state && (
                                <div>
                                  <span className="text-muted-foreground">State:</span>{" "}
                                  <span className="text-foreground">{submission.state}</span>
                                </div>
                              )}
                            </div>
                            
                            <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                              {submission.dateRange && (
                                <div>
                                  <span className="text-muted-foreground">Date Seen:</span>{" "}
                                  <span className="text-foreground">{submission.dateRange}</span>
                                </div>
                              )}
                              {submission.size && (
                                <div>
                                  <span className="text-muted-foreground">Size:</span>{" "}
                                  <span className="text-foreground">{submission.size}</span>
                                </div>
                              )}
                              {submission.color && (
                                <div>
                                  <span className="text-muted-foreground">Color:</span>{" "}
                                  <span className="text-foreground">{submission.color}</span>
                                </div>
                              )}
                              {String(submission.status || "").toLowerCase() !== "draft" && (
                                <div>
                                  <span className="text-muted-foreground">Submitted:</span>{" "}
                                  <span className="text-foreground">
                                    {new Date(submission.created_at).toLocaleDateString()}
                                  </span>
                                </div>
                              )}
                            </div>

                            {submission.description && (
                              <p className="text-sm text-muted-foreground line-clamp-2 mt-2">
                                {submission.description}
                              </p>
                            )}

                            <div className="mt-3 flex flex-wrap gap-2 justify-end">
                              {String(submission.status || "").toLowerCase() === "draft" && (
                                <Button
                                  variant="secondary"
                                  size="sm"
                                  className="font-medium"
                                  onClick={() => goEditDraft(submission)}
                                >
                                  <Pencil className="mr-1.5 h-4 w-4" />
                                  Edit Draft
                                </Button>
                              )}
                              {String(submission.status || "").toLowerCase() !== "draft" &&
                                canEditSubmission(submission) && (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => goEditSubmission(submission)}
                                  >
                                    Edit
                                  </Button>
                                )}
                            </div>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}

              {!loading && user && filteredAndSortedSubmissions.length > 0 && (
                <div className="mt-8 flex flex-col items-center gap-4">
                  {totalPages > 1 && (
                    <Pagination>
                      <PaginationContent>
                        <PaginationItem>
                          <PaginationPrevious
                            onClick={() => {
                              setCurrentPage((p) => Math.max(1, p - 1));
                              window.scrollTo({ top: 0, left: 0, behavior: "auto" });
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
                                  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                                }}
                                isActive={currentPage === p}
                                className="cursor-pointer"
                              >
                                {p}
                              </PaginationLink>
                            </PaginationItem>
                          ),
                        )}

                        <PaginationItem>
                          <PaginationNext
                            onClick={() => {
                              setCurrentPage((p) => Math.min(totalPages, p + 1));
                              window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                            }}
                            className={
                              currentPage === totalPages ? "pointer-events-none opacity-50" : "cursor-pointer"
                            }
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
                          setItemsPerPage(n);
                        }
                      }}
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
                                window.scrollTo({ top: 0, left: 0, behavior: "auto" });
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
                              window.scrollTo({ top: 0, left: 0, behavior: "auto" });
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
          )}

          {activeTab === "editor" && isEditor && (
            <div className="flex flex-col lg:flex-row gap-6">
              {/* Filters sidebar for contribution history. */}
              <aside className={`lg:w-80 space-y-6 ${filtersOpen ? "block" : "hidden lg:block"}`}>
                <Card className="shadow-archival-md">
                  <CardContent className="pt-6 space-y-4">
                    <div className="flex items-center justify-between mb-4">
                      <h2 className="font-heading text-lg font-semibold">Filters</h2>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="lg:hidden"
                        onClick={() => setFiltersOpen(false)}
                      >
                        Close
                      </Button>
                    </div>

                    <div className="space-y-2">
                      <Label>Search</Label>
                      <div className="relative">
                        <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                        <Input
                          type="search"
                          placeholder="Search across fields..."
                          value={editorSearchQuery}
                          onChange={(e) => setEditorSearchQuery(e.target.value)}
                          className="pl-9 bg-background"
                          aria-label="Search user submissions by name, town, state, shape, or contributor"
                          disabled={editorHistoryLoading}
                        />
                      </div>
                    </div>

                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="editor-history-status"
                        label="Status"
                        field="status"
                        currentSort={submissionQueueSort}
                        onToggle={toggleEditorHistorySort}
                      />
                      <Select
                        value={editorHistoryStatusFilter}
                        onValueChange={setEditorHistoryStatusFilter}
                        disabled={editorHistoryLoading}
                      >
                        <SelectTrigger id="editor-history-status" className="bg-background">
                          <SelectValue placeholder="All Statuses" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">All Statuses</SelectItem>
                          <SelectItem value="pending">Pending</SelectItem>
                          <SelectItem value="approved">Approved</SelectItem>
                          <SelectItem value="rejected">Rejected</SelectItem>
                          <SelectItem value="needs_revision">Needs Revision</SelectItem>
                          <SelectItem value="archived">Archived</SelectItem>
                          <SelectItem value="removed">Removed</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="editor-state-filter"
                        label="State"
                        field="state"
                        currentSort={submissionQueueSort}
                        onToggle={toggleEditorHistorySort}
                      />
                      <SearchableSelect
                        id="editor-state-filter"
                        value={editorStateFilter}
                        onValueChange={setEditorStateFilter}
                        placeholder="All States"
                        allOption={{ value: "all", label: "All States" }}
                        options={Array.isArray(stateOptions) ? stateOptions : []}
                        loading={isLoadingFilters}
                        error={!!filterError}
                        errorMessage="Failed to load states"
                        searchPlaceholder="Search states..."
                        emptyMessage="No state found."
                        aria-label="Filter editor data by state"
                        disabled={
                          editorHistoryLoading ||
                          isLoadingFilters ||
                          editorHistoryStatusFilter === "removed"
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="editor-town-filter"
                        label="Town"
                        field="town"
                        currentSort={submissionQueueSort}
                        onToggle={toggleEditorHistorySort}
                      />
                      <Input
                        id="editor-town-filter"
                        placeholder="Enter town name..."
                        value={editorTownFilter}
                        onChange={(e) => setEditorTownFilter(e.target.value)}
                        className="bg-background"
                        disabled={editorHistoryLoading}
                      />
                    </div>
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="editor-shape-filter"
                        label="Shape"
                        field="shape"
                        currentSort={submissionQueueSort}
                        onToggle={toggleEditorHistorySort}
                      />
                      <SearchableSelect
                        id="editor-shape-filter"
                        value={editorShapeFilter}
                        onValueChange={setEditorShapeFilter}
                        placeholder="All Shapes"
                        allOption={{ value: "all", label: "All Shapes" }}
                        options={Array.isArray(shapeOptions) ? shapeOptions : []}
                        loading={isLoadingFilters}
                        error={!!filterError}
                        errorMessage="Failed to load types"
                        searchPlaceholder="Search types..."
                        emptyMessage="No type found."
                        aria-label="Filter editor history by shape"
                        disabled={editorHistoryLoading || isLoadingFilters}
                      />
                    </div>
                    <div className="space-y-2">
                      <SortableLabel
                        htmlFor="editor-color-filter"
                        label="Color"
                        field="color"
                        currentSort={submissionQueueSort}
                        onToggle={toggleEditorHistorySort}
                      />
                      <SearchableSelect
                        id="editor-color-filter"
                        value={editorColorFilter}
                        onValueChange={setEditorColorFilter}
                        placeholder="All Colors"
                        allOption={{ value: "all", label: "All Colors" }}
                        options={Array.isArray(colorOptions) ? colorOptions : []}
                        loading={isLoadingFilters}
                        error={!!filterError}
                        errorMessage="Failed to load colors"
                        searchPlaceholder="Search colors..."
                        emptyMessage="No color found."
                        aria-label="Filter editor history by color"
                        disabled={editorHistoryLoading || isLoadingFilters}
                      />
                    </div>
                    <div className="grid grid-cols-1 gap-4">
                      <div className="space-y-2">
                        <SortableLabel
                          label="Submission Date From"
                          field="submitted"
                          currentSort={submissionQueueSort}
                          onToggle={toggleEditorHistorySort}
                        />
                        <div className="relative">
                          <Input
                            ref={editorDateFromInputRef}
                            type={editorDateFrom ? "date" : "text"}
                            value={editorDateFrom}
                            placeholder={editorSubmissionDateBounds.earliest}
                            onFocus={(e) => {
                              e.currentTarget.type = "date";
                              e.currentTarget.showPicker?.();
                            }}
                            onBlur={(e) => {
                              if (!e.currentTarget.value) e.currentTarget.type = "text";
                            }}
                            onChange={(e) => setEditorDateFrom(e.target.value)}
                            className="bg-background pr-10 date-input-hide-native-icon"
                            disabled={editorHistoryLoading}
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="absolute right-0 top-0 h-full px-3 text-muted-foreground hover:bg-transparent hover:text-foreground"
                            onClick={() => {
                              const el = editorDateFromInputRef.current;
                              if (!el) return;
                              el.type = "date";
                              el.focus();
                              el.showPicker?.();
                            }}
                            disabled={editorHistoryLoading}
                            aria-label="Open date picker"
                          >
                            <Calendar className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                      <div className="space-y-2">
                        <SortableLabel
                          label="Submission Date To"
                          field="submitted"
                          currentSort={submissionQueueSort}
                          onToggle={toggleEditorHistorySort}
                        />
                        <div className="relative">
                          <Input
                            ref={editorDateToInputRef}
                            type={editorDateTo ? "date" : "text"}
                            value={editorDateTo}
                            placeholder={editorSubmissionDateBounds.latest}
                            onFocus={(e) => {
                              e.currentTarget.type = "date";
                              e.currentTarget.showPicker?.();
                            }}
                            onBlur={(e) => {
                              if (!e.currentTarget.value) e.currentTarget.type = "text";
                            }}
                            onChange={(e) => setEditorDateTo(e.target.value)}
                            className="bg-background pr-10 date-input-hide-native-icon"
                            disabled={editorHistoryLoading}
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="absolute right-0 top-0 h-full px-3 text-muted-foreground hover:bg-transparent hover:text-foreground"
                            onClick={() => {
                              const el = editorDateToInputRef.current;
                              if (!el) return;
                              el.type = "date";
                              el.focus();
                              el.showPicker?.();
                            }}
                            disabled={editorHistoryLoading}
                            aria-label="Open date picker"
                          >
                            <Calendar className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    </div>

                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={() => {
                        setSubmissionQueueSort([{ field: "submitted", dir: "desc" }]);
                        setEditorSearchQuery("");
                        setEditorHistoryStatusFilter("all");
                        setEditorStateFilter("all");
                        setEditorTownFilter("");
                        setEditorShapeFilter("all");
                        setEditorColorFilter("all");
                        setEditorDateFrom("");
                        setEditorDateTo("");
                      }}
                      disabled={editorHistoryLoading}
                    >
                      Clear Filters
                    </Button>
                  </CardContent>
                </Card>
              </aside>

              <div className="flex-1 flex flex-col gap-6">
              <main className="flex-1 space-y-4">
                {/* Contribution history in assigned states. */}
                <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-card p-4 rounded-lg border border-border shadow-archival-sm">
                  <div>
                    <p className="text-sm text-muted-foreground">
                      {editorHistoryLoading ? (
                        "Loading history..."
                      ) : (
                        <>
                          Showing{" "}
                          <span className="font-semibold text-foreground">
                            {editorHistoryTotalCount === 0
                              ? "0"
                              : `${editorHistoryPageStart.toLocaleString()}-${editorHistoryPageEnd.toLocaleString()}`}
                          </span>{" "}
                          of{" "}
                          <span className="font-semibold text-foreground">
                            {editorHistoryTotalCount.toLocaleString()}
                          </span>{" "}
                          result{editorHistoryTotalCount !== 1 ? "s" : ""} in history
                          {editorHistoryRefreshing && (
                            // Filtering now costs a round trip, so say so --
                            // without this the rows just sit there looking stale.
                            <Loader2
                              className="inline-block h-3 w-3 ml-2 animate-spin align-[-1px]"
                              aria-label="Updating results"
                            />
                          )}
                        </>
                      )}
                    </p>
                    {editorHistoryError && (
                      <p className="text-xs text-destructive mt-1">{editorHistoryError}</p>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="lg:hidden"
                    onClick={() => setFiltersOpen((open) => !open)}
                  >
                    <SlidersHorizontal className="h-4 w-4 mr-2" />
                    Filters
                  </Button>
                </div>

                {editorHistoryLoading ? (
                  <div className="flex flex-col justify-center items-center gap-3 py-12 text-muted-foreground">
                    <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
                    <p>Loading history...</p>
                  </div>
                ) : editorHistoryStatusFilter === "removed" ? (
                  <div className="space-y-8">
                    <div className="space-y-3">
                      <h3 className="text-sm font-semibold text-foreground">Removed Markings</h3>
                      {removedMarkings.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          No removed markings found.
                        </p>
                      ) : (
                        <ul className="space-y-3">
                          {removedMarkings.map((m) => {
                            const title = [m.town, m.stateAbbrev || m.state].filter(Boolean).join(", ");
                            const shapeStr = (m.shapeName || "").trim();
                            const displayLabel =
                              [title, shapeStr]
                                .filter((x) => x && String(x).trim().toLowerCase() !== "unknown")
                                .join(" - ") ||
                              title ||
                              `Marking #${m.id}`;
                            return (
                              <li
                                key={m.id}
                                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-4 bg-card hover:shadow-archival-sm transition-shadow"
                              >
                                <div className="flex items-center gap-4 min-w-0 flex-1">
                                  <button
                                    type="button"
                                    onClick={() =>
                                      navigate(`/record/${m.id}`, { state: dashboardReturnState() })
                                    }
                                    className="w-16 h-16 shrink-0 p-0 border-0 bg-transparent cursor-pointer rounded overflow-hidden focus:outline-none focus:ring-2 focus:ring-ring"
                                    aria-label={`Open ${displayLabel}`}
                                  >
                                    <ImageOrPlaceholder
                                      src={m.mainImage?.imageUrl ?? null}
                                      alt={displayLabel}
                                      className="w-full h-full object-cover rounded border border-border hover:opacity-90 transition-opacity"
                                    />
                                  </button>
                                  <div className="min-w-0">
                                    <span className="font-medium text-foreground block truncate">
                                      {displayLabel}
                                    </span>
                                    <span className="text-xs text-muted-foreground block truncate">
                                      Removed
                                      {m.removedByUsername ? ` by ${m.removedByUsername}` : ""}
                                      {m.removedAt
                                        ? ` on ${new Date(m.removedAt).toLocaleDateString()}`
                                        : ""}
                                      {m.removalReason ? ` - ${m.removalReason}` : ""}
                                    </span>
                                  </div>
                                </div>
                                <div className="flex flex-wrap items-center gap-2 shrink-0">
                                  <Badge className="rounded-full px-3 py-1 text-xs font-semibold shadow-sm bg-muted text-muted-foreground hover:bg-muted">
                                    Removed
                                  </Badge>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => void handleRestoreMarking(m)}
                                    disabled={restoringMarkingId === m.id}
                                  >
                                    <ArchiveRestore className="mr-2 h-4 w-4" />
                                    {restoringMarkingId === m.id ? "Restoring..." : "Restore"}
                                  </Button>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                    <div className="space-y-3">
                      <h3 className="text-sm font-semibold text-foreground">Removed Covers</h3>
                      {removedCovers.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          No removed covers found.
                        </p>
                      ) : (
                        <ul className="space-y-3">
                          {removedCovers.map((c) => {
                            const coverLabel = c.code ?? `Cover #${c.id}`;
                            const coverMeta = [
                              c.colorName,
                              c.type === "FC"
                                ? "Folded Cover"
                                : c.type === "FL"
                                  ? "Folded Letter"
                                  : "",
                            ]
                              .filter(Boolean)
                              .join(" - ");
                            return (
                              <li
                                key={c.id}
                                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-4 bg-card hover:shadow-archival-sm transition-shadow"
                              >
                                <button
                                  type="button"
                                  onClick={() =>
                                    navigate(`/covers/${c.id}`, { state: dashboardReturnState() })
                                  }
                                  className="min-w-0 flex-1 text-left p-0 border-0 bg-transparent cursor-pointer rounded focus:outline-none focus:ring-2 focus:ring-ring"
                                  aria-label={`Open ${coverLabel}`}
                                >
                                  <span className="font-medium text-foreground block truncate">
                                    {coverLabel}
                                  </span>
                                  {coverMeta && (
                                    <span className="text-xs text-muted-foreground block truncate">
                                      {coverMeta}
                                    </span>
                                  )}
                                </button>
                                <div className="flex flex-wrap items-center gap-2 shrink-0">
                                  <Badge className="rounded-full px-3 py-1 text-xs font-semibold shadow-sm bg-muted text-muted-foreground hover:bg-muted">
                                    Removed
                                  </Badge>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </div>
                ) : editorHistoryItems.length === 0 ? (
                  <Card className="flex-1 flex items-center justify-center min-h-[200px]">
                    <CardContent className="text-center">
                      <p className="text-muted-foreground mb-1">
                        {isArchivedView
                          ? "Nothing archived yet."
                          : "No submissions in history for the selected status."}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {isArchivedView
                          ? "Archiving a reviewed entry clears it off this queue without deleting it."
                          : "User contributions in your assigned states will appear here."}
                      </p>
                    </CardContent>
                  </Card>
                ) : (
                  <ul className="space-y-3">
                    {editorHistoryItems.map((item) => {
                      const title = [item.town_display, item.state_display].filter(Boolean).join(", ");
                      const shapeStr = (item.shape_display || "").trim();
                      const fallbackName =
                        [title, shapeStr].filter((x) => x && String(x).trim().toLowerCase() !== "unknown").join(" -- ") ||
                        title ||
                        `Submission #${item.id}`;
                      const displayLabel = item.display_name || fallbackName;
                      const statusClassName =
                        item.status === "approved"
                          ? "bg-green-600 text-white hover:bg-green-600"
                          : item.status === "rejected"
                            ? "bg-red-600 text-white hover:bg-red-600"
                            : item.status === "needs_revision"
                              ? "bg-orange-500 text-white hover:bg-orange-500"
                              : "bg-yellow-500 text-black hover:bg-yellow-500";
                      return (
                        <li
                          key={item.id}
                          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-4 bg-card hover:shadow-archival-sm transition-shadow"
                        >
                          <div className="flex items-center gap-4 min-w-0 flex-1">
                            <button
                              type="button"
                              onClick={() => goOpenDashboardItem(item)}
                              className="w-16 h-16 shrink-0 p-0 border-0 bg-transparent cursor-pointer rounded overflow-hidden focus:outline-none focus:ring-2 focus:ring-ring"
                              aria-label={`Open ${displayLabel}`}
                            >
                              <ImageOrPlaceholder
                                src={item.image_url}
                                alt={displayLabel}
                                className="w-full h-full object-cover rounded border border-border hover:opacity-90 transition-opacity"
                              />
                            </button>
                            <div className="min-w-0">
                              <span className="font-medium text-foreground block truncate">
                                {displayLabel}
                              </span>
                              <span className="text-muted-foreground text-sm">
                                by {item.contributor_username}
                                {" - "}
                                {new Date(item.created_at).toLocaleDateString()}
                              </span>
                              {isArchivedView && (
                                <span className="text-xs text-muted-foreground block truncate">
                                  Archived
                                  {item.archived_by_username ? ` by ${item.archived_by_username}` : ""}
                                  {item.archived_at
                                    ? ` on ${new Date(item.archived_at).toLocaleDateString()}`
                                    : ""}
                                  {item.archive_reason ? ` - ${item.archive_reason}` : ""}
                                </span>
                              )}
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center gap-2 shrink-0">
                            <Badge className={`rounded-full px-3 py-1 text-xs font-semibold shadow-sm ${statusClassName}`}>
                              {item.status === "needs_revision"
                                ? "Needs Revision"
                                : item.status === "approved"
                                  ? "Approved"
                                  : item.status === "rejected"
                                    ? "Rejected"
                                    : "Pending"}
                            </Badge>
                            {isArchivedView ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void handleRestoreArchived(item)}
                                disabled={restoringId === item.id}
                              >
                                <ArchiveRestore className="mr-2 h-4 w-4" />
                                {restoringId === item.id ? "Restoring..." : "Restore"}
                              </Button>
                            ) : (
                              canArchive(item) && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => {
                                    setArchiveReason("");
                                    setArchiveTarget(item);
                                  }}
                                  aria-label={`Archive ${displayLabel}`}
                                >
                                  <Archive className="mr-2 h-4 w-4" />
                                  Archive
                                </Button>
                              )
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {!editorHistoryLoading && !editorHistoryError && editorHistoryTotalCount > 0 && (
                  <div className="mt-8 flex flex-col items-center gap-4">
                    {editorHistoryTotalPages > 1 && (
                      <Pagination>
                        <PaginationContent>
                          <PaginationItem>
                            <PaginationPrevious
                              onClick={() => {
                                setEditorHistoryPage((p) => Math.max(1, p - 1));
                                window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                              }}
                              className={editorHistoryPage === 1 ? "pointer-events-none opacity-50" : "cursor-pointer"}
                            />
                          </PaginationItem>

                          {getPaginationPages(editorHistoryPage, editorHistoryTotalPages).map((p, i) =>
                            p === "ellipsis" ? (
                              <PaginationItem key={`ellipsis-history-${i}`}>
                                <PaginationEllipsis />
                              </PaginationItem>
                            ) : (
                              <PaginationItem key={`history-${p}`}>
                                <PaginationLink
                                  onClick={() => {
                                    setEditorHistoryPage(p);
                                    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                                  }}
                                  isActive={editorHistoryPage === p}
                                  className="cursor-pointer"
                                >
                                  {p}
                                </PaginationLink>
                              </PaginationItem>
                            ),
                          )}

                          <PaginationItem>
                            <PaginationNext
                              onClick={() => {
                                setEditorHistoryPage((p) => Math.min(editorHistoryTotalPages, p + 1));
                                window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                              }}
                              className={
                                editorHistoryPage === editorHistoryTotalPages ? "pointer-events-none opacity-50" : "cursor-pointer"
                              }
                            />
                          </PaginationItem>
                        </PaginationContent>
                      </Pagination>
                    )}

                    <div className="flex items-center gap-2">
                      <span className="text-sm text-muted-foreground">Records shown</span>
                      <Select
                        value={String(editorHistoryPageSize)}
                        onValueChange={(v) => {
                          const n = parseInt(v, 10);
                          if (n === 10 || n === 25 || n === 50 || n === 100) {
                            setEditorHistoryPageSize(n);
                            setEditorHistoryPage(1);
                          }
                        }}
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
                      {editorHistoryTotalPages > 1 && (
                        <>
                          <span className="text-sm text-muted-foreground">Go to page</span>
                          <Input
                            type="number"
                            min={1}
                            max={editorHistoryTotalPages}
                            placeholder="Page"
                            value={editorHistoryGoToInput}
                            onChange={(e) => {
                              const raw = e.target.value;
                              if (raw === "") {
                                setEditorHistoryGoToInput("");
                                return;
                              }
                              const n = parseInt(raw, 10);
                              if (Number.isNaN(n)) return;
                              const clamped = Math.max(1, Math.min(editorHistoryTotalPages, n));
                              setEditorHistoryGoToInput(String(clamped));
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                const n = parseInt(editorHistoryGoToInput, 10);
                                if (!Number.isNaN(n)) {
                                  setEditorHistoryPage(Math.max(1, Math.min(editorHistoryTotalPages, n)));
                                  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                                  setEditorHistoryGoToInput("");
                                }
                              }
                            }}
                            className="h-9 w-16 text-center [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                            aria-label="Go to history page number"
                          />
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="h-9"
                            onClick={() => {
                              const n = parseInt(editorHistoryGoToInput, 10);
                              if (!Number.isNaN(n)) {
                                setEditorHistoryPage(Math.max(1, Math.min(editorHistoryTotalPages, n)));
                                window.scrollTo({ top: 0, left: 0, behavior: "auto" });
                                setEditorHistoryGoToInput("");
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
          )}
      </div>
      <Footer />
    </div>

    <Dialog
      open={archiveTarget != null}
      onOpenChange={(open) => {
        if (archiving) return;
        if (!open) setArchiveTarget(null);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Archive this entry</DialogTitle>
          <DialogDescription>
            This clears the entry off your review queue without deleting it. The
            submission, its status and your feedback are kept, the contributor
            still sees it on their own dashboard, and you can restore it from the
            Archived list at any time. Optionally record a reason.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          placeholder="Reason (optional) - e.g. duplicate, not needed, blatantly wrong"
          value={archiveReason}
          onChange={(e) => setArchiveReason(e.target.value)}
          disabled={archiving}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => setArchiveTarget(null)} disabled={archiving}>
            Cancel
          </Button>
          <Button onClick={() => void handleArchiveConfirm()} disabled={archiving}>
            {archiving ? "Archiving..." : "Archive Entry"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
  );
};

export default Dashboard;
