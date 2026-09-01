import { useState, useEffect } from "react";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, Loader2, CheckCircle, XCircle, MessageSquare, ExternalLink, Pencil, Trash2 } from "lucide-react";
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
import { Link, useNavigate, useParams, useLocation } from "react-router-dom";
import imageNotAvailable from "@/assets/image-not-available.jpg";
import { ToastAction } from "@/components/ui/toast";
import { useToast } from "@/hooks/use-toast";
import { dashboardHref, dashboardHrefForTab } from "@/lib/dashboardParams";
import { readReturnTo } from "@/lib/returnTo";
import { useAuth } from "@/hooks/useAuth";
import { normalizeImageUrl } from "@/services/markings";
import { Carousel, CarouselContent, CarouselItem, CarouselNext, CarouselPrevious, type CarouselApi } from "@/components/ui/carousel";
import { getLetterings, type LetteringOption } from "@/services/letterings";
import { getDateFormats, type DateFormatOption } from "@/constants/markingEnums";
import { isCoverContributionData } from "@/lib/contributionDisplay";
import CoverContributionDetail from "@/pages/CoverContributionDetail";
import { CatalogRecordFields } from "@/components/CatalogRecordFields";
import {
  buildCatalogSearchTitleFromParts,
  displayCatalogField,
  markingTypeLabel,
  type CatalogFieldValues,
} from "@/lib/catalogRecordDisplay";
import { submittedDataToFieldInput } from "@/lib/contributionToFields";
import { readVphcProvenance } from "@/lib/vphcProvenance";
import { VphcProvenanceCard } from "@/components/VphcProvenanceCard";
import type { MarkingFieldInput } from "@/lib/markingFields";
import {
  type Contribution,
  getContribution,
  decideContribution,
  deleteOwnContribution,
  getContributionCatalogCodeSuggestion,
  getDirectCatalogCodeSuggestion,
} from "@/services/contributions";


/** Shape of the per-image metadata blobs stored in a contribution's submitted_data. */
type ContributionImageMeta = {
  storage_filename?: string;
  original_filename?: string;
  storageFilename?: string;
  originalFilename?: string;
};

function buildContributionCatalogFields(input: MarkingFieldInput): CatalogFieldValues {
  return {
    type: displayCatalogField(markingTypeLabel(input.type) || ""),
    town: displayCatalogField(input.town),
    state: displayCatalogField(input.state),
    regionAbbrev: displayCatalogField(input.state),
    manuscript: displayCatalogField(input.isManuscript ? "Yes" : "No"),
    desc: displayCatalogField(input.catalogTxt),
    markingTextLines: [],
    markingTextSingle: displayCatalogField(input.inscriptionTxt),
    shape: displayCatalogField(input.shapeName),
    lettering: displayCatalogField(input.letteringName),
    impression: displayCatalogField(input.impression),
    irregular: displayCatalogField(
      input.isIrreg == null ? null : input.isIrreg ? "Yes" : "No",
    ),
    dimensions: displayCatalogField(input.dimensions),
    color: displayCatalogField(input.colorName),
    rateValue: displayCatalogField(input.rateValFormatted),
    earliestSeen: displayCatalogField(input.earliestSeen),
    latestSeen: displayCatalogField(input.latestSeen),
  };
}

const ContributionDetail = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuth();
  const { toast } = useToast();
  const { id } = useParams();
  const [contribution, setContribution] = useState<Contribution | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [comment, setComment] = useState("");
  const [commentError, setCommentError] = useState<string | null>(null);
  const [catalogCode, setCatalogCode] = useState("");
  const [catalogCodeError, setCatalogCodeError] = useState<string | null>(null);
  const [catalogCodeLoading, setCatalogCodeLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // Withdraw/delete -- the contributor may delete any of their own UNapproved
  // submissions; the backend enforces IsOwnDeletableContribution on
  // DELETE /contributions/{id}/ (status must not be "approved").
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [letteringOptions, setLetteringOptions] = useState<LetteringOption[]>([]);
  const [dateFormatOptions, setDateFormatOptions] = useState<DateFormatOption[]>([]);
  const [carouselApi, setCarouselApi] = useState<CarouselApi>();
  const [carouselCurrent, setCarouselCurrent] = useState(0);
  const [carouselCount, setCarouselCount] = useState(0);
  const locationState = location.state as {
    fromDashboard?: boolean;
    dashboardTab?: "submissions" | "editor";
  } | null;
  const fromDashboard = locationState?.fromDashboard === true;
  const dashboardTab = locationState?.dashboardTab;
  // Issue #147: the listing the editor came from, carried in `?from=`. Unlike
  // location.state and the sessionStorage mirror, this survives a direct link --
  // which is how editors most often arrive, because Ian mails contribution URLs.
  // Null whenever the param is absent or fails the same-origin check, and every
  // caller below falls back to the pre-#147 dashboard behaviour in that case.
  const returnTo = readReturnTo(new URLSearchParams(location.search));
  /**
   * Where a finished action should land. `?from=` wins because it is the most
   * specific signal available: the caller stated it explicitly. Everything else
   * keeps issue #87's behaviour exactly.
   */
  const returnHref = (fallbackTab: "submissions" | "editor") =>
    returnTo ?? dashboardHrefForTab(dashboardTab ?? fallbackTab);
  const isStateEditor =
    user?.role === "editor" || user?.role === "administrator" || user?.is_superuser;
  /** True if the logged-in user is the person who submitted this contribution (edit/review UI is for other editors only). */
  const isContributor =
    !!user &&
    !!contribution &&
    (contribution.contributorId === user.id ||
      contribution.contributorUsername === user.username ||
      contribution.contributorUsername === user.email);

  useEffect(() => {
    const contributionId = id ? parseInt(String(id), 10) : null;
    if (!contributionId || isNaN(contributionId)) {
      setError("Invalid contribution");
      setLoading(false);
      return;
    }

    let cancelled = false;
    getContribution(contributionId)
      .then((normalized) => {
        if (cancelled) return;
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
  }, [id]);

  // Carousel pagination
  useEffect(() => {
    if (!carouselApi) return;
    setCarouselCount(carouselApi.scrollSnapList().length);
    setCarouselCurrent(carouselApi.selectedScrollSnap());
    const onSelect = () => setCarouselCurrent(carouselApi.selectedScrollSnap());
    carouselApi.on("select", onSelect);
    return () => {
      carouselApi.off("select", onSelect);
    };
  }, [carouselApi]);

  // Lettering / date format lookups -- needed to resolve numeric ids /
  // codes stored on a contribution's submitted_data to human-readable names.
  useEffect(() => {
    let cancelled = false;
    Promise.all([getLetterings(), getDateFormats()])
      .then(([lettering, dateFormat]) => {
        if (!cancelled) {
          setLetteringOptions(lettering);
          setDateFormatOptions(dateFormat);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLetteringOptions([]);
          setDateFormatOptions([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const contributionId = contribution?.id;
  const contributionStatus = contribution?.status;
  const contributionSubmittedData = contribution?.submittedData;

  // Preview the code WITHOUT writing it. `getContributionCatalogCodeSuggestion`
  // persists its answer into submitted_data (catalog_codes.suggest_for_contribution),
  // so firing it from an effect meant merely OPENING a pending row wrote to the
  // database -- no click, no intent, outside any transaction, and with no audit
  // row. That is issue #118: 27 VPHC rows in the review queue were carrying a
  // minted VPHC1-VA-M#### code purely because someone had looked at them, and
  // approving one would have made that the permanent Marking.code.
  //
  // The direct endpoint computes the same code from the same payload via the
  // same helpers (_region_from_payload, _reference_code_from_selected_payload)
  // but takes no contribution and saves nothing. Minting stays where it belongs:
  // ensureCatalogCode() below, at approve time, deliberately.
  useEffect(() => {
    if (contributionId == null || contributionStatus !== "pending" || !isStateEditor || !user) return;
    if (isCoverContributionData(contributionSubmittedData)) return;
    const sd = (contributionSubmittedData ?? {}) as Record<string, unknown>;
    const referenceWorkIds = Array.isArray(sd.reference_work_ids)
      ? (sd.reference_work_ids as number[])
      : undefined;
    let cancelled = false;
    setCatalogCodeLoading(true);
    setCatalogCodeError(null);
    getDirectCatalogCodeSuggestion({
      subjectType: "MARKING",
      state: typeof sd.state === "string" ? sd.state : undefined,
      markingId: typeof sd.edit_marking_id === "number" ? sd.edit_marking_id : null,
      referenceWorkIds,
    })
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
  }, [contributionId, contributionStatus, contributionSubmittedData, isStateEditor, user]);

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
      // Issue #87: every decision returns to the review queue the editor came
      // from. Approving used to redirect to the new catalog record instead,
      // which stranded the editor mid-queue; the record is reachable from the
      // toast action rather than by hijacking the navigation.
      toast({
        title: actionLabel,
        description: "Your comment was saved for the contributor.",
        action:
          kind === "approve" && result.markingId != null ? (
            <ToastAction
              altText="View the approved record"
              onClick={() => navigate(`/record/${result.markingId}`)}
            >
              View record
            </ToastAction>
          ) : undefined,
      });
      navigate(returnHref("editor"));
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


  const handleBack = () => {
    // Issue #87: return to the dashboard view the editor actually left, not a
    // freshly defaulted one. dashboardHrefForTab keeps their filters and page
    // while pinning the tab they came in on.
    if (returnTo) navigate(returnTo);
    else if (fromDashboard) navigate(dashboardHrefForTab(dashboardTab ?? "submissions"));
    else navigate(dashboardHref());
  };

  const handleDeleteConfirm = async () => {
    if (!contribution) return;
    setDeleting(true);
    try {
      await deleteOwnContribution(contribution.id);
      toast({ title: "Draft deleted" });
      setDeleteOpen(false);
      // Issue #147: a finished action returns to the listing it started from,
      // whichever action it was.
      navigate(returnTo ?? dashboardHref());
    } catch (err) {
      toast({
        title: "Could not delete",
        description: err instanceof Error ? err.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setDeleting(false);
    }
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

  if (error || !contribution) {
    return (
      <div className="min-h-screen flex flex-col">
        <Navigation />
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <p className="text-muted-foreground">{error || "Contribution not found"}</p>
          <Button variant="outline" onClick={handleBack}>
            Back to Dashboard
          </Button>
        </div>
        <Footer />
      </div>
    );
  }

  const rawSubmitted = contribution.submittedData;
  const sd: Record<string, unknown> =
    typeof rawSubmitted === "object" && rawSubmitted !== null
      ? (rawSubmitted as Record<string, unknown>)
      : {};
  if (isCoverContributionData(sd)) {
    return (
      <CoverContributionDetail
        initialContribution={{ ...contribution, submittedData: sd }}
      />
    );
  }

  const contributorComment = String(
    sd.contributor_comment ??
      sd.contributorComment ??
      sd.comment_for_editor ??
      sd.commentForEditor ??
      sd.review_notes ??
      sd.reviewNotes ??
      sd.comment ??
      ""
  ).trim();
  // Null for anything that did not come from the VPHC ingest.
  const vphc = readVphcProvenance(sd);
  const baseImageUrl = (import.meta.env.VITE_IMAGE_URL ?? "").replace(/\/+$/, "");
  const imageRoot = baseImageUrl || "/media";
  const resolveStorageImageUrl = (storageFilename: string) =>
    normalizeImageUrl(`${imageRoot}/${storageFilename.replace(/^\/+/, "")}`);
  const imageMetasRaw = (sd.image_metas ?? sd.imageMetas) as ContributionImageMeta[] | undefined;
  const imageMetaSingle = sd.image_meta as ContributionImageMeta | undefined;
  const imageMetaList: Array<{ imageUrl: string; originalFilename?: string }> = [];
  const asImageUrlArray = (raw: unknown): string[] => {
    if (!Array.isArray(raw)) return [];
    return raw
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (!item || typeof item !== "object") return "";
        const obj = item as Record<string, unknown>;
        const fromUrl =
          obj.url ?? obj.image_url ?? obj.imageUrl ?? obj.public_url ?? obj.publicUrl;
        return typeof fromUrl === "string" ? fromUrl.trim() : "";
      })
      .filter((url) => url.length > 0);
  };
  const categorizedImageUrls = [
    ...asImageUrlArray(sd.ratemark_images ?? sd.ratemarkImages ?? sd.RatemarkImages),
    ...asImageUrlArray(sd.auxmark_images ?? sd.auxmarkImages ?? sd.AuxmarkImages),
  ];
  const seenImageUrls = new Set<string>();
  categorizedImageUrls.forEach((url) => {
    const normalized = normalizeImageUrl(url);
    if (!normalized || seenImageUrls.has(normalized)) return;
    seenImageUrls.add(normalized);
    imageMetaList.push({ imageUrl: normalized });
  });
  if (imageMetasRaw && Array.isArray(imageMetasRaw) && imageMetasRaw.length > 0) {
    for (const meta of imageMetasRaw) {
      if (meta && typeof meta === "object") {
        const sf = meta.storage_filename ?? meta.storageFilename;
        if (sf) {
          const imageUrl = resolveStorageImageUrl(sf);
          if (!imageUrl || seenImageUrls.has(imageUrl)) continue;
          seenImageUrls.add(imageUrl);
          imageMetaList.push({
            imageUrl,
            originalFilename: meta.original_filename ?? meta.originalFilename,
          });
        }
      }
    }
  }
  if (imageMetaList.length === 0 && imageMetaSingle) {
    const sf = imageMetaSingle.storage_filename ?? imageMetaSingle.storageFilename;
    if (sf) {
      const imageUrl = resolveStorageImageUrl(sf);
      if (imageUrl) {
        imageMetaList.push({
          imageUrl,
          originalFilename: imageMetaSingle.original_filename ?? imageMetaSingle.originalFilename,
        });
      }
    }
  }
  const images = imageMetaList;

  const isPending = contribution.status === "pending";
  const normalizedStatus = String(contribution.status || "").toLowerCase();
  const statusLabel =
    normalizedStatus === "approved"
      ? "Approved"
      : normalizedStatus === "rejected"
        ? "Rejected"
        : normalizedStatus === "needs_revision"
          ? "Needs Revision"
          : "Pending";
  const statusBadgeClassName =
    normalizedStatus === "approved"
      ? "rounded-full border border-green-700 bg-green-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-green-600"
      : normalizedStatus === "rejected"
        ? "rounded-full border border-red-700 bg-red-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-red-600"
        : normalizedStatus === "needs_revision"
          ? "rounded-full border border-orange-600 bg-orange-500 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-orange-500"
          : "rounded-full border border-yellow-600 bg-yellow-500 px-3 py-1 text-xs font-semibold text-black shadow-sm hover:bg-yellow-500";
  const canReview = isStateEditor && isPending && !!user;

  // Build the catalog-search-style field block from submitted_data. The review
  // header/details intentionally mirror Catalog Search cards so the same entry
  // is recognizable before and after approval.
  let contributionCatalogFields: CatalogFieldValues | null = null;
  let displayName = `Submission #${contribution.id}`;
  let fieldRowsError: string | null = null;
  try {
    const fieldData = { ...sd };
    delete fieldData.display_submitter_name;
    delete fieldData.displaySubmitterName;
    const fieldInput = submittedDataToFieldInput(
      fieldData,
      { letteringOptions, dateFormatOptions },
      { contributionId: contribution.id },
    );
    contributionCatalogFields = buildContributionCatalogFields(fieldInput);
    displayName = buildCatalogSearchTitleFromParts({
      town: fieldInput.town,
      region: fieldInput.state,
      inscription: fieldInput.inscriptionTxt,
      code: fieldInput.code,
    });
  } catch (err) {
    fieldRowsError = err instanceof Error ? err.message : String(err);
  }
  const showEditorFeedbackCard =
    contribution.status !== "pending" || !!(contribution.reviewNotes && contribution.reviewNotes.trim());
  // Unified edit/remove buttons (mirror the entry detail page). Edit is offered
  // for any still-editable status; Delete (withdraw) is offered to the
  // contributor for any of their own UNAPPROVED submissions -- the backend
  // (IsOwnDeletableContribution) permits removing draft/pending/needs_revision/
  // rejected, but never an approved contribution.
  const canContributorEdit =
    isContributor &&
    (contribution.status === "draft" ||
      contribution.status === "pending" ||
      contribution.status === "needs_revision" ||
      contribution.status === "rejected");
  const canDeleteOwn = isContributor && contribution.status !== "approved";
  const markingId = contribution.markingId;

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />
      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {/* Breadcrumb + View record when approved -- same as RecordDetail */}
          <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
            <Button variant="ghost" onClick={handleBack} className="sm:-ml-4">
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to Dashboard
            </Button>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Badge className={statusBadgeClassName}>{statusLabel}</Badge>
              {markingId != null && (
                <Button variant="outline" size="sm" asChild>
                  <Link to={`/record/${markingId}`} state={{ fromDashboard: true, dashboardTab }}>
                    <ExternalLink className="mr-2 h-4 w-4" />
                    View record
                  </Link>
                </Button>
              )}
              {canContributorEdit && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => navigate(`/contribute?edit=${contribution.id}`)}
                >
                  <Pencil className="mr-2 h-4 w-4" />
                  Edit
                </Button>
              )}
              {canDeleteOwn && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setDeleteOpen(true)}
                  disabled={deleting}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </Button>
              )}
            </div>
          </div>

          {/* Main Content -- max-lg: flex + order -> image -> meta -> review -> feedback. lg: 2 columns, 1 row; left cell is a
              flex stack so Review sits directly under the image (no multi-row grid splitting the form height). */}
          <div className="flex flex-col gap-8 mb-8 min-w-0 lg:grid lg:grid-cols-2 lg:gap-8 lg:items-start">
            <div className="contents lg:flex lg:flex-col lg:gap-8 lg:min-w-0">
            <div className="order-1 min-w-0 lg:order-none">
              <Card className="shadow-archival-lg">
                <CardContent className="p-6">
                  <Carousel setApi={setCarouselApi} className="w-full">
                    <CarouselContent>
                      {images.length > 0 ? (
                        images.map((img, index) => {
                          const alt = img.originalFilename || `Submission image ${index + 1}`;
                          return (
                            <CarouselItem key={index}>
                              <a
                                href={img.imageUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                aria-label={`Open ${alt} in new tab`}
                                className="block"
                              >
                                <div className="flex w-full aspect-[4/3] items-center justify-center rounded border border-border bg-muted overflow-hidden">
                                  <img
                                    src={img.imageUrl}
                                    alt={alt}
                                    className="w-full h-full object-contain"
                                  />
                                </div>
                              </a>
                            </CarouselItem>
                          );
                        })
                      ) : (
                        <CarouselItem>
                          <div className="flex w-full aspect-[4/3] items-center justify-center rounded border border-border bg-muted overflow-hidden">
                            <img
                              src={imageNotAvailable}
                              alt="No image available"
                              className="w-full h-full object-cover"
                            />
                          </div>
                        </CarouselItem>
                      )}
                    </CarouselContent>
                    {images.length > 1 && (
                      <>
                        <CarouselPrevious className="left-2" />
                        <CarouselNext className="right-2" />
                      </>
                    )}
                  </Carousel>
                  {images.length > 1 && (
                    <div className="flex justify-center gap-2 mt-4 mb-4">
                      {images.map((_, index) => (
                        <button
                          key={index}
                          type="button"
                          onClick={() => carouselApi?.scrollTo(index)}
                          className={`h-2 rounded-full transition-all ${
                            index === carouselCurrent
                              ? "w-6 bg-primary"
                              : "w-2 bg-muted-foreground/30 hover:bg-muted-foreground/50"
                          }`}
                          aria-label={`Go to image ${index + 1}`}
                        />
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>



            {canReview && (
              <div className="order-4 min-w-0 lg:order-none">
                <Card className="shadow-archival-lg border-primary/20">
                  <CardHeader>
                    <CardTitle className="font-heading text-lg">Review this submission</CardTitle>
                    <p className="text-sm text-muted-foreground">
                      Choose Approve, Reject, or Return.
                    </p>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="contribution-catalog-code">Catalog code</Label>
                      <Input
                        id="contribution-catalog-code"
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
                      {catalogCodeError ? (
                        <p className="text-sm text-destructive">{catalogCodeError}</p>
                      ) : null}
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="contribution-comment">Comment</Label>
                      <Textarea
                        id="contribution-comment"
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
                      {commentError ? (
                        <p className="text-sm text-destructive">{commentError}</p>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap gap-2 pt-2">
                      <Button
                        type="button"
                        onClick={() => submitDecision("approve")}
                        disabled={submitting}
                        className="bg-green-600 hover:bg-green-700"
                      >
                        <CheckCircle className="mr-2 h-4 w-4" />
                        {submitting ? "Submitting..." : "Approve"}
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        onClick={() => submitDecision("reject")}
                        disabled={submitting}
                      >
                        <XCircle className="mr-2 h-4 w-4" />
                        Reject
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => submitDecision("revision")}
                        disabled={submitting}
                      >
                        <MessageSquare className="mr-2 h-4 w-4" />
                        Return
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            )}

            {showEditorFeedbackCard ? (
              <div className="order-5 min-w-0 lg:order-none">
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
                            : "The reviewer left a comment for you. Use this feedback to improve your submission or add a new marking if requested."}
                    </p>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {contribution.reviewNotes?.trim() ? (
                      <p className="text-sm text-foreground leading-relaxed whitespace-pre-line">
                        {contribution.reviewNotes.trim()}
                      </p>
                    ) : null}
                  </CardContent>
                </Card>
              </div>
            ) : null}
            </div>

            <div className="order-2 min-w-0 space-y-6 lg:order-none lg:col-start-2 lg:row-start-1">
              <div>
                <h1 className="font-heading text-3xl font-bold text-foreground mb-2">{displayName}</h1>
              </div>

              <Card className="shadow-archival-md border-primary/10">
                <CardHeader>
                  <CardTitle className="font-heading text-lg">Contribution details</CardTitle>
                  <p className="text-sm text-muted-foreground">
                    {canReview
                      ? "Read-only view of the contributor's submission. Use Return for revision if changes are needed."
                      : isContributor
                        ? "What you submitted. An editor will review this."
                        : "Snapshot of fields stored on this contribution."}
                  </p>
                </CardHeader>
                <CardContent>
                  {fieldRowsError ? (
                    <p className="text-sm text-destructive rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2">
                      Failed to render submitted data: {fieldRowsError}
                    </p>
                  ) : contributionCatalogFields ? (
                    <CatalogRecordFields row={contributionCatalogFields} variant="contribution" />
                  ) : (
                    <p className="text-sm text-muted-foreground py-2">
                      No submitted data returned for this contribution.
                    </p>
                  )}
                  {contributorComment ? (
                    <div className="mt-4 rounded-md border border-border bg-muted/40 px-3 py-2">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Comment for editor</p>
                      <p className="text-sm text-foreground whitespace-pre-line">{contributorComment}</p>
                    </div>
                  ) : null}
                  {/* Provenance for ingested records. The fields above are what
                      the ingest concluded; this is how much of it is guesswork. */}
                  {vphc ? <VphcProvenanceCard provenance={vphc} /> : null}
                </CardContent>
              </Card>

            </div>
          </div>

        </div>
      </div>

      <AlertDialog open={deleteOpen} onOpenChange={(open) => !deleting && setDeleteOpen(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {contribution.status === "draft" ? "Delete draft" : "Delete submission"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {contribution.status === "pending"
                ? "This withdraws your submission and permanently deletes it before it is reviewed. This cannot be undone."
                : "This permanently deletes this submission. This cannot be undone."}
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

      <Footer />
    </div>
  );
};

export default ContributionDetail;
