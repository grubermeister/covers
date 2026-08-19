import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowDown, ArrowLeft, ArrowUp, Crop, History, Info, Loader2, MessageSquare, Pencil, Plus, Recycle, Replace, Star, Stamp, Trash2 } from "lucide-react";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
  type CarouselApi,
} from "@/components/ui/carousel";
import imageNotAvailable from "@/assets/image-not-available.jpg";
import { ImageOrPlaceholder } from "@/components/ImageOrPlaceholder";
import { CropImageDialog } from "@/components/CropImageDialog";
import { formatDateSeen, formatDatesSeenList, markingTypeLabel } from "@/lib/catalogRecordDisplay";
import { dashboardHrefForTab } from "@/lib/dashboardParams";
import { buildMarkingFields } from "@/lib/markingFields";
import { formatRateValue } from "@/lib/rateDisplay";
import { isTrueCircleShapeName } from "@/lib/shapeDisplay";
import { MarkingFieldsDisplay } from "@/components/MarkingFieldsDisplay";
import {
  countyDisplay,
  getMarkingById,
  getMarkingsPage,
  moveTargetCandidates,
  getMarkingChangelog,
  loadAssociatedCoversForMarking,
  moveImageSubject,
  normalizeImageUrl,
  primaryRegions,
  regionsDisplay,
  removeMarking,
  reorderImages,
  restoreMarking,
  updateMarkingReviewed,
  type AssociatedCover,
  type AssociatedDateSeen,
  type MarkingChangelogEvent,
  type MarkingCitation,
  type MarkingCitationReferenceWork,
  type MarkingImage,
  type MarkingRecord,
  deleteImage,
} from "@/services/markings";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useToast } from "@/hooks/use-toast";
import { SUBMISSION_LABELS } from "@/labels/submission";
import { useAuth } from "@/hooks/useAuth";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { createCoverMarking, getCoverById } from "@/services/covers";
import { parseCoverIdInput } from "@/lib/recordLinking";
import { readVphcProvenance } from "@/lib/vphcProvenance";
import { VphcProvenanceCard } from "@/components/VphcProvenanceCard";

type GalleryImage = {
  imageUrl: string | null;
  originalFilename?: string;
  /**
   * Subject label shown in the upper-left tag stack: "Cover" when the image
   * is attached to an associated cover (subject_type=COVER), otherwise the
   * marking's type label (Townmark / Ratemark / Auxmark) since
   * subject_type=MARKING images belong to the marking itself.
   */
  subjectLabel: string;
  isDefault: boolean;
  isTracing: boolean;
  /**
   * Backing image_id (Image.image_id) used by the editor reorder controls
   * to call PATCH /api/v2/images/{id}/. null only on the synthetic
   * "image not available" placeholder slide.
   */
  imageId: number | null;
};

const EMPTY = "-";

function dimensionsDisplay(record: MarkingRecord): string {
  const w = record.width?.trim() ?? "";
  const h = record.height?.trim() ?? "";

  // True circle-family shapes display as diameter, not WxH. Must run BEFORE the
  // sizeDisplay branch because the API serializer always populates
  // size_display as "WxH" (see common/api/v2/serializers.py
  // get_size_display); deferring this check would surface "28x28 mm" for
  // circles instead of "28 mm diameter" and disagree with the Search card.
  if (!record.isManuscript && isTrueCircleShapeName(record.shapeName)) {
    const d = w || h;
    if (d) return `${d} mm diameter`;
    return "";
  }
  if (record.sizeDisplay && record.sizeDisplay.trim()) {
    return record.sizeDisplay.trim().includes("mm")
      ? record.sizeDisplay.trim()
      : `${record.sizeDisplay.trim()} mm`;
  }
  if (w && h) return `${w}x${h} mm`;
  if (w) return `${w} mm`;
  if (h) return `${h} mm`;
  return "";
}

function coverDimensionsDisplay(width: string | null, height: string | null): string {
  const w = (width ?? "").trim();
  const h = (height ?? "").trim();
  if (w && h) return `${w}x${h} mm`;
  if (w) return `${w} mm`;
  if (h) return `${h} mm`;
  return EMPTY;
}

function coverTypeLabel(t: string | null): string {
  if (t === "FC") return "Folded Cover";
  if (t === "FL") return "Folded Letter";
  return EMPTY;
}

function formatCoverDate(d: AssociatedDateSeen): string {
  return formatDateSeen(d.date, d.granularity, {
    dateYear: d.dateYear,
    dateMonth: d.dateMonth,
    dateDay: d.dateDay,
  }) || d.date || "";
}

function associatedCoverDatesDisplay(
  c: AssociatedCover["coverDetails"],
): string {
  if (!c || c.datesSeen.length === 0) return EMPTY;
  const parts = c.datesSeen.map(formatCoverDate).filter(Boolean);
  return parts.length > 0 ? parts.join(", ") : EMPTY;
}

/** Preview fields for an associated cover (matches Catalog Search card layout). */
function AssociatedCoverPreviewFields({ cover }: { cover: AssociatedCover }) {
  const c = cover.coverDetails;
  const typeText = coverTypeLabel(c?.type ?? null) || EMPTY;
  const dateText = associatedCoverDatesDisplay(c) || EMPTY;
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
      <div className="min-w-0">
        <span className="text-muted-foreground">Type:</span>{" "}
        <span className="text-foreground break-words">{typeText}</span>
      </div>
      <div className="min-w-0">
        <span className="text-muted-foreground">Date:</span>{" "}
        <span className="text-foreground break-words">{dateText}</span>
      </div>
      {cover.isBackstamp && (
        <div className="min-w-0">
          <span className="text-muted-foreground">Backstamp:</span>{" "}
          <span className="text-foreground break-words">Yes</span>
        </div>
      )}
      {c?.isInstitutional === true && (
        <div className="min-w-0">
          <span className="text-muted-foreground">Institutional Ownership:</span>{" "}
          <span className="text-foreground break-words">Yes</span>
        </div>
      )}
    </dl>
  );
}

/**
 * Title text for a citation entry. The optional reference-work `code`
 * (editor-assigned identifier like "ASCC-204") is shown as a separate
 * badge in the UI, so this function returns just the human-readable
 * title and leaves the code to the caller.
 */
function citationTitle(citation: MarkingCitation): string {
  const rw = citation.referenceWork;
  if (!rw) return "Reference work";
  const title = rw.title.trim();
  if (title) return title;
  const code = (rw.code ?? "").trim();
  return code || "Reference work";
}

/**
 * Build the "Author (Year)" subtitle that sits directly under the title.
 * Returns "" when neither field is populated; either alone is fine.
 */
function citationByline(rw: MarkingCitationReferenceWork | null): string {
  if (!rw) return "";
  const authorship = rw.authorship.trim();
  const year = rw.publicationYear != null ? String(rw.publicationYear) : "";
  if (authorship && year) return `${authorship} (${year})`;
  if (authorship) return authorship;
  if (year) return `(${year})`;
  return "";
}

/**
 * Format a server-side ISO timestamp (e.g. "2026-04-12T19:34:51.123Z") for
 * the Record History row. We render in the viewer's locale so timestamps
 * read naturally, with second precision since events can fire close together
 * during automated workflows. Falls back to the raw string if Date parsing
 * fails (e.g. a malformed payload) so editors still see *something*.
 */
function formatHistoryTimestamp(raw: string | null | undefined): string {
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Fallback when an event has no actor email (e.g. system-generated). */
function historyActorDisplay(event: MarkingChangelogEvent): string {
  const email = (event.actor_email ?? "").trim();
  if (email) return email;
  const actor = (event.actor ?? "").trim();
  if (actor) return actor;
  return "system";
}

function buildGalleryImages(record: MarkingRecord): GalleryImage[] {
  const typeLabel = markingTypeLabel(record.type) || "Marking";
  return record.images.map((img: MarkingImage) => ({
    imageUrl: normalizeImageUrl(img.imageUrl),
    originalFilename: img.originalFilename || undefined,
    subjectLabel: img.subjectType === "COVER" ? "Cover" : typeLabel,
    // display_order=0 is the canonical "default" slot -- matches the editor
    // tooling on ContributionDetail.tsx where displayOrder===0 is what gets
    // labeled "Default" / "Set default".
    isDefault: img.displayOrder === 0,
    isTracing: img.subjectType === "MARKING" && img.isTracing,
    imageId: img.imageId > 0 ? img.imageId : null,
  }));
}

function coverLinkReviewBadgeLabel(cover: AssociatedCover): string {
  if (cover.contributionDraftId != null) {
    const st = (cover.contributionStatus ?? "draft").toLowerCase();
    if (st === "draft") return "Draft";
    if (st === "needs_revision") return "Needs revision";
    if (st === "pending") return "Pending review";
    return st.charAt(0).toUpperCase() + st.slice(1);
  }
  switch (cover.reviewStatus) {
    case "pending":
      return "Pending review";
    case "needs_revision":
      return "Needs revision";
    case "rejected":
      return "Rejected";
    default:
      return "Approved";
  }
}

function associatedCoverShowsStatusBadge(cover: AssociatedCover): boolean {
  if (cover.contributionDraftId != null) return true;
  return cover.reviewStatus !== "approved";
}

const RecordDetail = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuth();
  const { toast } = useToast();
  const { id } = useParams();
  const [api, setApi] = useState<CarouselApi>();
  const [current, setCurrent] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [record, setRecord] = useState<MarkingRecord | null>(null);
  const [associatedCovers, setAssociatedCovers] = useState<AssociatedCover[]>([]);
  const [coversLoadError, setCoversLoadError] = useState<string | null>(null);
  const [historyEvents, setHistoryEvents] = useState<MarkingChangelogEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyExpanded, setHistoryExpanded] = useState(false);
  // Disables the editor's reorder buttons while a PATCH round-trip is in
  // flight. Without this an editor can fire two overlapping reorders before
  // the first one resolves, producing inconsistent display_order values.
  const [reorderingImages, setReorderingImages] = useState(false);
  // Remove/restore (recycle bin) controls -- only rendered when the backend
  // reports record.canRemove (superuser or responsible editor).
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeReason, setRemoveReason] = useState("");
  const [removing, setRemoving] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [linkCoverOpen, setLinkCoverOpen] = useState(false);
  const [linkCoverInput, setLinkCoverInput] = useState("");
  const [linkCoverIsBackstamp, setLinkCoverIsBackstamp] = useState(false);
  const [linkCoverBusy, setLinkCoverBusy] = useState(false);
  const [linkCoverError, setLinkCoverError] = useState<string | null>(null);
  const [moveImageDialogImg, setMoveImageDialogImg] = useState<MarkingImage | null>(null);
  // Image whose marking an editor is cropping out of a whole-cover scan (#77).
  const [cropImageTarget, setCropImageTarget] = useState<MarkingImage | null>(null);
  const [moveImageTargetCoverId, setMoveImageTargetCoverId] = useState("");
  const [moveImageView, setMoveImageView] = useState("FRONT");
  const [moveImageBusy, setMoveImageBusy] = useState(false);
  const [moveImageError, setMoveImageError] = useState<string | null>(null);
  // Move an image to another marking at the same town (#104 / C3): the second
  // half of the crop -> reattach workflow for scans that hold two devices.
  const [moveToMarkingImg, setMoveToMarkingImg] = useState<MarkingImage | null>(null);
  const [moveMarkingTargetId, setMoveMarkingTargetId] = useState("");
  const [moveMarkingView, setMoveMarkingView] = useState("FULL");
  const [moveMarkingCandidates, setMoveMarkingCandidates] = useState<MarkingRecord[]>([]);
  // Own busy/error pair rather than sharing the move-to-cover one: they are
  // two independent dialogs, and shared state lets a failure in one surface
  // in the other.
  const [moveMarkingBusy, setMoveMarkingBusy] = useState(false);
  const [moveMarkingError, setMoveMarkingError] = useState<string | null>(null);
  const [savingReviewed, setSavingReviewed] = useState(false);
  const [deletingImageId, setDeletingImageId] = useState<number | null>(null);

  const markingId = id ? parseInt(String(id).replace(/^api-/, ""), 10) : null;

  // Editors see the Record History panel; everyone else doesn't even fire the
  // changelog request. Mirrors the same role gate used for the destructive
  // Delete buttons further down so a single role-string change can't desync
  // the two surfaces. user is `null` while we're still resolving auth, so
  // wait for that to settle before deciding.
  const canViewHistory = useMemo(() => {
    if (!user) return false;
    return (
      user.role === "editor" ||
      user.role === "administrator" ||
      user.is_superuser === true
    );
  }, [user]);

  useEffect(() => {
    if (markingId == null || Number.isNaN(markingId)) {
      setError("Invalid record ID");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getMarkingById(markingId)
      .then((data) => {
        if (cancelled) return;
        if (!data) {
          setError("Record not found");
          return;
        }
        setRecord(data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load record");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [markingId]);

  // Fetcher extracted so both the initial mount and post-edit navigation
  // returns can refresh the cover list without duplicating request logic.
  useEffect(() => {
    if (markingId == null || Number.isNaN(markingId)) {
      setAssociatedCovers([]);
      setCoversLoadError(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const { covers: rows, error: coversErr } = await loadAssociatedCoversForMarking(markingId);
      if (cancelled) return;
      setCoversLoadError(coversErr);
      setAssociatedCovers(rows);
    })();
    return () => {
      cancelled = true;
    };
  }, [markingId, user?.id, location.pathname, location.search]);

  // Sibling markings at this post office -- the target list for "move image to
  // another marking" (#104 / C3). Editor-only, matching the control's own
  // gate; skipping it for everyone else keeps a public page view at its
  // current request count. `post_office` is an exact-id filter -- `town` is
  // name-contains server-side and would pull in other towns.
  //
  // The staff test is inlined rather than reusing the `isStaff` const below,
  // which is declared after this component's early returns: a hook placed
  // there would run conditionally.
  const userIsStaff =
    !!user &&
    (user.role === "editor" ||
      user.role === "administrator" ||
      user.is_superuser === true);
  // Depend on the two primitives, not on `record` itself: the record is a
  // fresh object on every refresh, and a successful move refreshes it, so
  // depending on the object refetched the sibling list after every move.
  const recordId = record?.id ?? null;
  const recordPostOfficeId = record?.postOfficeId ?? null;
  useEffect(() => {
    if (!userIsStaff || recordId == null || recordPostOfficeId == null) {
      setMoveMarkingCandidates([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        // A post office can hold more than one page of markings (max_page_size
        // is 100 server-side), and a candidate missing from the list is an
        // editor who cannot complete the move -- the exact failure this
        // feature exists to prevent. So page until the API stops offering a
        // next link. `count` is null under deferCount, so `next` is the only
        // truncation signal available. The cap is a runaway guard, not a
        // limit any real office reaches (production averages ~2.6 markings
        // per office).
        const MAX_PAGES = 20;
        const collected: MarkingRecord[] = [];
        for (let page = 1; page <= MAX_PAGES; page += 1) {
          const res = await getMarkingsPage(page, 100, {
            postOfficeId: recordPostOfficeId,
            deferCount: true,
          });
          if (cancelled) return;
          collected.push(...res.results);
          if (!res.next) break;
        }
        setMoveMarkingCandidates(
          moveTargetCandidates(collected, {
            id: recordId,
            postOfficeId: recordPostOfficeId,
          }),
        );
      } catch {
        // A failed lookup just hides the control; the page must still render.
        if (!cancelled) setMoveMarkingCandidates([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userIsStaff, recordId, recordPostOfficeId]);

  // Record History (audit trail). Only fires for editor-class users since the
  // backend `markings/{id}/changelog/` endpoint requires
  // `_user_is_responsible_for_marking` (assigned region OR superuser). For
  // unauthorized users the call returns null (see getMarkingChangelog), which
  // we surface as an empty-state message inside the panel instead of crashing
  // the page.
  useEffect(() => {
    if (markingId == null || Number.isNaN(markingId)) {
      setHistoryEvents([]);
      setHistoryError(null);
      return;
    }
    if (!canViewHistory) {
      setHistoryEvents([]);
      setHistoryError(null);
      return;
    }
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError(null);
    setHistoryExpanded(false);
    getMarkingChangelog(markingId)
      .then((data) => {
        if (cancelled) return;
        if (!data) {
          setHistoryEvents([]);
          setHistoryError(
            "Direct import from catalog",
          );
          return;
        }
        setHistoryEvents(Array.isArray(data.events) ? data.events : []);
      })
      .catch(() => {
        if (cancelled) return;
        setHistoryEvents([]);
        setHistoryError("Unable to load record history.");
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [markingId, canViewHistory]);

  useEffect(() => {
    if (!api) return;
    setCurrent(api.selectedScrollSnap());
    const onSelect = () => setCurrent(api.selectedScrollSnap());
    api.on("select", onSelect);
    return () => {
      api.off("select", onSelect);
    };
  }, [api]);

  /**
   * Apply a new ordering to the marking's images. Optimistically rewrites
   * `record.images` so the UI re-renders immediately, fires parallel PATCHes
   * to /api/v2/images/{id}/, and refetches the marking on completion to
   * reconcile any drift (e.g. if a concurrent edit changed display_order).
   * Used by the "Move up", "Move down", and "Set as default" controls.
   */
  const applyImageOrder = async (newImages: MarkingImage[]) => {
    if (markingId == null || Number.isNaN(markingId)) return;
    if (newImages.length === 0) return;
    setReorderingImages(true);
    setRecord((prev) =>
      prev
        ? {
            ...prev,
            images: newImages.map((img, idx) => ({
              ...img,
              displayOrder: idx,
            })),
            mainImage: newImages[0] ?? null,
            secondImage: newImages[1] ?? null,
          }
        : prev,
    );
    try {
      const ok = await reorderImages(
        newImages.map((img) => img.imageId).filter((id) => id > 0),
      );
      if (!ok) {
        toast({
          title: "Reorder failed",
          description:
            "Could not save the new image order. Refreshing from the server.",
          variant: "destructive",
        });
      }
      const refreshed = await getMarkingById(markingId);
      if (refreshed) setRecord(refreshed);
    } finally {
      setReorderingImages(false);
    }
  };

  const moveImageBy = (index: number, offset: -1 | 1) => {
    if (!record) return;
    const target = index + offset;
    if (target < 0 || target >= record.images.length) return;
    const next = record.images.slice();
    [next[index], next[target]] = [next[target], next[index]];
    void applyImageOrder(next);
  };

  const setImageAsDefault = (index: number) => {
    if (!record) return;
    if (index <= 0 || index >= record.images.length) return;
    const next = record.images.slice();
    const [picked] = next.splice(index, 1);
    next.unshift(picked);
    setCurrent(0);
    void applyImageOrder(next);
  };

  const locationState = location.state as {
    fromDashboard?: boolean;
    dashboardTab?: "submissions" | "editor";
  } | null;
  const fromDashboard = locationState?.fromDashboard === true;
  const dashboardTab = locationState?.dashboardTab;
  // Issue #87: this used to send everyone to /search, so an editor who opened a
  // record from their review queue was dumped into catalog search. When they
  // arrived from the dashboard, go back to the dashboard view they left.
  const handleBack = () => {
    if (fromDashboard) navigate(dashboardHrefForTab(dashboardTab ?? "editor"));
    else navigate("/search");
  };

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col">
        <Navigation />
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
        <Footer />
      </div>
    );
  }

  if (error || !record) {
    return (
      <div className="min-h-screen flex flex-col">
        <Navigation />
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <p className="text-muted-foreground">{error || "Record not found"}</p>
          <Button variant="outline" onClick={handleBack}>Back</Button>
        </div>
        <Footer />
      </div>
    );
  }

  // Comment-for-editor and editor feedback come straight off the marking's
  // approved Contribution. The backend returns "" unless the viewer is that
  // contributor or an editor, so a non-empty string is already authorized to show.
  const commentForEditor = record.commentForEditor?.trim() ?? "";
  const editorFeedback = record.editorFeedback?.trim() ?? "";
  // Issue #110. The ingest's "[VPHC: ...]" markers used to ride along in `desc`
  // and render publicly; they are now stripped on approval, so this card is the
  // only place the doubt is visible on a catalog record. Same gating as the two
  // above -- the backend returns null unless the viewer is an editor or the
  // contributor, so a non-null value is already authorised to show.
  // readVphcProvenance reads `.vphc` off a whole submitted_data blob; the
  // serializer hands back just that inner object, so re-wrap it rather than
  // duplicating the reader.
  const vphcProvenance = record.vphcProvenance
    ? readVphcProvenance({ vphc: record.vphcProvenance })
    : null;
  const submitterName = record.submitterName?.trim() ?? "";
  const galleryImages = buildGalleryImages(record);
  const typeLabel = markingTypeLabel(record.type) || "Townmark";

  const goEdit = () =>
    navigate(`/edit/${record.id}?mode=suggestion`, {
      state: {
        from: location.pathname + location.search,
        fromSearch: location.state?.fromSearch,
        fromDashboard,
        dashboardTab,
        fromDashboardViaDetail: !!fromDashboard,
        mode: "suggestion",
      },
    });

  // Re-pull the marking after a remove/restore so the buttons toggle in place
  // and the recycle-bin banner appears/disappears. Reuses the same loader the
  // page mounts with and refreshes after image edits. The editor/superuser
  // retrieve override (backend B1) keeps the page loadable once removed.
  const refetchRecord = async () => {
    if (markingId == null || Number.isNaN(markingId)) return;
    const refreshed = await getMarkingById(markingId);
    if (refreshed) setRecord(refreshed);
  };

  const handleDeleteImage = async (index: number) => {
    if (!record) return;
    const image = record.images[index];
    if (!image || image.imageId <= 0) return;
    const label = image.originalFilename || `image ${index + 1}`;
    const confirmed = window.confirm(
      `Delete ${label}? This removes the image from its catalog record.`,
    );
    if (!confirmed) return;
    setDeletingImageId(image.imageId);
    try {
      const res = await deleteImage(image.imageId);
      if (res.ok) {
        toast({ title: "Image deleted" });
        await refetchRecord();
        setCurrent((prev) => Math.max(0, Math.min(prev, record.images.length - 2)));
      } else {
        toast({
          title: "Could not delete image",
          description: "message" in res ? res.message : "Could not delete image.",
          variant: "destructive",
        });
      }
    } finally {
      setDeletingImageId(null);
    }
  };

  // State-editor "reviewed/confirmed" toggle (Issue #22). Optimistically reflect
  // the new value, persist, then re-pull so the displayed state matches the
  // server (and reverts if the editor wasn't authorized for this region).
  const handleReviewedToggle = async (next: boolean) => {
    if (!record || savingReviewed) return;
    setSavingReviewed(true);
    const ok = await updateMarkingReviewed(record.id, next);
    if (ok) {
      setRecord({ ...record, isReviewed: next });
      toast({ title: next ? "Marked as reviewed" : "Marked as not reviewed" });
    } else {
      toast({
        title: "Could not update review status",
        description: "You may not be the state editor for this record.",
        variant: "destructive",
      });
      await refetchRecord();
    }
    setSavingReviewed(false);
  };

  const handleRemoveConfirm = async () => {
    if (!record) return;
    setRemoving(true);
    try {
      const res = await removeMarking(record.id, removeReason.trim() || undefined);
      if (res.ok) {
        toast({ title: "Marking removed" });
        setRemoveOpen(false);
        setRemoveReason("");
        await refetchRecord();
      } else {
        toast({
          title: "Could not remove",
          description: "message" in res ? res.message : "Could not remove marking.",
          variant: "destructive",
        });
      }
    } finally {
      setRemoving(false);
    }
  };

  const handleRestoreConfirm = async () => {
    if (!record) return;
    setRestoring(true);
    try {
      const res = await restoreMarking(record.id);
      if (res.ok) {
        toast({ title: "Marking restored" });
        setRestoreOpen(false);
        await refetchRecord();
      } else {
        toast({
          title: "Could not restore",
          description: "message" in res ? res.message : "Could not restore marking.",
          variant: "destructive",
        });
      }
    } finally {
      setRestoring(false);
    }
  };

  const dimensionsValue = dimensionsDisplay(record) || EMPTY;
  const earliestValue = formatDateSeen(record.earliestSeen, record.earliestSeenGranularity);
  const latestValue = formatDateSeen(record.latestSeen, record.latestSeenGranularity);
  const earliestSeenTo =
    record.earliestSeenCoverId != null
      ? `/record/${record.id}/cover/${record.earliestSeenCoverId}`
      : undefined;
  const latestSeenTo =
    record.latestSeenCoverId != null
      ? `/record/${record.id}/cover/${record.latestSeenCoverId}`
      : undefined;
  const datesSeenValue = formatDatesSeenList(record.datesSeen);
  const impressionValue =
    record.impression && record.impression.trim().toLowerCase() !== "normal"
      ? record.impression
      : "";
  // Same test as `userIsStaff` above, which had to be hoisted for the sibling
  // -markings hook; aliased rather than recomputed so the two cannot drift.
  const isStaff = userIsStaff;


  // Record History display rule: collapsed by default we show the three most
  // recent events; when expanded we cap at the 10 newest events. Backend
  // already returns events sorted by timestamp DESC, so we slice from the
  // front to avoid an extra sort pass on every render.
  const HISTORY_COLLAPSED_LIMIT = 3;
  const HISTORY_EXPANDED_LIMIT = 10;
  const visibleHistoryEvents = historyExpanded
    ? historyEvents.slice(0, HISTORY_EXPANDED_LIMIT)
    : historyEvents.slice(0, HISTORY_COLLAPSED_LIMIT);
  const hasMoreHistory = historyEvents.length > HISTORY_COLLAPSED_LIMIT;
  const historyOverflow = Math.max(
    0,
    historyEvents.length - HISTORY_EXPANDED_LIMIT,
  );

  // Field order and visibility rules live in buildMarkingFields so
  // ContributionDetail renders the same sequence.
  const detailRows = buildMarkingFields(
    {
      type: record.type,
      isManuscript: record.isManuscript,
      state: regionsDisplay(record),
      // Each territory/state becomes a chip linking to a region-filtered
      // search. The Search page's `state` param matches on region name, and
      // its filter traverses the post_office_regions M2M. (issue #28)
      //
      // Counties are filtered out and rendered as their own County row below:
      // a town's `regions` list carries every link, so since the VPHC ingest
      // this was showing an "Accomack" chip beside "Virginia" that searched
      // for a state named Accomack. (issue #103)
      regionTags: primaryRegions(record).map((r) => ({
        label: r.name,
        to: `/search?state=${encodeURIComponent(r.name)}`,
      })),
      county: countyDisplay(record),
      town: record.town,
      postOfficeId: record.postOfficeId,
      inscriptionTxt: record.inscriptionTxt,
      earliestSeen: earliestValue,
      earliestSeenTo,
      latestSeen: latestValue,
      latestSeenTo,
      datesSeen: datesSeenValue,
      shapeName: record.shapeName,
      rateValFormatted: formatRateValue(record.rateVal),
      dateFmt: record.dateFmt,
      impression: impressionValue,
      isIrreg: record.isIrreg,
      colorName: record.colorName,
      letteringName: record.letteringName,
      dimensions: dimensionsValue,
      catalogTxt: record.catalogTxt,
      code: record.code,
    },
    { isStaff },
  );

  const coverCount = associatedCovers.length;
  // Unauthenticated visitors clicking a write-action button get bounced to
  // /auth with `from` state so the auth page can return them here after
  // login. Matches the pattern used in App.tsx for protected routes.
  const requireAuth = (): boolean => {
    if (user) return true;
    navigate("/auth", { state: { from: location } });
    return false;
  };
  const openNewCoverDialog = () => {
    if (!requireAuth()) return;
    navigate(`/record/${markingId}/cover/new`, {
      state: { from: location.pathname + location.search },
    });
  };

  // Reassigns an image from this marking to one of its associated covers
  // (issue #48: v1 attached every cover upload to the marking). Target list
  // is restricted to covers already linked to this marking so images can't
  // be scattered onto unrelated records from here.
  const handleMoveImageToCover = async () => {
    if (!moveImageDialogImg?.imageId) return;
    const coverId = parseInt(moveImageTargetCoverId, 10);
    if (!Number.isFinite(coverId) || coverId <= 0) {
      setMoveImageError("Select a target cover.");
      return;
    }
    setMoveImageBusy(true);
    setMoveImageError(null);
    try {
      const res = await moveImageSubject(
        moveImageDialogImg.imageId,
        "COVER",
        coverId,
        moveImageView,
      );
      if (res.ok === false) {
        setMoveImageError(res.message);
        return;
      }
      toast({ title: "Image moved", description: "Image reassigned to the cover." });
      setMoveImageDialogImg(null);
      if (markingId != null) {
        const refreshed = await getMarkingById(markingId);
        if (refreshed) setRecord(refreshed);
      }
    } finally {
      setMoveImageBusy(false);
    }
  };

  // Reassign an image to another marking at the same post office (#104 / C3).
  // This is the second half of the crop -> reattach workflow: crop saves the
  // cut-out onto the SAME marking (it deliberately never relocates), and this
  // sends it to the marking it actually belongs to. Scoped to one town so an
  // image cannot be filed under an unrelated record from here.
  const handleMoveImageToMarking = async () => {
    if (!moveToMarkingImg?.imageId) return;
    const targetId = parseInt(moveMarkingTargetId, 10);
    if (!Number.isFinite(targetId) || targetId <= 0) {
      setMoveMarkingError("Select a target marking.");
      return;
    }
    setMoveMarkingBusy(true);
    setMoveMarkingError(null);
    try {
      const res = await moveImageSubject(
        moveToMarkingImg.imageId,
        "MARKING",
        targetId,
        moveMarkingView,
      );
      if (res.ok === false) {
        setMoveMarkingError(res.message);
        return;
      }
      toast({ title: "Image moved", description: "Image reassigned to the marking." });
      // Full teardown, so reopening for a different image cannot inherit the
      // previous target.
      setMoveToMarkingImg(null);
      setMoveMarkingTargetId("");
      setMoveMarkingView("FULL");
      if (markingId != null) {
        const refreshed = await getMarkingById(markingId);
        if (refreshed) setRecord(refreshed);
      }
    } finally {
      setMoveMarkingBusy(false);
    }
  };

  // Creates a CoverMarking junction row between this marking and an
  // already-existing cover. The endpoint is editor/admin-gated
  // (IsEditorOrAdminWrite), so the button only renders for isStaff.
  const handleLinkExistingCover = async () => {
    if (markingId == null) return;
    const coverId = parseCoverIdInput(linkCoverInput);
    if (coverId == null) {
      setLinkCoverError("Enter a valid cover ID (e.g. 42 or C-42).");
      return;
    }
    setLinkCoverBusy(true);
    setLinkCoverError(null);
    try {
      const cover = await getCoverById(coverId);
      if (!cover) {
        setLinkCoverError(`Cover ${coverId} not found.`);
        return;
      }
      await createCoverMarking({
        cover: coverId,
        marking: markingId,
        is_backstamp: linkCoverIsBackstamp,
      });
      toast({
        title: "Cover linked",
        description: `Cover ${cover.code ?? coverId} is now linked to this marking.`,
      });
      setLinkCoverOpen(false);
      setLinkCoverInput("");
      setLinkCoverIsBackstamp(false);
      const { covers: rows, error: coversErr } = await loadAssociatedCoversForMarking(markingId);
      setCoversLoadError(coversErr);
      setAssociatedCovers(rows);
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: string; non_field_errors?: string[] } } };
      const detail = ax.response?.data?.detail ?? ax.response?.data?.non_field_errors?.[0];
      setLinkCoverError(
        typeof detail === "string" ? detail : "Could not link cover. It may already be linked.",
      );
    } finally {
      setLinkCoverBusy(false);
    }
  };
  const goCoverView = (cover: AssociatedCover) => {
    if (markingId == null) return;
    if (cover.contributionDraftId != null) {
      navigate(`/record/${markingId}/cover/new?edit=${cover.contributionDraftId}`, {
        state: { from: location.pathname + location.search },
      });
      return;
    }
    const coverId = cover.coverDetails?.id;
    if (coverId == null || coverId < 0) return;
    navigate(`/record/${markingId}/cover/${coverId}`, {
      state: { from: location.pathname + location.search },
    });
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />
      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="mb-6">
            <Button variant="ghost" onClick={handleBack} className="-ml-4">
              <ArrowLeft className="mr-2 h-4 w-4" />
              {fromDashboard ? "Back to Dashboard" : "Back"}
            </Button>
          </div>

          <div className="grid items-start lg:grid-cols-2 gap-8">
            <div className="space-y-6">
              <Card className="shadow-archival-lg">
                <CardContent className="p-6">
                  <Carousel setApi={setApi} className="w-full">
                    <CarouselContent>
                      {(galleryImages.length
                        ? galleryImages
                        : [
                            {
                              imageUrl: imageNotAvailable,
                              subjectLabel: typeLabel,
                              isDefault: false,
                              isTracing: false,
                              imageId: null,
                            } satisfies GalleryImage,
                          ]
                      ).map((img, index) => {
                        const src = img.imageUrl || imageNotAvailable;
                        const alt = img.originalFilename || `Image ${index + 1}`;
                        const isPlaceholder = !img.imageUrl;
                        const canSetDefaultImage =
                          isStaff &&
                          !record.isRemoved &&
                          img.imageId != null &&
                          !isPlaceholder;
                        const imageFrame = (
                          <div className="relative flex w-full aspect-[4/3] items-center justify-center rounded border border-border bg-muted overflow-hidden">
                            <img src={src} alt={alt} className="w-full h-full object-contain" />
                            <div className="absolute top-2 left-2 flex flex-wrap items-center gap-1">
                              {!isPlaceholder && img.isTracing && (
                                <Badge variant="secondary">Tracing</Badge>
                              )}
                            </div>
                          </div>
                        );
                        return (
                          <CarouselItem key={index}>
                            <div className="relative">
                              {img.imageUrl ? (
                                <a
                                  href={img.imageUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  aria-label={`Open ${alt} in new tab`}
                                  className="block"
                                >
                                  {imageFrame}
                                </a>
                              ) : (
                                imageFrame
                              )}
                              {canSetDefaultImage && (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="icon"
                                      className={
                                        img.isDefault
                                          ? "absolute right-2 top-2 h-8 w-8 border-amber-400 bg-amber-100 text-amber-700 hover:bg-amber-100 hover:text-amber-700 disabled:opacity-100"
                                          : "absolute right-2 top-2 h-8 w-8 bg-background/90"
                                      }
                                      aria-label={
                                        img.isDefault
                                          ? "Default catalog image"
                                          : "Set as default catalog image"
                                      }
                                      disabled={reorderingImages || img.isDefault}
                                      onClick={() => setImageAsDefault(index)}
                                    >
                                      <Star className={`h-4 w-4 ${img.isDefault ? "fill-amber-500 text-amber-500" : ""}`} />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    {img.isDefault ? "Default catalog image" : "Set as default catalog image"}
                                  </TooltipContent>
                                </Tooltip>
                              )}
                            </div>
                          </CarouselItem>
                        );
                      })}
                    </CarouselContent>
                    {galleryImages.length > 1 && (<><CarouselPrevious className="left-2" /><CarouselNext className="right-2" /></>)}
                  </Carousel>
                  {galleryImages.length > 1 && (
                    <div className="flex justify-center gap-2 mt-4">
                      {galleryImages.map((_, index) => (
                        <button
                          key={index}
                          onClick={() => api?.scrollTo(index)}
                          className={`h-2 rounded-full transition-all ${index === current ? "w-6 bg-primary" : "w-2 bg-muted-foreground/30"}`}
                          aria-label={`Go to image ${index + 1}`}
                        />
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="shadow-archival-md">
                <CardHeader>
                  <CardTitle className="font-heading text-lg">Associated Thumbnails</CardTitle>
                </CardHeader>
                <CardContent>
                  {galleryImages.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No approved images linked to this marking.</p>
                  ) : (
                    <div className="flex gap-3 overflow-x-auto pb-1">
                      {galleryImages.map((img, idx) => {
                        // A removed marking is read-only: no image reordering
                        // either, only Restore.
                        const canManageImage =
                          isStaff &&
                          !record.isRemoved &&
                          img.imageId != null;
                        const canReorder = canManageImage && galleryImages.length > 1;
                        const canMoveToCover =
                          canManageImage &&
                          record.images[idx]?.subjectType === "MARKING" &&
                          associatedCovers.some((c) => c.coverDetails?.id != null);
                        // Hidden at a one-marking town rather than opening an
                        // empty dropdown (#104 / C3).
                        const canMoveToMarking =
                          canManageImage &&
                          record.images[idx]?.subjectType === "MARKING" &&
                          moveMarkingCandidates.length > 0;
                        return (
                          <div
                            key={`${img.imageId ?? img.originalFilename ?? "img"}-${idx}`}
                            className="flex flex-col items-center gap-1 shrink-0"
                          >
                            <button
                              type="button"
                              onClick={() => api?.scrollTo(idx)}
                              aria-label={`Show image ${idx + 1}`}
                              className={`relative h-16 w-16 rounded border overflow-hidden transition-all ${idx === current ? "border-primary ring-2 ring-primary" : "border-border"}`}
                            >
                              <img
                                src={img.imageUrl || imageNotAvailable}
                                alt={img.originalFilename || `Thumbnail ${idx + 1}`}
                                className="h-full w-full object-cover"
                              />
                            </button>
                            {canManageImage && (
                              // Editor reorder strip. Each button issues a
                              // PATCH /api/v2/images/{id}/ via applyImageOrder
                              // (with optimistic UI). Star = move to position
                              // 0 = becomes the Catalog Search thumbnail.
                              <div className="flex items-center gap-0.5">
                                {canReorder && (
                                  <>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon"
                                      className="h-6 w-6"
                                      aria-label="Move thumbnail left"
                                      disabled={
                                        reorderingImages || idx === 0
                                      }
                                      onClick={() => moveImageBy(idx, -1)}
                                    >
                                      <ArrowUp className="h-3 w-3 -rotate-90" />
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon"
                                      className="h-6 w-6"
                                      aria-label="Move thumbnail right"
                                      disabled={
                                        reorderingImages ||
                                        idx === galleryImages.length - 1
                                      }
                                      onClick={() => moveImageBy(idx, 1)}
                                    >
                                      <ArrowDown className="h-3 w-3 -rotate-90" />
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon"
                                      className={
                                        img.isDefault
                                          ? "h-6 w-6 text-amber-600 hover:text-amber-600 disabled:opacity-100"
                                          : "h-6 w-6"
                                      }
                                      aria-label="Set as default catalog thumbnail"
                                      title={
                                        img.isDefault
                                          ? "Default catalog thumbnail"
                                          : "Set as default catalog thumbnail"
                                      }
                                      disabled={reorderingImages || img.isDefault}
                                      onClick={() => setImageAsDefault(idx)}
                                    >
                                      <Star
                                        className={`h-3 w-3 ${img.isDefault ? "fill-amber-500 text-amber-500" : ""}`}
                                      />
                                    </Button>
                                  </>
                                )}
                                {canManageImage && (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    aria-label="Crop the marking out of this image"
                                    title="Crop marking"
                                    disabled={reorderingImages}
                                    onClick={() => {
                                      const rawImg = record.images[idx];
                                      if (!rawImg?.imageId) return;
                                      setCropImageTarget(rawImg);
                                    }}
                                  >
                                    <Crop className="h-3 w-3" />
                                  </Button>
                                )}
                                {canMoveToCover && (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    aria-label="Move image to a cover entry"
                                    title="Move to cover"
                                    disabled={reorderingImages}
                                    onClick={() => {
                                      const rawImg = record.images[idx];
                                      if (!rawImg) return;
                                      setMoveImageDialogImg(rawImg);
                                      setMoveImageTargetCoverId(
                                        String(
                                          associatedCovers.find((c) => c.coverDetails?.id != null)
                                            ?.coverDetails?.id ?? "",
                                        ),
                                      );
                                      setMoveImageView("FRONT");
                                      setMoveImageError(null);
                                    }}
                                  >
                                    <Replace className="h-3 w-3" />
                                  </Button>
                                )}
                                {canMoveToMarking && (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    aria-label="Move image to another marking"
                                    title="Move to another marking"
                                    disabled={reorderingImages}
                                    onClick={() => {
                                      const rawImg = record.images[idx];
                                      if (!rawImg) return;
                                      setMoveToMarkingImg(rawImg);
                                      setMoveMarkingTargetId(
                                        String(moveMarkingCandidates[0]?.id ?? ""),
                                      );
                                      setMoveMarkingView("FULL");
                                      setMoveMarkingError(null);
                                    }}
                                  >
                                    <Stamp className="h-3 w-3" />
                                  </Button>
                                )}
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon"
                                  className="h-6 w-6 text-destructive hover:text-destructive"
                                  aria-label="Delete image"
                                  title="Delete image"
                                  disabled={
                                    reorderingImages ||
                                    deletingImageId === img.imageId
                                  }
                                  onClick={() => handleDeleteImage(idx)}
                                >
                                  <Trash2 className="h-3 w-3" />
                                </Button>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              {isStaff && (
                <Card className="shadow-archival-md">
                  <CardHeader>
                    <CardTitle className="font-heading text-lg flex items-center gap-2">
                      <History className="h-5 w-5 text-muted-foreground" />
                      Record History
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {historyLoading ? (
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading history...
                      </div>
                    ) : historyError ? (
                      <p className="text-sm text-muted-foreground">{historyError}</p>
                    ) : historyEvents.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No audit events recorded for this marking yet.
                      </p>
                    ) : (
                      <>
                        <ul className="divide-y divide-border text-sm">
                          {visibleHistoryEvents.map((event) => (
                            <li
                              key={event.event_id}
                              className="py-3 first:pt-0 last:pb-0"
                            >
                              <div className="flex items-baseline justify-between gap-3">
                                <span className="font-medium text-foreground">
                                  {event.action_label || event.action}
                                </span>
                                <span className="text-xs text-muted-foreground whitespace-nowrap">
                                  {formatHistoryTimestamp(event.timestamp)}
                                </span>
                              </div>
                              <div className="mt-1 text-xs text-muted-foreground break-all">
                                {historyActorDisplay(event)}
                              </div>
                            </li>
                          ))}
                        </ul>
                        {hasMoreHistory && (
                          <div className="mt-3 flex items-center justify-between gap-3">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => setHistoryExpanded((v) => !v)}
                            >
                              {historyExpanded
                                ? `Show latest ${HISTORY_COLLAPSED_LIMIT}`
                                : `Show recent history (up to ${HISTORY_EXPANDED_LIMIT})`}
                            </Button>
                            {historyExpanded && historyOverflow > 0 && (
                              <span className="text-xs text-muted-foreground">
                                {historyOverflow} older event
                                {historyOverflow === 1 ? "" : "s"} not shown
                              </span>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </CardContent>
                </Card>
              )}
            </div>

            <div className="space-y-6">
              {record.isRemoved && (
                <div className="flex items-center gap-2 rounded-md border border-muted bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
                  <Info className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>This entry has been marked for removal.</span>
                </div>
              )}
              <Card className="shadow-archival-md">
                <CardHeader>
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="font-heading text-lg">Marking Details</CardTitle>
                    {/* A removed marking is read-only: no edits until it is restored. */}
                    {!record.isRemoved && (
                      <Button variant="outline" size="sm" onClick={goEdit}>
                        <Pencil className="mr-2 h-4 w-4" />
                        {SUBMISSION_LABELS.action.submitEditToMarking}
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  <MarkingFieldsDisplay rows={detailRows} mode="record" />
                  {/* State-editor review toggle (Issue #22): only shown to
                      editors/admins. The backend independently enforces that
                      the editor is responsible for this record's region. */}
                  {isStaff && !record.isRemoved && (
                    <div className="mt-4 flex items-center gap-2 border-t pt-4">
                      <Checkbox
                        id="marking-reviewed"
                        checked={record.isReviewed}
                        disabled={savingReviewed}
                        onCheckedChange={(value) => handleReviewedToggle(value === true)}
                      />
                      <label
                        htmlFor="marking-reviewed"
                        className="text-sm font-medium leading-none"
                      >
                        Reviewed / confirmed
                      </label>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="shadow-archival-md">
                <CardHeader>
                  <div className="flex items-center justify-between gap-3">
                    <CardTitle className="font-heading text-lg">
                      Associated Covers ({coverCount})
                    </CardTitle>
                    {/* No new covers can be attached to a removed marking. */}
                    {!record.isRemoved && (
                      <div className="flex gap-2">
                        {isStaff && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setLinkCoverInput("");
                              setLinkCoverIsBackstamp(false);
                              setLinkCoverError(null);
                              setLinkCoverOpen(true);
                            }}
                          >
                            <Plus className="mr-2 h-4 w-4" />
                            Link Existing Cover
                          </Button>
                        )}
                        <Button
                          size="sm"
                          onClick={openNewCoverDialog}
                          className="bg-green-800 hover:bg-green-900 text-white"
                        >
                          <Plus className="mr-2 h-4 w-4" />
                          {SUBMISSION_LABELS.action.submitNewCover}
                        </Button>
                      </div>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="space-y-4 pt-0">
                  {coversLoadError && (
                    <p className="text-sm text-destructive rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2">
                      {coversLoadError}
                    </p>
                  )}
                  {coverCount === 0 && !coversLoadError && (
                    <p className="text-sm text-muted-foreground">No covers linked to this marking yet.</p>
                  )}
                  {coverCount > 0 && (
                    <>
                    <div className="space-y-4">
                        {associatedCovers.map((cover) => {
                          const c = cover.coverDetails;
                          const thumb = cover.defaultImageUrl ?? null;
                          const codeLabel =
                            cover.displayLabel?.trim() ||
                            (isStaff ? c?.code?.trim() : "") ||
                            (cover.contributionDraftId != null
                              ? `Cover draft #${cover.contributionDraftId}`
                              : `Cover #${c?.id ?? cover.id}`);
                          const rs = cover.reviewStatus;
                          const canOpenCover =
                            markingId != null &&
                            (cover.contributionDraftId != null ||
                              (c?.id != null && c.id > 0));
                          return (
                            <Card
                              key={cover.contributionDraftId ?? cover.id}
                              className={`shadow-archival-md hover:shadow-archival-lg transition-shadow ${
                                canOpenCover ? "cursor-pointer" : ""
                              }`}
                              onClick={
                                canOpenCover
                                  ? () => goCoverView(cover)
                                  : undefined
                              }
                              onKeyDown={
                                canOpenCover
                                  ? (e) => {
                                      if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        goCoverView(cover);
                                      }
                                    }
                                  : undefined
                              }
                              role={canOpenCover ? "button" : undefined}
                              tabIndex={canOpenCover ? 0 : undefined}
                              aria-label={canOpenCover ? `Open cover ${codeLabel}` : undefined}
                            >
                              <CardContent className="p-4">
                                <div className="flex gap-6 md:flex-row flex-col">
                                  <ImageOrPlaceholder
                                    src={thumb}
                                    alt={codeLabel}
                                    className="md:w-32 md:h-32 w-full h-48 object-cover rounded border border-border shrink-0"
                                  />
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-start justify-between gap-3 mb-2 flex-wrap">
                                      <h3 className="font-heading text-xl font-semibold text-foreground">
                                        {codeLabel}
                                      </h3>
                                      {associatedCoverShowsStatusBadge(cover) && (
                                        <Badge
                                          variant={
                                            cover.contributionDraftId != null
                                              ? "secondary"
                                              : rs === "pending"
                                                ? "secondary"
                                                : rs === "needs_revision"
                                                  ? "outline"
                                                  : "destructive"
                                          }
                                          className="font-normal shrink-0"
                                        >
                                          {coverLinkReviewBadgeLabel(cover)}
                                        </Badge>
                                      )}
                                    </div>
                                    <AssociatedCoverPreviewFields cover={cover} />
                                  </div>
                                </div>
                              </CardContent>
                            </Card>
                          );
                        })}
                    </div>
                    </>
                  )}
                </CardContent>
              </Card>

              {record.desc.trim() && (
                <Card className="shadow-archival-md">
                  <CardHeader><CardTitle className="font-heading text-lg">Description</CardTitle></CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground leading-relaxed whitespace-pre-line">{record.desc}</p>
                  </CardContent>
                </Card>
              )}

              {submitterName && (
                <Card className="shadow-archival-md">
                  <CardHeader><CardTitle className="font-heading text-lg">Submitted by</CardTitle></CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground">{submitterName}</p>
                  </CardContent>
                </Card>
              )}

              {vphcProvenance && <VphcProvenanceCard provenance={vphcProvenance} />}

              {commentForEditor && (
                <Card className="shadow-archival-md">
                  <CardHeader>
                    <CardTitle className="font-heading text-lg">Comment for editor</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="rounded-md border border-border bg-muted/40 px-3 py-2">
                      <p className="text-sm text-foreground whitespace-pre-line">{commentForEditor}</p>
                    </div>
                  </CardContent>
                </Card>
              )}

              {editorFeedback && (
                <Card className="shadow-archival-md border-amber-500/20 bg-amber-500/5">
                  <CardHeader>
                    <CardTitle className="font-heading text-lg flex items-center gap-2">
                      <MessageSquare className="h-5 w-5 text-amber-600" />
                      Editor feedback
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-foreground leading-relaxed whitespace-pre-line">{editorFeedback}</p>
                  </CardContent>
                </Card>
              )}

              {record.citations.length > 0 && (
                <Card className="shadow-archival-md">
                  <CardHeader>
                    <CardTitle className="font-heading text-lg">
                      Citations ({record.citations.length})
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    {record.citations.map((citation, idx) => {
                      const rw = citation.referenceWork;
                      const code = (rw?.code ?? "").trim();
                      const title = citationTitle(citation);
                      const byline = citationByline(rw);
                      const detail = citation.citationDetail.trim();
                      const detailIsUrl = /^https?:\/\//i.test(detail);
                      const rwUrl = (rw?.url ?? "").trim();
                      const rows: { label: string; value: ReactNode }[] = [];
                      if (detail) {
                        rows.push({
                          label: "Page",
                          value: detailIsUrl ? (
                            <a
                              href={detail}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="underline text-primary break-all"
                            >
                              {detail}
                            </a>
                          ) : (
                            detail
                          ),
                        });
                      }
                      if (rw?.publisher.trim()) {
                        rows.push({ label: "Publisher", value: rw.publisher.trim() });
                      }
                      if (rw?.edition.trim()) {
                        rows.push({ label: "Edition", value: rw.edition.trim() });
                      }
                      if (rw?.volume.trim()) {
                        rows.push({ label: "Volume", value: rw.volume.trim() });
                      }
                      if (rw?.isbn.trim()) {
                        rows.push({ label: "ISBN", value: rw.isbn.trim() });
                      }
                      if (rwUrl && !detailIsUrl) {
                        rows.push({
                          label: "Link",
                          value: (
                            <a
                              href={rwUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="underline text-primary break-all"
                            >
                              {rwUrl}
                            </a>
                          ),
                        });
                      }
                      return (
                        <div
                          key={citation.id}
                          className={
                            idx === 0
                              ? ""
                              : "border-t-2 border-primary/40 pt-6 mt-6"
                          }
                        >
                          <div className="flex items-baseline gap-2 flex-wrap">
                            {code && (
                              <Badge variant="secondary" className="font-mono">
                                {code}
                              </Badge>
                            )}
                            <div className="font-medium text-foreground">
                              {title}
                            </div>
                          </div>
                          {byline && (
                            <div className="mt-1 text-xs text-muted-foreground italic">
                              {byline}
                            </div>
                          )}
                          {rows.length > 0 && (
                            <dl className="mt-3 text-sm">
                              {rows.map((r, i) => (
                                <div
                                  key={r.label}
                                  className={`flex justify-between gap-4 py-2 ${i === rows.length - 1 ? "" : "border-b border-border"}`}
                                >
                                  <dt className="text-muted-foreground font-medium shrink-0">
                                    {r.label}
                                  </dt>
                                  <dd className="text-foreground text-right break-words min-w-0">
                                    {r.value}
                                  </dd>
                                </div>
                              ))}
                            </dl>
                          )}
                        </div>
                      );
                    })}
                  </CardContent>
                </Card>
              )}
            </div>
          </div>

          {isStaff && record.canRemove && (
            <div className="mt-8 flex justify-end">
              {record.isRemoved ? (
                <Button variant="outline" onClick={() => setRestoreOpen(true)}>
                  <Recycle className="mr-2 h-4 w-4" />
                  Restore Marking
                </Button>
              ) : (
                <Button variant="destructive" onClick={() => setRemoveOpen(true)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Remove Marking
                </Button>
              )}
            </div>
          )}

        </div>
      </div>

      <Dialog open={removeOpen} onOpenChange={(open) => !removing && setRemoveOpen(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove Marking</DialogTitle>
            <DialogDescription>
              This moves the marking to the recycle bin and hides it from the public catalog.
              It can be restored later. Optionally record a reason.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Reason (optional)"
            value={removeReason}
            onChange={(e) => setRemoveReason(e.target.value)}
            disabled={removing}
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRemoveOpen(false)}
              disabled={removing}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleRemoveConfirm()}
              disabled={removing}
            >
              {removing ? "Removing..." : "Remove Marking"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={restoreOpen} onOpenChange={(open) => !restoring && setRestoreOpen(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restore Marking</AlertDialogTitle>
            <AlertDialogDescription>
              This returns the marking to the public catalog.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restoring}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleRestoreConfirm();
              }}
              disabled={restoring}
            >
              {restoring ? "Restoring..." : "Restore Marking"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={moveImageDialogImg != null}
        onOpenChange={(open) => {
          if (moveImageBusy) return;
          if (!open) setMoveImageDialogImg(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Move Image to Cover</DialogTitle>
            <DialogDescription>
              Reassign this image from the marking to one of its associated covers.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1">
              <Label htmlFor="move-img-cover-id">Target cover</Label>
              <Select
                value={moveImageTargetCoverId}
                onValueChange={(v) => {
                  setMoveImageTargetCoverId(v);
                  setMoveImageError(null);
                }}
                disabled={moveImageBusy}
              >
                <SelectTrigger id="move-img-cover-id">
                  <SelectValue placeholder="Select a cover…" />
                </SelectTrigger>
                <SelectContent>
                  {associatedCovers
                    .filter((c) => c.coverDetails?.id != null)
                    .map((c) => (
                      <SelectItem key={c.coverDetails!.id} value={String(c.coverDetails!.id)}>
                        {c.coverDetails!.code ?? `Cover #${c.coverDetails!.id}`}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="move-img-view">Image view</Label>
              <Select
                value={moveImageView}
                onValueChange={(v) => setMoveImageView(v)}
                disabled={moveImageBusy}
              >
                <SelectTrigger id="move-img-view">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="FRONT">Front</SelectItem>
                  <SelectItem value="BACK">Back</SelectItem>
                  <SelectItem value="INTERIOR">Interior</SelectItem>
                  <SelectItem value="DETAIL">Detail</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {moveImageError && <p className="text-sm text-destructive">{moveImageError}</p>}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setMoveImageDialogImg(null)}
              disabled={moveImageBusy}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleMoveImageToCover()}
              disabled={moveImageBusy || !moveImageTargetCoverId}
            >
              {moveImageBusy ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Moving…
                </>
              ) : (
                "Move Image"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={moveToMarkingImg != null}
        onOpenChange={(open) => {
          if (moveMarkingBusy) return;
          if (!open) setMoveToMarkingImg(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Move Image to Marking</DialogTitle>
            <DialogDescription>
              Reassign this image to another marking at the same town. Use this after
              cropping a second device out of a scan that shows more than one.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1">
              <Label htmlFor="move-img-marking-id">Target marking</Label>
              <Select
                value={moveMarkingTargetId}
                onValueChange={(v) => {
                  setMoveMarkingTargetId(v);
                  setMoveImageError(null);
                }}
                disabled={moveMarkingBusy}
              >
                <SelectTrigger id="move-img-marking-id">
                  <SelectValue placeholder="Select a marking…" />
                </SelectTrigger>
                <SelectContent>
                  {moveMarkingCandidates.map((m) => (
                    <SelectItem key={m.id} value={String(m.id)}>
                      {m.code || `Marking #${m.id}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="move-img-marking-view">Image view</Label>
              {/* Marking subjects accept FULL/DETAIL only; the cover views
                  (Front/Back/Interior) are rejected by the serializer. */}
              <Select
                value={moveMarkingView}
                onValueChange={(v) => setMoveMarkingView(v)}
                disabled={moveMarkingBusy}
              >
                <SelectTrigger id="move-img-marking-view">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="FULL">Full</SelectItem>
                  <SelectItem value="DETAIL">Detail</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {moveMarkingError && <p className="text-sm text-destructive">{moveMarkingError}</p>}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setMoveToMarkingImg(null)}
              disabled={moveMarkingBusy}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleMoveImageToMarking()}
              disabled={moveMarkingBusy || !moveMarkingTargetId}
            >
              {moveMarkingBusy ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Moving…
                </>
              ) : (
                "Move Image"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CropImageDialog
        open={cropImageTarget != null}
        onOpenChange={(open) => {
          if (!open) setCropImageTarget(null);
        }}
        imageId={cropImageTarget?.imageId ?? null}
        imageUrl={cropImageTarget?.imageUrl ?? null}
        onCropped={async () => {
          toast({
            title: "Crop saved",
            description:
              "Added as a new image on this record. You can now move the original to a cover.",
          });
          if (markingId != null) {
            const refreshed = await getMarkingById(markingId);
            if (refreshed) setRecord(refreshed);
          }
        }}
      />

      <Dialog
        open={linkCoverOpen}
        onOpenChange={(open) => {
          if (linkCoverBusy) return;
          setLinkCoverOpen(open);
          if (!open) {
            setLinkCoverInput("");
            setLinkCoverIsBackstamp(false);
            setLinkCoverError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Link Existing Cover</DialogTitle>
            <DialogDescription>
              Enter the cover ID or code (e.g. 42 or C-42) to link it to this marking.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Input
              placeholder="Cover ID or code"
              value={linkCoverInput}
              onChange={(e) => {
                setLinkCoverInput(e.target.value);
                setLinkCoverError(null);
              }}
              disabled={linkCoverBusy}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleLinkExistingCover();
                }
              }}
              autoFocus
            />
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={linkCoverIsBackstamp}
                onCheckedChange={(value) => setLinkCoverIsBackstamp(value === true)}
                disabled={linkCoverBusy}
              />
              Backstamp
            </label>
            {linkCoverError && <p className="text-sm text-destructive">{linkCoverError}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLinkCoverOpen(false)} disabled={linkCoverBusy}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleLinkExistingCover()}
              disabled={linkCoverBusy || !linkCoverInput.trim()}
            >
              {linkCoverBusy ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Linking…
                </>
              ) : (
                "Link Cover"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Footer />
    </div>
  );
};

export default RecordDetail;
