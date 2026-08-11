import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { CheckCircle, Info, Loader2, MessageSquare, Pencil, Plus, Trash2, XCircle } from "lucide-react";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type CarouselApi } from "@/components/ui/carousel";
import { formatDateSeen } from "@/lib/catalogRecordDisplay";
import { EntryDetailLayout } from "@/components/entry-detail/EntryDetailLayout";
import { EntryImageGalleryCard } from "@/components/entry-detail/EntryImageGalleryCard";
import { EntryAssociatedThumbnailsCard } from "@/components/entry-detail/EntryAssociatedThumbnailsCard";
import { EntryRecordHistoryCard } from "@/components/entry-detail/EntryRecordHistoryCard";
import { EntryCitationsCard, type EntryCitationItem } from "@/components/entry-detail/EntryCitationsCard";
import { CoverRecordDetailFields } from "@/components/entry-detail/CoverRecordDetailFields";
import { AssociatedMarkingPreviewCard } from "@/components/entry-detail/AssociatedMarkingPreviewCard";
import type { EntryGalleryImage } from "@/components/entry-detail/types";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/hooks/useAuth";
import {
  getImagesForSubject,
  getMarkingById,
  getMarkingChangelog,
  getCoverMarkingsByCover,
  deleteImage,
  loadAssociatedMarkingsForCover,
  moveImageSubject,
  normalizeImageUrl,
  postCoverMarkingReview,
  reorderImages,
  type AssociatedMarkingOnCover,
  type CoverMarkingLink,
  type CoverMarkingReviewActionApi,
  type CoverMarkingReviewStatus,
  type MarkingChangelogEvent,
  type MarkingImage,
} from "@/services/markings";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  createCoverMarking,
  getCoverById,
  removeCover,
  restoreCover,
  type CoverDetail,
  type CoverDateSeenItem,
} from "@/services/covers";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { parseMarkingIdInput } from "@/lib/recordLinking";
import { listCitationsForSubject } from "@/services/citations";
import { getReferenceWorks, type ReferenceWorkRecord } from "@/services/referenceWorks";
import { SUBMISSION_LABELS } from "@/labels/submission";

const EMPTY = "-";

type CoverDetailLocationState = {
  from?: string;
  fromDashboard?: boolean;
  dashboardTab?: "submissions" | "editor";
  markingId?: number;
  coverMarkingId?: number;
};

function buildCoverGalleryImages(images: MarkingImage[]): EntryGalleryImage[] {
  return images.map((img) => ({
    imageUrl: normalizeImageUrl(img.imageUrl),
    originalFilename: img.originalFilename || undefined,
    isDefault: img.displayOrder === 0,
    isTracing: img.isTracing,
    imageId: img.imageId > 0 ? img.imageId : null,
  }));
}

function coverTypeLabel(t: string | null): string {
  if (t === "FC") return "Folded Cover";
  if (t === "FL") return "Folded Letter";
  return EMPTY;
}

function formatCoverDate(d: CoverDateSeenItem): string {
  return formatDateSeen(d.date, d.granularity, {
    dateYear: d.dateYear,
    dateMonth: d.dateMonth,
    dateDay: d.dateDay,
  }) || d.date || "";
}




function coverLinkReviewBadgeLabel(status: CoverMarkingReviewStatus): string {
  switch (status) {
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


const CoverDetailPage = () => {
  const { id: markingIdParam, coverId: coverIdParam } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as CoverDetailLocationState | undefined;
  const user = useAuth();
  const { toast } = useToast();

  const markingIdFromRoute = useMemo(() => {
    if (!markingIdParam) return null;
    const n = parseInt(String(markingIdParam).replace(/^api-/, ""), 10);
    return Number.isFinite(n) ? n : null;
  }, [markingIdParam]);

  const markingId = markingIdFromRoute ?? state?.markingId ?? null;

  const coverPk = useMemo(() => {
    const n = coverIdParam ? parseInt(String(coverIdParam), 10) : NaN;
    return Number.isFinite(n) ? n : null;
  }, [coverIdParam]);

  const [api, setApi] = useState<CarouselApi>();
  const [current, setCurrent] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cover, setCover] = useState<CoverDetail | null>(null);
  const [images, setImages] = useState<MarkingImage[]>([]);
  const [reorderingImages, setReorderingImages] = useState(false);
  const [coverMarkingLink, setCoverMarkingLink] = useState<CoverMarkingLink | null>(null);
  const [associatedMarkings, setAssociatedMarkings] = useState<AssociatedMarkingOnCover[]>([]);
  const [markingsLoadError, setMarkingsLoadError] = useState<string | null>(null);
  const [citations, setCitations] = useState<EntryCitationItem[]>([]);
  const [historyEvents, setHistoryEvents] = useState<MarkingChangelogEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [coverReviewNotes, setCoverReviewNotes] = useState("");
  const [coverReviewError, setCoverReviewError] = useState<string | null>(null);
  const [coverReviewBusy, setCoverReviewBusy] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeReason, setRemoveReason] = useState("");
  const [removing, setRemoving] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [deletingImageId, setDeletingImageId] = useState<number | null>(null);
  const [linkMarkingOpen, setLinkMarkingOpen] = useState(false);
  const [linkMarkingInput, setLinkMarkingInput] = useState("");
  const [linkMarkingIsBackstamp, setLinkMarkingIsBackstamp] = useState(false);
  const [linkMarkingBusy, setLinkMarkingBusy] = useState(false);
  const [linkMarkingError, setLinkMarkingError] = useState<string | null>(null);
  const [moveImageIndex, setMoveImageIndex] = useState<number | null>(null);
  const [moveImageTargetMarkingId, setMoveImageTargetMarkingId] = useState("");
  const [moveImageView, setMoveImageView] = useState("FULL");
  const [moveImageBusy, setMoveImageBusy] = useState(false);
  const [moveImageError, setMoveImageError] = useState<string | null>(null);

  const isStaff =
    !!user &&
    (user.role === "editor" ||
      user.role === "administrator" ||
      user.is_superuser === true);

  const canViewHistory = useMemo(() => {
    if (!user) return false;
    return (
      user.role === "editor" ||
      user.role === "administrator" ||
      user.is_superuser === true
    );
  }, [user]);

  // A removed (recycle-binned) cover is hidden from the catalog, so its link is
  // no longer reviewable: hide approve/reject/return until it is restored.
  const canReviewCover =
    isStaff &&
    !cover?.isRemoved &&
    coverMarkingLink != null &&
    coverMarkingLink.reviewStatus === "pending";

  const refreshCoverMarkingLink = useCallback(async () => {
    if (coverPk == null) return;
    const { links } = await getCoverMarkingsByCover(coverPk);
    const link =
      markingId != null
        ? links.find((l) => l.markingId === markingId) ?? null
        : links[0] ?? null;
    setCoverMarkingLink(link);
  }, [coverPk, markingId]);

  const submitCoverReview = async (kind: CoverMarkingReviewActionApi) => {
    if (!coverMarkingLink) return;
    if (kind !== "approve" && !coverReviewNotes.trim()) {
      setCoverReviewError("A comment is required to reject or request revision.");
      return;
    }
    setCoverReviewError(null);
    setCoverReviewBusy(true);
    try {
      const res = await postCoverMarkingReview(
        coverMarkingLink.id,
        kind,
        coverReviewNotes.trim() || undefined,
      );
      if (!res.ok) {
        toast({
          title: "Could not update cover",
          description: "message" in res ? res.message : "Request failed.",
          variant: "destructive",
        });
        return;
      }
      toast({
        title: "Cover review saved",
        description:
          kind === "approve"
            ? "This cover link is now visible to everyone on the catalog record."
            : kind === "reject"
              ? "The contributor will see this cover as rejected on the record."
              : "The contributor can edit the cover and resubmit it for review.",
      });
      setCoverReviewNotes("");
      await refreshCoverMarkingLink();
    } finally {
      setCoverReviewBusy(false);
    }
  };

  // Re-fetch just the cover row so its is_removed/can_remove flags refresh after
  // a remove/restore action (the full page load is heavier than we need here).
  const reloadCover = useCallback(async () => {
    if (coverPk == null) return;
    const detail = await getCoverById(coverPk);
    if (detail) setCover(detail);
  }, [coverPk]);

  const handleRemoveConfirm = async () => {
    if (cover == null) return;
    setRemoving(true);
    try {
      const res = await removeCover(cover.id, removeReason.trim() || undefined);
      if (res.ok) {
        toast({ title: "Cover removed" });
        setRemoveOpen(false);
        setRemoveReason("");
        await reloadCover();
      } else {
        toast({
          title: "Could not remove",
          description: "message" in res ? res.message : "Could not remove cover.",
          variant: "destructive",
        });
      }
    } finally {
      setRemoving(false);
    }
  };

  const handleRestoreConfirm = async () => {
    if (cover == null) return;
    setRestoring(true);
    try {
      const res = await restoreCover(cover.id);
      if (res.ok) {
        toast({ title: "Cover restored" });
        setRestoreOpen(false);
        await reloadCover();
      } else {
        toast({
          title: "Could not restore",
          description: "message" in res ? res.message : "Could not restore cover.",
          variant: "destructive",
        });
      }
    } finally {
      setRestoring(false);
    }
  };

  const handleBack = () => {
    const associatedMarkingId = markingId ?? associatedMarkings[0]?.marking.id ?? null;
    if (associatedMarkingId != null) {
      navigate(`/record/${associatedMarkingId}`);
      return;
    }
    navigate("/search");
  };

  useEffect(() => {
    if (coverPk == null) {
      setError("Invalid cover ID");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      const [detail, imgs, linksResult, refWorks] = await Promise.all([
        getCoverById(coverPk),
        getImagesForSubject({ subjectType: "COVER", subjectId: coverPk }),
        getCoverMarkingsByCover(coverPk),
        getReferenceWorks(),
      ]);
      if (cancelled) return;
      if (!detail) {
        setCover(null);
        setImages([]);
        setAssociatedMarkings([]);
        setCitations([]);
        setCoverMarkingLink(null);
        setError("Cover not found");
        setLoading(false);
        return;
      }
      setCover(detail);
      setImages(imgs);
      setMarkingsLoadError(linksResult.error);

      const linkForMarking =
        markingId != null
          ? linksResult.links.find((l) => l.markingId === markingId) ?? null
          : linksResult.links[0] ?? null;
      setCoverMarkingLink(linkForMarking);

      const markings = await loadAssociatedMarkingsForCover(linksResult.links);
      if (!cancelled) setAssociatedMarkings(markings);

      const citationRows = await listCitationsForSubject({
        subjectType: "COVER",
        subjectId: coverPk,
      });
      const refById = new Map(refWorks.map((w) => [w.id, w]));
      const built: EntryCitationItem[] = citationRows.map((row) => {
        const rw = refById.get(row.referenceWorkId) ?? null;
        return {
          id: row.id,
          citationDetail: row.citationDetail,
          referenceWork: rw
            ? {
                code: rw.code,
                title: rw.title,
                authorship: rw.authorship,
                publisher: rw.publisher,
                publicationYear: rw.publicationYear,
                edition: rw.edition,
                volume: rw.volume,
                isbn: rw.isbn,
                url: rw.url,
              }
            : null,
        };
      });
      if (!cancelled) setCitations(built);
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [coverPk, markingId]);

  useEffect(() => {
    if (markingId == null || Number.isNaN(markingId) || !canViewHistory) {
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

  const applyImageOrder = useCallback(
    async (newImages: MarkingImage[]) => {
      if (coverPk == null || newImages.length === 0) return;
      setReorderingImages(true);
      setImages(
        newImages.map((img, idx) => ({
          ...img,
          displayOrder: idx,
        })),
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
        const refreshed = await getImagesForSubject({
          subjectType: "COVER",
          subjectId: coverPk,
        });
        setImages(refreshed);
      } finally {
        setReorderingImages(false);
      }
    },
    [coverPk, toast],
  );

  const moveImageBy = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (target < 0 || target >= images.length) return;
    const next = images.slice();
    [next[index], next[target]] = [next[target], next[index]];
    void applyImageOrder(next);
  };

  const setImageAsDefault = (index: number) => {
    if (index <= 0 || index >= images.length) return;
    const next = images.slice();
    const [picked] = next.splice(index, 1);
    next.unshift(picked);
    setCurrent(0);
    void applyImageOrder(next);
  };

  const handleDeleteImage = useCallback(
    async (index: number) => {
      if (coverPk == null) return;
      const image = images[index];
      if (!image || image.imageId <= 0) return;
      const label = image.originalFilename || `image ${index + 1}`;
      const confirmed = window.confirm(
        `Delete ${label}? This removes the image from this cover.`,
      );
      if (!confirmed) return;
      setDeletingImageId(image.imageId);
      try {
        const res = await deleteImage(image.imageId);
        if (res.ok) {
          toast({ title: "Image deleted" });
          const refreshed = await getImagesForSubject({
            subjectType: "COVER",
            subjectId: coverPk,
          });
          setImages(refreshed);
          setCurrent((prev) => Math.max(0, Math.min(prev, refreshed.length - 1)));
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
    },
    [coverPk, images, toast],
  );

  const requireAuth = (): boolean => {
    if (user) return true;
    navigate("/auth", { state: { from: location } });
    return false;
  };

  // Reassigns an image from this cover to one of its associated markings
  // (issue #48, reverse direction). Target list is restricted to markings
  // already linked to this cover.
  const handleMoveImageToMarking = async () => {
    if (coverPk == null || moveImageIndex == null) return;
    const image = images[moveImageIndex];
    if (!image || image.imageId <= 0) return;
    const targetId = parseInt(moveImageTargetMarkingId, 10);
    if (!Number.isFinite(targetId) || targetId <= 0) {
      setMoveImageError("Select a target marking.");
      return;
    }
    setMoveImageBusy(true);
    setMoveImageError(null);
    try {
      const res = await moveImageSubject(image.imageId, "MARKING", targetId, moveImageView);
      if (res.ok === false) {
        setMoveImageError(res.message);
        return;
      }
      toast({ title: "Image moved", description: "Image reassigned to the marking." });
      setMoveImageIndex(null);
      const refreshed = await getImagesForSubject({
        subjectType: "COVER",
        subjectId: coverPk,
      });
      setImages(refreshed);
      setCurrent((prev) => Math.max(0, Math.min(prev, refreshed.length - 1)));
    } finally {
      setMoveImageBusy(false);
    }
  };

  // Creates a CoverMarking junction row between this cover and an
  // already-existing marking. The endpoint is editor/admin-gated
  // (IsEditorOrAdminWrite), so the button only renders for isStaff.
  const handleLinkExistingMarking = async () => {
    if (coverPk == null) return;
    const markingIdTarget = parseMarkingIdInput(linkMarkingInput);
    if (markingIdTarget == null) {
      setLinkMarkingError("Enter a valid marking ID.");
      return;
    }
    setLinkMarkingBusy(true);
    setLinkMarkingError(null);
    try {
      const marking = await getMarkingById(markingIdTarget);
      if (!marking) {
        setLinkMarkingError(`Marking ${markingIdTarget} not found.`);
        return;
      }
      await createCoverMarking({
        cover: coverPk,
        marking: markingIdTarget,
        is_backstamp: linkMarkingIsBackstamp,
      });
      toast({
        title: "Marking linked",
        description: `Marking ${marking.code ?? markingIdTarget} is now linked to this cover.`,
      });
      setLinkMarkingOpen(false);
      setLinkMarkingInput("");
      setLinkMarkingIsBackstamp(false);
      const linksResult = await getCoverMarkingsByCover(coverPk);
      const linkForMarking =
        markingId != null
          ? linksResult.links.find((l) => l.markingId === markingId) ?? null
          : linksResult.links[0] ?? null;
      setCoverMarkingLink(linkForMarking);
      const markings = await loadAssociatedMarkingsForCover(linksResult.links);
      setAssociatedMarkings(markings);
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: string; non_field_errors?: string[] } } };
      const detail = ax.response?.data?.detail ?? ax.response?.data?.non_field_errors?.[0];
      setLinkMarkingError(
        typeof detail === "string" ? detail : "Could not link marking. It may already be linked.",
      );
    } finally {
      setLinkMarkingBusy(false);
    }
  };

  const openEditCover = () => {
    if (markingId == null || coverPk == null) return;
    if (!requireAuth()) return;
    navigate(`/record/${markingId}/cover/${coverPk}/edit`, {
      state: { from: location.pathname + location.search },
    });
  };

  const goMarkingView = (markingIdTarget: number) => {
    navigate(`/record/${markingIdTarget}`, {
      state: { from: location.pathname + location.search },
    });
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

  if (error || !cover || coverPk == null) {
    return (
      <div className="min-h-screen flex flex-col">
        <Navigation />
        <div className="flex-1 flex flex-col items-center justify-center gap-4 px-4">
          <p className="text-muted-foreground text-center">
            {error || "Cover not found"}
          </p>
          <Button variant="outline" onClick={handleBack}>
            Back
          </Button>
        </div>
        <Footer />
      </div>
    );
  }

  const galleryImages = buildCoverGalleryImages(images);
  const canManageImages = isStaff && !cover.isRemoved;
  // A removed (recycle-binned) cover is read-only: no edits until it is restored
  // (mirrors the marking record UI).
  const canSubmitEdit =
    user != null && markingId != null && coverPk != null && !cover.isRemoved;

  const datesText =
    cover.datesSeen.length > 0
      ? cover.datesSeen.map(formatCoverDate).filter(Boolean).join("\n")
      : EMPTY;

  const institutionalText =
    cover.isInstitutional === true
      ? "Yes"
      : cover.isInstitutional === false
        ? "No"
        : EMPTY;

  const backstampText = coverMarkingLink?.isBackstamp ? "Yes" : "No";

  // Count every associated marking, not just ratemarks: the header is labeled
  // "Associated Markings" and the list below renders all of them.
  const associatedMarkingCount = associatedMarkings.length;

  return (
    <>
      <EntryDetailLayout
        onBack={handleBack}
        leftColumn={
          <>
            <EntryImageGalleryCard
              images={galleryImages}
              showSubjectBadge={false}
              carouselApi={api}
              setCarouselApi={setApi}
              currentIndex={current}
              canSetDefaultImage={canManageImages}
              settingDefaultImage={reorderingImages}
              onSetDefaultImage={setImageAsDefault}
            />
            <EntryAssociatedThumbnailsCard
              images={galleryImages}
              carouselApi={api}
              currentIndex={current}
              emptyMessage="No images linked to this cover yet."
              canReorder={canManageImages && galleryImages.length > 1}
              reorderingImages={reorderingImages}
              deletingImageId={deletingImageId}
              onMoveBy={moveImageBy}
              onSetDefault={setImageAsDefault}
              onDeleteImage={canManageImages ? handleDeleteImage : undefined}
              onMoveImage={
                canManageImages && !cover.isRemoved && associatedMarkings.length > 0
                  ? (index) => {
                      setMoveImageIndex(index);
                      setMoveImageTargetMarkingId(
                        String(associatedMarkings[0]?.marking.id ?? ""),
                      );
                      setMoveImageView("FULL");
                      setMoveImageError(null);
                    }
                  : undefined
              }
              moveImageLabel="Move to marking"
            />
            {canViewHistory && (
              <EntryRecordHistoryCard
                loading={historyLoading}
                error={historyError}
                events={historyEvents}
                expanded={historyExpanded}
                onToggleExpanded={() => setHistoryExpanded((v) => !v)}
                unavailableMessage={
                  markingId == null
                    ? "Open this cover from a marking record to view audit history."
                    : undefined
                }
              />
            )}
          </>
        }
        rightColumn={
          <>
            {cover.isRemoved && (
              <div className="flex items-center gap-2 rounded-md border border-muted bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
                <Info className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>This cover has been marked for removal.</span>
              </div>
            )}
            <Card className="shadow-archival-md">
              <CardHeader>
                <div className="flex items-center justify-between gap-3">
                  <CardTitle className="font-heading text-lg">Cover Details</CardTitle>
                  {canSubmitEdit && (
                    <Button variant="outline" size="sm" onClick={openEditCover}>
                      <Pencil className="mr-2 h-4 w-4" />
                      {SUBMISSION_LABELS.action.submitEditToCover}
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                {coverMarkingLink && coverMarkingLink.reviewStatus !== "approved" && (
                  <div className="mb-4 space-y-2">
                    <Badge
                      variant={
                        coverMarkingLink.reviewStatus === "pending"
                          ? "secondary"
                          : coverMarkingLink.reviewStatus === "needs_revision"
                            ? "outline"
                            : "destructive"
                      }
                      className="font-normal"
                    >
                      {coverLinkReviewBadgeLabel(coverMarkingLink.reviewStatus)}
                    </Badge>
                    {(coverMarkingLink.reviewStatus === "needs_revision" ||
                      coverMarkingLink.reviewStatus === "rejected") &&
                      (coverMarkingLink.reviewNotes ?? "").trim().length > 0 && (
                        <p className="text-sm text-muted-foreground whitespace-pre-wrap border-l-2 border-border pl-2">
                          <span className="font-medium text-foreground">Editor note: </span>
                          {coverMarkingLink.reviewNotes}
                        </p>
                      )}
                  </div>
                )}
                <CoverRecordDetailFields
                  type={coverTypeLabel(cover.type)}
                  date={datesText}
                  institutionallyOwned={institutionalText}
                  backstamp={backstampText}
                  submittedBy={cover.submitterName}
                  description={cover.description}
                />
                {cover.canRemove && (
                  <div className="mt-4 pt-4 border-t border-border flex justify-end">
                    {cover.isRemoved ? (
                      <Button variant="outline" size="sm" onClick={() => setRestoreOpen(true)}>
                        <Trash2 className="mr-2 h-4 w-4" />
                        Restore Cover
                      </Button>
                    ) : (
                      <Button variant="destructive" size="sm" onClick={() => setRemoveOpen(true)}>
                        <Trash2 className="mr-2 h-4 w-4" />
                        Remove Cover
                      </Button>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            {canReviewCover && (
              <Card className="shadow-archival-lg border-primary/20">
                <CardHeader>
                  <CardTitle className="font-heading text-lg">Review this cover</CardTitle>
                  <p className="text-sm text-muted-foreground">
                    Choose Approve, Reject, or Return.
                  </p>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="cover-detail-review-notes">Comment</Label>
                    <Textarea
                      id="cover-detail-review-notes"
                      placeholder="Optional for approvals, required for rejection/revision."
                      rows={4}
                      value={coverReviewNotes}
                      onChange={(e) => {
                        setCoverReviewNotes(e.target.value);
                        if (coverReviewError && e.target.value.trim()) setCoverReviewError(null);
                      }}
                      disabled={coverReviewBusy}
                      className={`resize-none ${coverReviewError ? "border-destructive" : ""}`}
                    />
                    {coverReviewError ? (
                      <p className="text-sm text-destructive">{coverReviewError}</p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-2 pt-2">
                    <Button
                      type="button"
                      onClick={() => void submitCoverReview("approve")}
                      disabled={coverReviewBusy}
                      className="bg-green-600 hover:bg-green-700"
                    >
                      <CheckCircle className="mr-2 h-4 w-4" />
                      {coverReviewBusy ? "Submitting..." : "Approve"}
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      onClick={() => void submitCoverReview("reject")}
                      disabled={coverReviewBusy}
                    >
                      <XCircle className="mr-2 h-4 w-4" />
                      Reject
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void submitCoverReview("request-revision")}
                      disabled={coverReviewBusy}
                    >
                      <MessageSquare className="mr-2 h-4 w-4" />
                      Return
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            <Card className="shadow-archival-md">
              <CardHeader>
                <div className="flex items-center justify-between gap-3">
                  <CardTitle className="font-heading text-lg">
                    Associated Markings ({associatedMarkingCount})
                  </CardTitle>
                  {isStaff && !cover.isRemoved && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setLinkMarkingInput("");
                        setLinkMarkingIsBackstamp(false);
                        setLinkMarkingError(null);
                        setLinkMarkingOpen(true);
                      }}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Link Existing Marking
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-4 pt-0">
                {markingsLoadError && (
                  <p className="text-sm text-destructive rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2">
                    {markingsLoadError}
                  </p>
                )}
                {associatedMarkings.length === 0 && !markingsLoadError ? (
                  <p className="text-sm text-muted-foreground">No markings linked to this cover yet.</p>
                ) : (
                  <div className="space-y-4">
                    {associatedMarkings.map(({ marking, defaultImageUrl }) => (
                      <AssociatedMarkingPreviewCard
                        key={marking.id}
                        marking={marking}
                        defaultImageUrl={defaultImageUrl}
                        onOpenMarking={() => goMarkingView(marking.id)}
                      />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <EntryCitationsCard
              citations={citations}
              emptyMessage="No citations linked to this cover yet."
            />
          </>
        }
      />

      <Dialog
        open={moveImageIndex != null}
        onOpenChange={(open) => {
          if (moveImageBusy) return;
          if (!open) setMoveImageIndex(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Move Image to Marking</DialogTitle>
            <DialogDescription>
              Reassign this image from the cover to one of its associated markings.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1">
              <Label htmlFor="move-img-marking-id">Target marking</Label>
              <Select
                value={moveImageTargetMarkingId}
                onValueChange={(v) => {
                  setMoveImageTargetMarkingId(v);
                  setMoveImageError(null);
                }}
                disabled={moveImageBusy}
              >
                <SelectTrigger id="move-img-marking-id">
                  <SelectValue placeholder="Select a marking…" />
                </SelectTrigger>
                <SelectContent>
                  {associatedMarkings.map(({ marking }) => (
                    <SelectItem key={marking.id} value={String(marking.id)}>
                      {marking.code ?? `Marking #${marking.id}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="move-img-marking-view">Image view</Label>
              <Select
                value={moveImageView}
                onValueChange={(v) => setMoveImageView(v)}
                disabled={moveImageBusy}
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
            {moveImageError && <p className="text-sm text-destructive">{moveImageError}</p>}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setMoveImageIndex(null)}
              disabled={moveImageBusy}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleMoveImageToMarking()}
              disabled={moveImageBusy || !moveImageTargetMarkingId}
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
        open={linkMarkingOpen}
        onOpenChange={(open) => {
          if (linkMarkingBusy) return;
          setLinkMarkingOpen(open);
          if (!open) {
            setLinkMarkingInput("");
            setLinkMarkingIsBackstamp(false);
            setLinkMarkingError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Link Existing Marking</DialogTitle>
            <DialogDescription>
              Enter the marking ID to link it to this cover.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Input
              placeholder="Marking ID"
              value={linkMarkingInput}
              onChange={(e) => {
                setLinkMarkingInput(e.target.value);
                setLinkMarkingError(null);
              }}
              disabled={linkMarkingBusy}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleLinkExistingMarking();
                }
              }}
              autoFocus
            />
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={linkMarkingIsBackstamp}
                onCheckedChange={(value) => setLinkMarkingIsBackstamp(value === true)}
                disabled={linkMarkingBusy}
              />
              Backstamp
            </label>
            {linkMarkingError && <p className="text-sm text-destructive">{linkMarkingError}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLinkMarkingOpen(false)} disabled={linkMarkingBusy}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleLinkExistingMarking()}
              disabled={linkMarkingBusy || !linkMarkingInput.trim()}
            >
              {linkMarkingBusy ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Linking…
                </>
              ) : (
                "Link Marking"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={removeOpen} onOpenChange={(open) => !removing && setRemoveOpen(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove Cover</DialogTitle>
            <DialogDescription>
              This moves the cover to the recycle bin and hides it from the public catalog.
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
              {removing ? "Removing..." : "Remove Cover"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={restoreOpen} onOpenChange={(open) => !restoring && setRestoreOpen(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restore Cover</AlertDialogTitle>
            <AlertDialogDescription>
              This returns the cover to the public catalog.
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
              {restoring ? "Restoring..." : "Restore Cover"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
};

export default CoverDetailPage;
