import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { CheckCircle, ExternalLink, Loader2, MessageSquare, Pencil, Trash2, XCircle } from "lucide-react";
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
import { AssociatedMarkingPreviewCard } from "@/components/entry-detail/AssociatedMarkingPreviewCard";
import { EntryAssociatedThumbnailsCard } from "@/components/entry-detail/EntryAssociatedThumbnailsCard";
import { EntryCitationsCard, type EntryCitationItem } from "@/components/entry-detail/EntryCitationsCard";
import { EntryDetailLayout } from "@/components/entry-detail/EntryDetailLayout";
import { EntryImageGalleryCard } from "@/components/entry-detail/EntryImageGalleryCard";
import { CoverRecordDetailFields } from "@/components/entry-detail/CoverRecordDetailFields";
import type { EntryGalleryImage } from "@/components/entry-detail/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type CarouselApi } from "@/components/ui/carousel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/hooks/useAuth";
import { useToast } from "@/hooks/use-toast";
import {
  coverContributionDisplayName,
  isCoverContributionData,
  parentMarkingIdFromContribution,
} from "@/lib/contributionDisplay";
import {
  formatPartialDateInput,
  partialDateInputFromSubmittedData,
} from "@/lib/partialDate";
import {
  contributionImageMetasFromSubmittedData,
  contributionMetaImageUrl,
} from "@/lib/contributionImages";
import {
  type Contribution,
  decideContribution,
  deleteOwnContribution,
  getContribution,
  getContributionCatalogCodeSuggestion,
} from "@/services/contributions";
import { getMarkingById, type MarkingRecord } from "@/services/markings";
import { getReferenceWorks, type ReferenceWorkRecord } from "@/services/referenceWorks";

const COVER_TYPE_LABELS: Record<string, string> = {
  FC: "Folded Cover",
  FL: "Folded Letter",
};

type ReferenceDetailInput = {
  pageNumber: string;
  citationUrl: string;
};

function boolLabel(raw: unknown): string {
  if (raw === true || raw === "true" || raw === "1" || raw === 1) return "Yes";
  if (raw === false || raw === "false" || raw === "0" || raw === 0) return "No";
  return "--";
}

function coverTypeLabel(typeCode: string): string {
  const code = typeCode.trim().toUpperCase();
  return COVER_TYPE_LABELS[code] || code || "--";
}

function formatCoverDate(sd: Record<string, unknown>): string {
  return formatPartialDateInput(partialDateInputFromSubmittedData(sd)) || "--";
}

function tracingFlagsFromSubmittedData(sd: Record<string, unknown>, imageCount: number): boolean[] {
  const raw = sd.cover_image_tags ?? sd.coverImageTags;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.map((tag) => String(tag).toLowerCase() === "tracing");
      }
    } catch {
      /* ignore */
    }
  }
  if (Array.isArray(raw)) {
    return raw.map((tag) => String(tag).toLowerCase() === "tracing");
  }
  return Array.from({ length: imageCount }, () => false);
}

function parseReferenceWorkIds(raw: unknown): number[] {
  const values: unknown[] = [];
  if (Array.isArray(raw)) {
    values.push(...raw);
  } else if (raw != null) {
    values.push(raw);
  }

  const parsed: number[] = [];
  const seen = new Set<number>();

  for (const value of values) {
    if (Array.isArray(value)) {
      value.forEach((v) => values.push(v));
      continue;
    }
    const text = String(value ?? "").trim();
    if (!text) continue;
    if (text.startsWith("[") && text.endsWith("]")) {
      try {
        const json = JSON.parse(text) as unknown;
        if (Array.isArray(json)) {
          json.forEach((v) => values.push(v));
          continue;
        }
      } catch {
        /* ignore malformed legacy payloads */
      }
    }
    if (text.includes(",")) {
      text.split(",").forEach((chunk) => values.push(chunk.trim()));
      continue;
    }
    const id = Number.parseInt(text, 10);
    if (Number.isNaN(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    parsed.push(id);
  }

  return parsed;
}

function parseReferenceWorkDetails(raw: unknown): Record<number, ReferenceDetailInput> {
  if (raw == null) return {};
  let list: unknown = raw;
  if (typeof raw === "string") {
    const text = raw.trim();
    if (!text) return {};
    try {
      list = JSON.parse(text);
    } catch {
      return {};
    }
  }
  if (!Array.isArray(list)) {
    if (list && typeof list === "object") {
      list = Object.entries(list as Record<string, unknown>).map(([id, detail]) => ({
        reference_work_id: id,
        ...(detail && typeof detail === "object" ? detail : {}),
      }));
    } else {
      return {};
    }
  }

  const out: Record<number, ReferenceDetailInput> = {};
  for (const row of list as unknown[]) {
    if (!row || typeof row !== "object") continue;
    const rec = row as Record<string, unknown>;
    const id = Number.parseInt(String(rec.reference_work_id ?? rec.referenceWorkId ?? ""), 10);
    if (Number.isNaN(id) || id <= 0) continue;
    out[id] = {
      pageNumber: String(rec.page_number ?? rec.pageNumber ?? "").trim(),
      citationUrl: String(rec.url ?? "").trim(),
    };
  }
  return out;
}

interface CoverContributionDetailProps {
  initialContribution?: Contribution | null;
}

export default function CoverContributionDetail({ initialContribution = null }: CoverContributionDetailProps) {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { toast } = useToast();
  const user = useAuth();

  const [contribution, setContribution] = useState<Contribution | null>(initialContribution);
  const [loading, setLoading] = useState(!initialContribution);
  const [error, setError] = useState<string | null>(null);
  const [carouselApi, setCarouselApi] = useState<CarouselApi>();
  const [carouselCurrent, setCarouselCurrent] = useState(0);
  const [comment, setComment] = useState("");
  const [commentError, setCommentError] = useState<string | null>(null);
  const [catalogCode, setCatalogCode] = useState("");
  const [catalogCodeError, setCatalogCodeError] = useState<string | null>(null);
  const [catalogCodeLoading, setCatalogCodeLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // Withdraw/delete: the contributor may delete any of their own UNapproved
  // cover submissions (backend IsOwnDeletableContribution on DELETE).
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [parentMarking, setParentMarking] = useState<MarkingRecord | null>(null);
  const [parentMarkingError, setParentMarkingError] = useState<string | null>(null);
  const [referenceWorks, setReferenceWorks] = useState<ReferenceWorkRecord[]>([]);

  const locationState = location.state as {
    fromDashboard?: boolean;
    dashboardTab?: "submissions" | "editor";
  } | null;
  const fromDashboard = locationState?.fromDashboard === true;
  const dashboardTab = locationState?.dashboardTab;
  const isStateEditor =
    user?.role === "editor" || user?.role === "administrator" || user?.is_superuser === true;

  useEffect(() => {
    if (initialContribution) {
      setContribution(initialContribution);
      setLoading(false);
      return;
    }
    const contributionId = id ? Number.parseInt(String(id), 10) : null;
    if (!contributionId || Number.isNaN(contributionId)) {
      setError("Invalid contribution");
      setLoading(false);
      return;
    }

    let cancelled = false;
    getContribution(contributionId)
      .then((normalized) => {
        if (cancelled) return;
        if (!isCoverContributionData(normalized.submittedData)) {
          throw new Error("Not a cover submission");
        }
        setContribution(normalized);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, initialContribution]);

  useEffect(() => {
    if (!carouselApi) return;
    setCarouselCurrent(carouselApi.selectedScrollSnap());
    const onSelect = () => setCarouselCurrent(carouselApi.selectedScrollSnap());
    carouselApi.on("select", onSelect);
    return () => {
      carouselApi.off("select", onSelect);
    };
  }, [carouselApi]);

  const sd = useMemo<Record<string, unknown>>(
    () => contribution?.submittedData ?? {},
    [contribution?.submittedData],
  );
  const parentMarkingId = parentMarkingIdFromContribution(sd);
  const displayName =
    contribution?.displayName?.trim() ||
    coverContributionDisplayName(sd, contribution?.id ?? 0, contribution?.status);

  useEffect(() => {
    let cancelled = false;
    setParentMarking(null);
    setParentMarkingError(null);
    setReferenceWorks([]);

    void (async () => {
      const [marking, refs] = await Promise.all([
        parentMarkingId != null ? getMarkingById(parentMarkingId) : Promise.resolve(null),
        getReferenceWorks().catch(() => []),
      ]);
      if (cancelled) return;
      setParentMarking(marking);
      setReferenceWorks(refs);
      if (parentMarkingId != null && marking == null) {
        setParentMarkingError("Could not load the parent marking preview.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [parentMarkingId]);

  const images = useMemo(() => {
    const metas = contributionImageMetasFromSubmittedData(sd);
    const tracingFlags = tracingFlagsFromSubmittedData(sd, metas.length);
    return metas
      .map((meta, index) => {
        const imageUrl = contributionMetaImageUrl(meta);
        if (!imageUrl) return null;
        const obj = meta && typeof meta === "object" ? (meta as Record<string, unknown>) : {};
        const originalFilename =
          typeof obj.original_filename === "string"
            ? obj.original_filename
            : typeof obj.originalFilename === "string"
              ? obj.originalFilename
              : undefined;
        return {
          imageUrl,
          originalFilename,
          isTracing: tracingFlags[index] === true,
        };
      })
      .filter((row): row is NonNullable<typeof row> => row != null);
  }, [sd]);

  const galleryImages = useMemo<EntryGalleryImage[]>(
    () =>
      images.map((img, index) => ({
        imageUrl: img.imageUrl,
        originalFilename: img.originalFilename,
        isTracing: img.isTracing,
        isDefault: index === 0,
        imageId: null,
      })),
    [images],
  );

  const citations = useMemo<EntryCitationItem[]>(() => {
    const ids = parseReferenceWorkIds(
      sd.reference_work_ids ?? sd.referenceWorkIds ?? sd["reference_work_ids[]"],
    );
    const detailsById = parseReferenceWorkDetails(
      sd.reference_work_details ?? sd.referenceWorkDetails,
    );
    const citationIds =
      ids.length > 0
        ? ids
        : Object.keys(detailsById)
            .map((rawId) => Number.parseInt(rawId, 10))
            .filter((value) => Number.isFinite(value) && value > 0);
    const refsById = new Map(referenceWorks.map((row) => [row.id, row]));

    return citationIds.map((refId, index) => {
      const detail = detailsById[refId];
      const referenceWork = refsById.get(refId) ?? null;
      return {
        id: refId * 1000 + index,
        citationDetail: detail?.pageNumber || detail?.citationUrl || "",
        referenceWork: referenceWork
          ? {
              code: referenceWork.code,
              title: referenceWork.title,
              authorship: referenceWork.authorship,
              publisher: referenceWork.publisher,
              publicationYear: referenceWork.publicationYear,
              edition: referenceWork.edition,
              volume: referenceWork.volume,
              isbn: referenceWork.isbn,
              url: referenceWork.url,
            }
          : null,
      };
    });
  }, [referenceWorks, sd]);

  const isContributor =
    !!user &&
    !!contribution &&
    (contribution.contributorId === user.id ||
      contribution.contributorUsername === user.username ||
      contribution.contributorUsername === user.email);

  const handleBack = () => {
    if (fromDashboard) {
      navigate("/dashboard", { state: { tab: dashboardTab ?? (isStateEditor ? "editor" : "submissions") } });
      return;
    }
    navigate("/dashboard");
  };

  const contributionId = contribution?.id;
  const contributionStatus = contribution?.status;

  useEffect(() => {
    if (contributionId == null || contributionStatus !== "pending" || !isStateEditor || !user) return;
    let cancelled = false;
    setCatalogCodeLoading(true);
    setCatalogCodeError(null);
    getContributionCatalogCodeSuggestion(contributionId)
      .then((suggestion) => {
        if (!cancelled) setCatalogCode(suggestion.catalogCode);
      })
      .catch((err) => {
        if (!cancelled) {
          setCatalogCodeError(err instanceof Error ? err.message : "Could not generate catalog code.");
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogCodeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contributionId, contributionStatus, isStateEditor, user]);

  const ensureCatalogCode = async (): Promise<string> => {
    if (!contribution) return "";
    const current = catalogCode.trim();
    if (current) return current;
    setCatalogCodeLoading(true);
    setCatalogCodeError(null);
    try {
      const suggestion = await getContributionCatalogCodeSuggestion(contribution.id, { force: true });
      setCatalogCode(suggestion.catalogCode);
      return suggestion.catalogCode;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not generate catalog code.";
      setCatalogCodeError(message);
      throw err;
    } finally {
      setCatalogCodeLoading(false);
    }
  };

  const submitDecision = async (kind: "approve" | "reject" | "revision") => {
    if (!contribution) return;
    if (kind !== "approve" && !comment.trim()) {
      setCommentError("A comment is required to reject or request revision.");
      return;
    }
    setCommentError(null);

    setSubmitting(true);
    try {
      const finalCatalogCode = kind === "approve" ? await ensureCatalogCode() : "";
      const result = await decideContribution(contribution.id, kind, {
        reviewNotes: comment.trim() || undefined,
        ...(kind === "approve" ? { catalogCode: finalCatalogCode } : {}),
      });
      const actionLabel = kind === "approve" ? "Approved" : kind === "reject" ? "Rejected" : "Submission returned";
      toast({ title: actionLabel, description: "Your comment was saved for the contributor." });
      if (kind === "approve" && result.coverId != null) {
        if (parentMarkingId != null) {
          navigate(`/record/${parentMarkingId}/cover/${result.coverId}`, {
            state: { fromDashboard: true, dashboardTab: dashboardTab ?? "editor" },
          });
          return;
        }
        navigate(`/covers/${result.coverId}`, {
          state: { fromDashboard: true, dashboardTab: dashboardTab ?? "editor" },
        });
        return;
      }
      navigate("/dashboard", { state: { tab: "editor" } });
    } catch (err) {
      toast({
        title: "Could not submit",
        description: err instanceof Error ? err.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col">
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  if (error || !contribution) {
    return (
      <EntryDetailLayout
        onBack={handleBack}
        leftColumn={null}
        rightColumn={
          <Card className="shadow-archival-md">
            <CardContent className="pt-6">
              <p className="text-muted-foreground">{error || "Contribution not found"}</p>
            </CardContent>
          </Card>
        }
      />
    );
  }

  const normalizedStatus = String(contribution.status || "").toLowerCase();
  const statusLabel =
    normalizedStatus === "approved"
      ? "Approved"
      : normalizedStatus === "rejected"
        ? "Rejected"
        : normalizedStatus === "needs_revision"
          ? "Needs Revision"
          : normalizedStatus === "draft"
            ? "Draft"
            : "Pending";
  const statusBadgeClassName =
    normalizedStatus === "approved"
      ? "rounded-full border border-green-700 bg-green-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-green-600"
      : normalizedStatus === "rejected"
        ? "rounded-full border border-red-700 bg-red-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-red-600"
        : normalizedStatus === "needs_revision"
          ? "rounded-full border border-orange-600 bg-orange-500 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-orange-500"
          : normalizedStatus === "draft"
            ? "rounded-full border border-amber-900 bg-amber-800 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-amber-800"
            : "rounded-full border border-yellow-600 bg-yellow-500 px-3 py-1 text-xs font-semibold text-black shadow-sm hover:bg-yellow-500";

  const isPending = normalizedStatus === "pending";
  const canReview = isStateEditor && isPending && !!user;
  const canContributorEdit =
    isContributor &&
    (normalizedStatus === "pending" ||
      normalizedStatus === "rejected" ||
      normalizedStatus === "needs_revision");
  const canDeleteOwn = isContributor && normalizedStatus !== "approved";

  const handleDeleteConfirm = async () => {
    if (!contribution) return;
    setDeleting(true);
    try {
      await deleteOwnContribution(contribution.id);
      toast({
        title: "Submission deleted",
        description: "Your cover submission has been removed.",
      });
      navigate("/dashboard", {
        state: { tab: dashboardTab ?? (isStateEditor ? "editor" : "submissions") },
      });
    } catch (err) {
      toast({
        title: "Could not delete submission",
        description: err instanceof Error ? err.message : "Please try again.",
        variant: "destructive",
      });
      setDeleting(false);
      setDeleteOpen(false);
    }
  };
  const contributorComment = String(
    sd.contributor_comment ??
      sd.contributorComment ??
      sd.comment_for_editor ??
      sd.commentForEditor ??
      "",
  ).trim();
  const typeCode = String(sd.type ?? "").trim().toUpperCase();
  const showReviewNotes = !!contribution.reviewNotes?.trim();
  const showEditorFeedbackCard =
    contribution.status !== "pending" || !!(contribution.reviewNotes && contribution.reviewNotes.trim());
  const associatedMarkingCount = parentMarkingId != null ? 1 : 0;

  const openParentMarking = () => {
    if (parentMarkingId == null) return;
    navigate(`/record/${parentMarkingId}`, {
      state: { from: location.pathname + location.search },
    });
  };

  const openApprovedCover = () => {
    if (contribution.coverId == null) return;
    const path =
      parentMarkingId != null
        ? `/record/${parentMarkingId}/cover/${contribution.coverId}`
        : `/covers/${contribution.coverId}`;
    navigate(path, {
      state: { from: location.pathname + location.search },
    });
  };

  return (
    <>
    <EntryDetailLayout
      onBack={handleBack}
      leftColumn={(
        <>
          <EntryImageGalleryCard
            images={galleryImages}
            showSubjectBadge={false}
            carouselApi={carouselApi}
            setCarouselApi={setCarouselApi}
            currentIndex={carouselCurrent}
          />
          <EntryAssociatedThumbnailsCard
            images={galleryImages}
            carouselApi={carouselApi}
            currentIndex={carouselCurrent}
            emptyMessage="No images linked to this cover submission yet."
          />
          {canReview && (
            <Card className="shadow-archival-lg border-primary/20">
              <CardHeader>
                <CardTitle className="font-heading text-lg">Review this submission</CardTitle>
                <p className="text-sm text-muted-foreground">
                  Choose Approve, Reject, or Return.
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="cover-contribution-catalog-code">Catalog code</Label>
                  <Input
                    id="cover-contribution-catalog-code"
                    value={catalogCode}
                    onChange={(e) => {
                      setCatalogCode(e.target.value);
                      if (catalogCodeError) setCatalogCodeError(null);
                    }}
                    onBlur={() => {
                      if (!catalogCode.trim()) void ensureCatalogCode();
                    }}
                    placeholder={catalogCodeLoading ? "Generating..." : "Catalog code"}
                    disabled={submitting || catalogCodeLoading}
                  />
                  {catalogCodeError ? <p className="text-sm text-destructive">{catalogCodeError}</p> : null}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="cover-contribution-comment">Comment</Label>
                  <Textarea
                    id="cover-contribution-comment"
                    placeholder="Optional for approvals, required for rejection/revision."
                    rows={4}
                    value={comment}
                    onChange={(e) => {
                      setComment(e.target.value);
                      if (commentError && e.target.value.trim()) setCommentError(null);
                    }}
                    disabled={submitting}
                    className={`resize-none ${commentError ? "border-destructive" : ""}`}
                  />
                  {commentError ? <p className="text-sm text-destructive">{commentError}</p> : null}
                </div>
                <div className="flex flex-wrap gap-2 pt-2">
                  <Button
                    type="button"
                    onClick={() => void submitDecision("approve")}
                    disabled={submitting}
                    className="bg-green-600 hover:bg-green-700"
                  >
                    <CheckCircle className="mr-2 h-4 w-4" />
                    {submitting ? "Submitting..." : "Approve"}
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    onClick={() => void submitDecision("reject")}
                    disabled={submitting}
                  >
                    <XCircle className="mr-2 h-4 w-4" />
                    Reject
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void submitDecision("revision")}
                    disabled={submitting}
                  >
                    <MessageSquare className="mr-2 h-4 w-4" />
                    Return
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
          {showEditorFeedbackCard ? (
            <Card className="shadow-archival-md border-amber-500/20 bg-amber-500/5">
              <CardHeader>
                <CardTitle className="font-heading text-lg flex items-center gap-2">
                  <MessageSquare className="h-5 w-5 text-amber-600" />
                  Editor feedback
                </CardTitle>
                <p className="text-sm text-muted-foreground">
                  {contribution.status === "approved"
                    ? "Your submission was approved and added to the catalog. If the editor left a comment below, use it as guidance for future submissions."
                    : contribution.status === "rejected"
                      ? "Your submission was not accepted. See the comment below for details."
                      : contribution.status === "needs_revision"
                        ? "The editor requested changes. Please update this submission and resubmit."
                        : "The reviewer left a comment for you. Use this feedback to improve your submission or add a new cover if requested."}
                </p>
              </CardHeader>
              <CardContent className="space-y-3">
                {showReviewNotes ? (
                  <p className="text-sm text-foreground leading-relaxed whitespace-pre-line">
                    {contribution.reviewNotes?.trim()}
                  </p>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </>
      )}
      rightColumn={(
        <>
          <Card className="shadow-archival-md">
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle className="font-heading text-lg">Cover Details</CardTitle>
                <div className="flex flex-wrap items-center gap-2">
                  {canContributorEdit && parentMarkingId != null && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        navigate(`/record/${parentMarkingId}/cover/new?edit=${contribution.id}`, {
                          state: { from: location.pathname + location.search },
                        })
                      }
                      disabled={submitting || deleting}
                    >
                      <Pencil className="mr-2 h-4 w-4" />
                      {normalizedStatus === "pending" ? "Edit" : "Edit before resubmitting"}
                    </Button>
                  )}
                  {normalizedStatus === "approved" && contribution.coverId != null && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={openApprovedCover}
                      disabled={submitting || deleting}
                    >
                      <ExternalLink className="mr-2 h-4 w-4" />
                      View cover
                    </Button>
                  )}
                  {canDeleteOwn && (
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => setDeleteOpen(true)}
                      disabled={submitting || deleting}
                    >
                      <Trash2 className="mr-2 h-4 w-4" />
                      Delete
                    </Button>
                  )}
                </div>
              </div>
              <p className="text-sm text-muted-foreground">{displayName}</p>
            </CardHeader>
            <CardContent>
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <Badge className={statusBadgeClassName}>{statusLabel}</Badge>
                <Badge variant="outline">Cover submission</Badge>
              </div>
              <CoverRecordDetailFields
                type={coverTypeLabel(typeCode)}
                date={formatCoverDate(sd)}
                institutionallyOwned={boolLabel(sd.is_institutional ?? sd.isInstitutional)}
                backstamp={boolLabel(sd.is_backstamp ?? sd.isBackstamp)}
              />
              {contributorComment && (
                <div className="mt-4 rounded-md border border-border bg-muted/40 px-3 py-2">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Comment for editor</p>
                  <p className="text-sm text-foreground whitespace-pre-line">{contributorComment}</p>
                </div>
              )}
              <p className="mt-4 text-sm text-muted-foreground">
                Submitted {new Date(contribution.createdAt).toLocaleString()}
                {contribution.contributorUsername ? ` by ${contribution.contributorUsername}` : ""}
              </p>
            </CardContent>
          </Card>

          <Card className="shadow-archival-md">
            <CardHeader>
              <CardTitle className="font-heading text-lg">
                Associated Markings ({associatedMarkingCount})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 pt-0">
              {parentMarkingError && (
                <p className="text-sm text-destructive rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2">
                  {parentMarkingError}
                </p>
              )}
              {parentMarking ? (
                <AssociatedMarkingPreviewCard
                  marking={parentMarking}
                  defaultImageUrl={parentMarking.mainImage?.imageUrl ?? null}
                  onOpenMarking={openParentMarking}
                />
              ) : parentMarkingId != null ? (
                <div className="rounded-md border border-border px-4 py-3">
                  <Button variant="outline" size="sm" onClick={openParentMarking}>
                    Open parent marking
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No parent marking linked to this cover submission.</p>
              )}
            </CardContent>
          </Card>

          <EntryCitationsCard
            citations={citations}
            emptyMessage="No citations linked to this cover submission yet."
          />
        </>
      )}
    />
    <AlertDialog open={deleteOpen} onOpenChange={(open) => !deleting && setDeleteOpen(open)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {normalizedStatus === "draft" ? "Delete draft" : "Delete submission"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {normalizedStatus === "pending"
              ? "This withdraws your cover submission and permanently deletes it before it is reviewed. This cannot be undone."
              : "This permanently deletes this cover submission. This cannot be undone."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault();
              handleDeleteConfirm();
            }}
            disabled={deleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {deleting ? "Deleting..." : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  );
}
