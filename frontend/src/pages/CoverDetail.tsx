import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { CheckCircle, Info, Loader2, MessageSquare, Pencil, Trash2, XCircle } from "lucide-react";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  getMarkingChangelog,
  getCoverMarkingsByCover,
  loadAssociatedMarkingsForCover,
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
  getCoverById,
  removeCover,
  restoreCover,
  type CoverDetail,
  type CoverDateSeenItem,
} from "@/services/covers";
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
  return formatDateSeen(d.date, d.granularity) || d.date || "";
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
        toast({ title: "Could not remove", description: res.message, variant: "destructive" });
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
        toast({ title: "Could not restore", description: res.message, variant: "destructive" });
      }
    } finally {
      setRestoring(false);
    }
  };

  const handleBack = () => {
    if (state?.fromDashboard) {
      navigate("/dashboard", { state: { tab: state.dashboardTab ?? "submissions" } });
      return;
    }
    if (state?.from) {
      navigate(state.from);
      return;
    }
    if (markingId != null) {
      navigate(`/record/${markingId}`);
      return;
    }
    navigate(-1);
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
            "Unable to load record history (you may not be assigned to this region).",
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
    void applyImageOrder(next);
  };

  const requireAuth = (): boolean => {
    if (user) return true;
    navigate("/auth", { state: { from: location } });
    return false;
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
            />
            <EntryAssociatedThumbnailsCard
              images={galleryImages}
              carouselApi={api}
              currentIndex={current}
              emptyMessage="No images linked to this cover yet."
              canReorder={isStaff && !cover.isRemoved}
              reorderingImages={reorderingImages}
              onMoveBy={moveImageBy}
              onSetDefault={setImageAsDefault}
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
                  <CardTitle className="font-heading text-lg">Record Details</CardTitle>
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
                <CardTitle className="font-heading text-lg">
                  Associated Markings ({associatedMarkingCount})
                </CardTitle>
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
