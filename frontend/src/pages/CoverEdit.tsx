import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Loader2,
  Star,
  Trash2,
  Upload,
} from "lucide-react";
import axios from "axios";
import { getContribution, createContribution } from "@/services/contributions";

import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { COVER_SUBMISSION_GUIDELINES } from "@/labels/guidelines";
import { isCoverContributionData } from "@/lib/contributionDisplay";
import {
  contributionImageMetasFromSubmittedData,
  markingImagesFromContributionMetas,
} from "@/lib/contributionImages";

import { deriveGranularityFromIso } from "@/lib/dateGranularity";
import { listCitationsForSubject } from "@/services/citations";
import {
  getImagesForSubject,
  getMarkingById,
  getMarkingCovers,
  normalizeImageUrl,
  resolveMarkingRoutingState,
  routingStateFromMarkingRecord,
  type AssociatedCover,
  type MarkingImage,
  type MarkingRecord,
} from "@/services/markings";
import { formatReferenceWorkLabel, getReferenceWorks, type ReferenceWorkRecord } from "@/services/referenceWorks";

type Mode = "create" | "edit";

type CoverDateField = {
  existingId?: number;
  date: string;
};

type PendingUpload = {
  key: string;
  file: File;
  tracing: boolean;
  previewUrl: string;
};

// One entry in the combined image gallery. "existing" wraps an already-saved
// catalog or draft image; "pending" wraps a newly-picked file. Reorder and
// set-default operate across both kinds in a single ordered list.
type GalleryItem =
  | { kind: "existing"; key: string; img: MarkingImage }
  | { kind: "pending"; key: string; upload: PendingUpload };

type ReferenceDetailInput = {
  pageNumber: string;
  citationUrl: string;
};

type ReferenceDetailFieldErrors = {
  pageNumber?: string;
  citationUrl?: string;
};

const COVER_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "FC", label: "FC - Folded Cover" },
  { value: "FL", label: "FL - Folded Letter" },
];

const DEFAULT_COVER_TYPE = "FL";

const MAX_IMAGE_SIZE_MB = 100;
const ALLOWED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/jpg", "image/tiff"];
const PAGE_NUMBER_RE = /^[A-Za-z0-9][A-Za-z0-9\s\-.,:;()/#]*$/;

function formatAxiosError(err: unknown): string {
  if (!axios.isAxiosError(err)) {
    return err instanceof Error ? err.message : "Request failed.";
  }
  const data = err.response?.data as Record<string, unknown> | undefined;
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(String).join(" ");
  if (data && typeof data === "object") return JSON.stringify(data);
  return err.message || "Request failed.";
}

function normalizeCoverType(raw: string | null | undefined): string {
  return raw === "FC" || raw === "FL" ? raw : DEFAULT_COVER_TYPE;
}

function buildEditState(cover: AssociatedCover | null | undefined) {
  const c = cover?.coverDetails ?? null;
  const seen = c?.datesSeen ?? [];
  const sorted = [...seen].sort((a, b) => a.date.localeCompare(b.date));
  const primary = sorted[0];
  const coverDate: CoverDateField =
    primary != null ? { existingId: primary.id, date: primary.date.slice(0, 10) } : { date: "" };

  return {
    type: normalizeCoverType(c?.type ?? null),
    isInstitutional: c?.isInstitutional === true,
    isBackstamp: cover?.isBackstamp === true,
    displaySubmitterName: c?.displaySubmitterName === true,
    coverDate,
  };
}

function fileKey(file: File) {
  return `${file.name}-${file.size}-${file.lastModified}`;
}

function parseCitationDetailBlob(text: string): ReferenceDetailInput {
  let pageNumber = "";
  let citationUrl = "";
  for (const line of text.split("\n")) {
    const p = line.match(/^Page number:\s*(.*)$/i);
    if (p) pageNumber = p[1]?.trim() ?? "";
    const u = line.match(/^Citation url:\s*(.*)$/i);
    if (u) citationUrl = u[1]?.trim() ?? "";
  }
  return { pageNumber, citationUrl };
}

type FieldErrorsShape = {
  type?: string;
  date?: string;
  images?: string;
  referenceWorks?: string;
  contributorComment?: string;
};

const COVER_FIELD_SCROLL_ORDER: Array<{ key: keyof FieldErrorsShape; id: string }> = [
  { key: "type", id: "cover-type" },
  { key: "date", id: "cover-date" },
  { key: "images", id: "cover-images-zone" },
  { key: "referenceWorks", id: "cover-reference-works" },
  { key: "contributorComment", id: "contributor-comment" },
];

function scrollToFirstFieldError(errors: FieldErrorsShape) {
  for (const { key, id } of COVER_FIELD_SCROLL_ORDER) {
    if (!errors[key]) continue;
    const el = document.getElementById(id);
    if (!el) continue;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const focusable = el as HTMLElement & { focus?: () => void };
    if (typeof focusable.focus === "function") {
      window.setTimeout(() => focusable.focus?.(), 300);
    }
    return;
  }
}

export default function CoverEdit() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams();
  const [searchParams] = useSearchParams();
  const user = useAuth();

  const markingId = params.id ? parseInt(String(params.id).replace(/^api-/, ""), 10) : NaN;
  const routeCoverId = params.coverId ? parseInt(String(params.coverId).replace(/^api-/, ""), 10) : null;
  const mode: Mode = routeCoverId != null && Number.isFinite(routeCoverId) ? "edit" : "create";
  const editContributionIdRaw = searchParams.get("edit");
  const editContributionId =
    editContributionIdRaw && /^\d+$/.test(editContributionIdRaw)
      ? parseInt(editContributionIdRaw, 10)
      : null;

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draftLoadDone, setDraftLoadDone] = useState(editContributionId == null);
  const [draftLoadError, setDraftLoadError] = useState<string | null>(null);
  const [coverRow, setCoverRow] = useState<AssociatedCover | null>(null);
  const [markingRecord, setMarkingRecord] = useState<MarkingRecord | null>(null);

  const initial = useMemo(
    () => (mode === "edit" ? buildEditState(coverRow) : null),
    [mode, coverRow],
  );

  const [type, setType] = useState(mode === "create" ? "" : DEFAULT_COVER_TYPE);
  const [isInstitutional, setIsInstitutional] = useState(false);
  const [isBackstamp, setIsBackstamp] = useState(false);
  const [displaySubmitterName, setDisplaySubmitterName] = useState(false);
  const [coverDate, setCoverDate] = useState<CoverDateField>({ date: "" });

  const [fieldErrors, setFieldErrors] = useState<FieldErrorsShape>({});
  const [referenceDetailErrorsById, setReferenceDetailErrorsById] = useState<
    Record<number, ReferenceDetailFieldErrors>
  >({});

  const [contributorComment, setContributorComment] = useState("");

  const [submitting, setSubmitting] = useState(false);

  const coverId = coverRow?.coverDetails?.id ?? null;
  // Single ordered gallery: already-saved images interleaved with new uploads.
  // Reorder + set-default operate across the whole list; the chosen order is sent
  // as image_order and applied at approval (see backend
  // _reorder_metas_by_image_order in common/api/v2/views.py). The edit form never
  // writes to the catalog directly.
  const [gallery, setGallery] = useState<GalleryItem[]>([]);
  const [imagesLoading, setImagesLoading] = useState(false);
  // Catalog image URLs the contributor removed during this edit session. Sent as
  // removed_existing_image_keys so the catalog image is dropped on approval.
  const [removedExistingImageKeys, setRemovedExistingImageKeys] = useState<string[]>([]);

  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dropActive, setDropActive] = useState(false);

  const [referenceWorks, setReferenceWorks] = useState<ReferenceWorkRecord[]>([]);
  const [referenceWorksLoading, setReferenceWorksLoading] = useState(true);
  const [referenceWorksError, setReferenceWorksError] = useState<string | null>(null);
  const [selectedReferenceWorks, setSelectedReferenceWorks] = useState<ReferenceWorkRecord[]>([]);
  const [referenceDetailsById, setReferenceDetailsById] = useState<Record<number, ReferenceDetailInput>>({});

  const citationsLoadedRef = useRef(false);

  useEffect(() => {
    citationsLoadedRef.current = false;
  }, [routeCoverId, mode, editContributionId]);

  // Resume an existing cover draft from My Submissions (?edit=contributionId).
  useEffect(() => {
    if (editContributionId == null || mode !== "create") {
      setDraftLoadDone(true);
      setDraftLoadError(null);
      return;
    }
    let cancelled = false;
    setDraftLoadDone(false);
    setDraftLoadError(null);
    void (async () => {
      try {
        const contribution = await getContribution(editContributionId);
        const sd = contribution.submittedData as Record<string, unknown> | undefined;
        if (!sd || typeof sd !== "object" || !isCoverContributionData(sd)) {
          if (!cancelled) {
            setDraftLoadError("This draft is not a cover submission.");
            setDraftLoadDone(true);
          }
          return;
        }
        const typeVal = String(sd.type ?? "").trim().toUpperCase();
        if (typeVal === "FC" || typeVal === "FL") setType(typeVal);
        const rawDate = String(sd.cover_date ?? sd.coverDate ?? "").trim();
        if (rawDate) setCoverDate({ date: rawDate.slice(0, 10) });
        setIsInstitutional(String(sd.is_institutional ?? sd.isInstitutional) === "true");
        setIsBackstamp(String(sd.is_backstamp ?? sd.isBackstamp) === "true");
        setDisplaySubmitterName(String(sd.display_submitter_name ?? sd.displaySubmitterName) === "true");
        const comment = String(sd.contributor_comment ?? sd.comment_for_editor ?? "").trim();
        if (comment) setContributorComment(comment);

        const refIdsRaw = sd.reference_work_ids ?? sd.referenceWorkIds;
        const refIds = Array.isArray(refIdsRaw)
          ? refIdsRaw.map((x) => parseInt(String(x), 10)).filter((n) => Number.isFinite(n))
          : [];
        if (refIds.length > 0 && referenceWorks.length > 0) {
          const selected = referenceWorks.filter((w) => refIds.includes(w.id));
          setSelectedReferenceWorks(selected);
        }

        const metas = contributionImageMetasFromSubmittedData(sd);
        const draftImages = markingImagesFromContributionMetas(metas, markingId);
        if (draftImages.length > 0) {
          const tagsRaw = sd.cover_image_tags ?? sd.coverImageTags;
          let tags: string[] = [];
          if (typeof tagsRaw === "string") {
            try {
              const parsed = JSON.parse(tagsRaw) as unknown;
              tags = Array.isArray(parsed) ? parsed.map((t) => String(t)) : [];
            } catch {
              tags = [];
            }
          } else if (Array.isArray(tagsRaw)) {
            tags = tagsRaw.map((t) => String(t));
          }
          setGallery(
            draftImages.map((img, i) => ({
              kind: "existing" as const,
              key: normalizeImageUrl(img.imageUrl),
              img: { ...img, isTracing: tags[i] === "tracing" },
            })),
          );
        }

        if (!cancelled) setDraftLoadDone(true);
      } catch {
        if (!cancelled) {
          setDraftLoadError("Could not load cover draft.");
          setDraftLoadDone(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [editContributionId, mode, referenceWorks]);

  useEffect(() => {
    if (mode !== "edit" || !initial) return;
    setType(initial.type);
    setIsInstitutional(initial.isInstitutional);
    setIsBackstamp(initial.isBackstamp);
    setDisplaySubmitterName(initial.displaySubmitterName);
    setCoverDate(initial.coverDate);
  }, [mode, initial]);

  useEffect(() => {
    if (!coverRow) return;
    setContributorComment(coverRow.contributorComment ?? "");
  }, [coverRow?.id]);

  useEffect(() => {
    let cancelled = false;
    setReferenceWorksLoading(true);
    setReferenceWorksError(null);
    getReferenceWorks()
      .then((rows) => {
        if (!cancelled) setReferenceWorks(rows);
      })
      .catch(() => {
        if (!cancelled) setReferenceWorksError("Could not load reference works.");
      })
      .finally(() => {
        if (!cancelled) setReferenceWorksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!Number.isFinite(markingId) || markingId <= 0) {
      setMarkingRecord(null);
      return;
    }
    let cancelled = false;
    getMarkingById(markingId)
      .then((rec) => {
        if (!cancelled) setMarkingRecord(rec);
      })
      .catch(() => {
        if (!cancelled) setMarkingRecord(null);
      });
    return () => {
      cancelled = true;
    };
  }, [markingId]);

  useEffect(() => {
    if (!Number.isFinite(markingId) || markingId <= 0) {
      setLoadError("Invalid marking id.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    getMarkingCovers(markingId)
      .then(({ covers }) => {
        if (cancelled) return;
        if (mode === "create") {
          setCoverRow(null);
          return;
        }
        const found = covers.find((c) => c.coverDetails?.id === routeCoverId) ?? null;
        if (!found) {
          setLoadError("Cover not found for this marking.");
          setCoverRow(null);
          return;
        }
        setCoverRow(found);
      })
      .catch(() => {
        if (!cancelled) setLoadError("Unable to load cover details.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [markingId, routeCoverId, mode]);

  useEffect(() => {
    if (
      mode !== "edit" ||
      !coverRow?.coverDetails?.id ||
      referenceWorks.length === 0 ||
      citationsLoadedRef.current
    ) {
      return;
    }
    const cid = coverRow.coverDetails.id;
    let cancelled = false;
    void listCitationsForSubject({ subjectType: "COVER", subjectId: cid }).then((rows) => {
      if (cancelled) return;
      const picked: ReferenceWorkRecord[] = [];
      const details: Record<number, ReferenceDetailInput> = {};
      for (const row of rows) {
        const rw = referenceWorks.find((w) => w.id === row.referenceWorkId);
        if (!rw) continue;
        picked.push(rw);
        details[rw.id] = parseCitationDetailBlob(row.citationDetail);
      }
      if (picked.length > 0) {
        setSelectedReferenceWorks(picked);
        setReferenceDetailsById((prev) => ({ ...details, ...prev }));
      }
      citationsLoadedRef.current = true;
    });
    return () => {
      cancelled = true;
    };
  }, [mode, coverRow, referenceWorks]);

  const refreshImages = async () => {
    if (coverId == null) {
      // Create/draft flow stores images in submitted_data until a Cover row exists.
      return;
    }
    setImagesLoading(true);
    try {
      const imgs = await getImagesForSubject({ subjectType: "COVER", subjectId: coverId });
      setGallery(
        imgs.map((img) => ({
          kind: "existing" as const,
          key: normalizeImageUrl(img.imageUrl),
          img,
        })),
      );
    } finally {
      setImagesLoading(false);
    }
  };

  useEffect(() => {
    void refreshImages();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverId]);

  const returnPath = () => {
    const from = (location.state as { from?: string } | null)?.from;
    if (from) return from;
    if (mode === "edit" && routeCoverId != null) {
      return `/record/${markingId}/cover/${routeCoverId}`;
    }
    return `/record/${markingId}`;
  };

  const handleBack = () => {
    navigate(returnPath());
  };

  const mergePickedFiles = (files: FileList | File[] | null) => {
    if (!files || (Array.isArray(files) && files.length === 0)) return;
    const list = Array.isArray(files) ? files : Array.from(files);
    const next: GalleryItem[] = [];
    for (const file of list) {
      const mime = (file.type || "").toLowerCase();
      if (!ALLOWED_IMAGE_TYPES.includes(mime)) continue;
      if (file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024) continue;
      const key = fileKey(file);
      next.push({
        kind: "pending",
        key,
        upload: { key, file, tracing: false, previewUrl: URL.createObjectURL(file) },
      });
    }
    if (next.length === 0) return;
    setGallery((prev) => {
      const seen = new Set(prev.map((p) => p.key));
      const additions = next.filter((p) => !seen.has(p.key));
      return [...prev, ...additions];
    });
    setFieldErrors((prev) => ({ ...prev, images: undefined }));
    if (inputRef.current) inputRef.current.value = "";
  };

  // Reorder + set-default + tracing + remove all act on the single combined
  // gallery, so they work uniformly across already-saved and newly-picked images.
  const removeGalleryAt = (index: number) => {
    setGallery((prev) => {
      const item = prev[index];
      if (!item) return prev;
      if (item.kind === "pending") {
        URL.revokeObjectURL(item.upload.previewUrl);
      } else {
        // Both catalog images and draft previews carry a media URL whose tail is
        // the storage_filename; the backend tail-matches removed keys against it,
        // so the same key works for a published-cover edit and a draft resume.
        const key = normalizeImageUrl(item.img.imageUrl);
        if (key) {
          setRemovedExistingImageKeys((keys) => (keys.includes(key) ? keys : [...keys, key]));
        }
      }
      return prev.filter((_, i) => i !== index);
    });
    setFieldErrors((prev) => ({ ...prev, images: undefined }));
  };

  const moveGalleryBy = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    setGallery((prev) => {
      if (target < 0 || target >= prev.length) return prev;
      const next = prev.slice();
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const setGalleryDefault = (index: number) => {
    if (index <= 0) return;
    setGallery((prev) => {
      if (index >= prev.length) return prev;
      const next = prev.slice();
      const [picked] = next.splice(index, 1);
      next.unshift(picked);
      return next;
    });
  };

  const setGalleryTracingAt = (index: number, tracing: boolean) => {
    setGallery((prev) =>
      prev.map((item, i) => {
        if (i !== index) return item;
        return item.kind === "pending"
          ? { ...item, upload: { ...item.upload, tracing } }
          : { ...item, img: { ...item.img, isTracing: tracing } };
      }),
    );
  };

  const validateForm = (saveAsDraft: boolean): boolean => {
    if (saveAsDraft) {
      setReferenceDetailErrorsById({});
      setFieldErrors({});
      return true;
    }
    const errors: FieldErrorsShape = {};
    const referenceDetailErrors: Record<number, ReferenceDetailFieldErrors> = {};

    if (!type.trim()) {
      errors.type = "Cover type is required.";
    }
    const trimmedDate = (coverDate.date ?? "").trim();
    if (!trimmedDate) {
      errors.date = "Date is required.";
    } else if (!deriveGranularityFromIso(trimmedDate)) {
      errors.date = "Enter a complete calendar date.";
    }
    const imageCount = gallery.length;
    if (imageCount < 1) {
      errors.images = "At least one cover image is required.";
    }

    for (const work of selectedReferenceWorks) {
      const detail = referenceDetailsById[work.id];
      const page = detail?.pageNumber.trim() ?? "";
      const citationUrl = detail?.citationUrl.trim() ?? "";
      const rowErrors: ReferenceDetailFieldErrors = {};

      if (page) {
        if (page.length > 120) {
          rowErrors.pageNumber = "Page number must be 120 characters or fewer";
        } else if (!PAGE_NUMBER_RE.test(page)) {
          rowErrors.pageNumber = "Page number has invalid characters";
        }
      }
      if (citationUrl) {
        if (citationUrl.length > 2000) {
          rowErrors.citationUrl = "Citation URL must be 2000 characters or fewer";
        } else {
          try {
            const parsed = new URL(citationUrl);
            if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
              rowErrors.citationUrl = "Citation URL must start with http:// or https://";
            }
          } catch {
            rowErrors.citationUrl = "Citation URL must be a valid URL";
          }
        }
      }

      if (rowErrors.pageNumber || rowErrors.citationUrl) {
        referenceDetailErrors[work.id] = rowErrors;
      }
    }

    setReferenceDetailErrorsById(referenceDetailErrors);
    if (Object.keys(referenceDetailErrors).length > 0) {
      errors.referenceWorks = "Fix citation fields for selected reference works.";
    }

    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      scrollToFirstFieldError(errors);
      return false;
    }
    return true;
  };

  // Save a cover draft (asDraft=true) or submit it for editor review (asDraft=false).
  // Both create a Contribution via createContribution; only the save_as_draft flag and
  // the post-success toast/navigation differ. A submitted cover goes through the same
  // review queue as a marking, so it appears under My Submissions (contributor) and the
  // editor pending-review queue -- not directly on the marking record. The backend sets
  // status=pending when save_as_draft is absent and materializes the Cover + CoverMarking
  // on approval (see backend ContributionViewSet.approve / contribution_apply.py).
  const submitCoverContribution = async (asDraft: boolean) => {
    if (!user) {
      toast({
        title: "Sign in required",
        description: asDraft ? "Please sign in to save a draft." : "Please sign in before submitting.",
        variant: "destructive",
      });
      return;
    }
    if (!Number.isFinite(markingId) || markingId <= 0) {
      toast({
        title: "Invalid marking",
        description: "This cover must be linked to a marking record.",
        variant: "destructive",
      });
      return;
    }

    // State helps route the draft to the correct Collection when available; the
    // API still accepts cover drafts without it (fallback collection + status=draft).
    let stateRoute = routingStateFromMarkingRecord(markingRecord);
    if (!stateRoute) {
      const rec = markingRecord ?? (await getMarkingById(markingId));
      if (rec) {
        if (!markingRecord) setMarkingRecord(rec);
        stateRoute = routingStateFromMarkingRecord(rec);
      }
    }
    if (!stateRoute) {
      stateRoute = await resolveMarkingRoutingState(markingId);
    }
    const oversized = gallery.filter(
      (item) => item.kind === "pending" && item.upload.file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024,
    );
    if (oversized.length) {
      toast({
        title: "Image too large",
        description: `Use images under ${MAX_IMAGE_SIZE_MB}MB.`,
        variant: "destructive",
      });
      return;
    }
    if (!validateForm(asDraft)) return;

    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("submission_kind", "cover");
      if (asDraft) form.append("save_as_draft", "true");
      if (editContributionId != null) {
        form.append("edit_contribution_id", String(editContributionId));
      }
      if (stateRoute) {
        form.append("state", stateRoute);
      }
      form.append("parent_marking_id", String(markingId));
      form.append("marking_id", String(markingId));
      if (type.trim()) form.append("type", type.trim().toUpperCase());
      const trimmedDate = coverDate.date.trim();
      const derived = deriveGranularityFromIso(trimmedDate);
      if (derived) {
        form.append("cover_date", derived.normalizedDate);
        form.append("cover_granularity", derived.granularity);
      }
      form.append("is_institutional", String(isInstitutional));
      form.append("is_backstamp", String(isBackstamp));
      form.append("display_submitter_name", String(displaySubmitterName));
      const trimmedComment = contributorComment.trim();
      if (trimmedComment) {
        form.append("contributor_comment", trimmedComment);
        form.append("comment_for_editor", trimmedComment);
      }
      selectedReferenceWorks.forEach((w) => form.append("reference_work_ids[]", String(w.id)));
      if (selectedReferenceWorks.length > 0) {
        const payload = selectedReferenceWorks.map((work) => ({
          reference_work_id: work.id,
          page_number: referenceDetailsById[work.id]?.pageNumber?.trim() || undefined,
          url: referenceDetailsById[work.id]?.citationUrl?.trim() || undefined,
        }));
        form.append("reference_work_details", JSON.stringify(payload));
      }
      // Walk the combined gallery in display order: append each new-upload file
      // with its positional tracing tag, and emit an image_order token per image
      // (the existing image's key, or "__new__" for a new upload). The backend
      // rebuilds the saved metas list in this order so display_order at approval
      // matches the on-screen order (and index 0 becomes the catalog default).
      const tracingTags: string[] = [];
      const imageOrder: string[] = [];
      for (const item of gallery) {
        if (item.kind === "pending") {
          form.append("cover_image", item.upload.file, item.upload.file.name);
          tracingTags.push(item.upload.tracing ? "tracing" : "photograph");
          imageOrder.push("__new__");
        } else {
          imageOrder.push(item.key);
        }
      }
      form.append("cover_image_tags", JSON.stringify(tracingTags));
      form.append("image_order", JSON.stringify(imageOrder));

      // Existing-image removals apply on approval in both flows: the fresh
      // published-cover edit (edit_cover_id) and the draft-resume merge
      // (edit_contribution_id). Harmless no-op for a brand-new cover (empty list).
      if (removedExistingImageKeys.length > 0) {
        form.append("removed_existing_image_keys", JSON.stringify(removedExistingImageKeys));
      }

      if (mode === "edit" && coverRow?.coverDetails) {
        // Edit markers: the backend updates the existing Cover/CoverMarking in
        // place on approval (no duplicate) -- see backend/common/api/v2/views.py
        // _apply_fresh_edit_image_metas and contribution_apply.py _apply_cover_edit.
        form.append("edit_cover_id", String(coverRow.coverDetails.id));
        form.append("edit_cover_marking_id", String(coverRow.id));
        // existing_image_tags: {url: "tracing"|"photograph"} over retained catalog
        // images (imageId > 0). Emit every kept image (not just tracings) so
        // unchecking Tracing flips the value back to photograph; the backend
        // tail-matches each URL against Image.storage_filename.
        const existingTagMap: Record<string, string> = {};
        for (const item of gallery) {
          if (item.kind === "existing" && item.img.imageId > 0) {
            existingTagMap[normalizeImageUrl(item.img.imageUrl)] =
              item.img.isTracing ? "tracing" : "photograph";
          }
        }
        if (Object.keys(existingTagMap).length > 0) {
          form.append("existing_image_tags", JSON.stringify(existingTagMap));
        }
      }

      await createContribution(form);

      if (asDraft) {
        toast({
          title: editContributionId != null ? "Cover draft updated" : "Cover draft saved",
          description: "Your draft is saved and available in your Dashboard under My Submissions.",
        });
        // Return to wherever the editor opened this form from (dashboard, record,
        // or contribution detail); returnPath honors location.state.from and
        // falls back to the parent marking record.
        navigate(returnPath());
      } else {
        toast({
          title: mode === "edit" ? "Cover edit submitted for review" : "Cover submitted",
          description:
            mode === "edit"
              ? "Your changes have been submitted for approval. The published cover updates after an editor approves them."
              : "Your cover has been submitted for approval. It will appear on this marking after an editor approves it.",
        });
        navigate("/dashboard", { state: { tab: "submissions" } });
      }
    } catch (err) {
      toast({
        title: asDraft ? "Could not save draft" : "Could not submit cover",
        description: formatAxiosError(err),
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  // Route the form's submit button. Both new covers and edits go through the
  // editor review queue (submitCoverContribution); nothing writes to the catalog
  // directly -- approval applies the change (and updates edits in place).
  const handleFormSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submitCoverContribution(false);
  };

  if (loading || !draftLoadDone) {
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

  if (loadError || draftLoadError) {
    return (
      <div className="min-h-screen flex flex-col">
        <Navigation />
        <div className="flex-1 bg-background">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <Card className="border-destructive/50">
              <CardContent className="pt-6 space-y-4">
                <p className="text-destructive">{loadError || draftLoadError}</p>
                <Button variant="outline" onClick={handleBack}>
                  Go back
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>
        <Footer />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />
      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="mb-6 flex flex-wrap items-center gap-3">
            <Button variant="ghost" onClick={handleBack} className="-ml-4">
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back
            </Button>
          </div>

          <div className="mb-8">
            <h1 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-2">
              Contributor Dashboard
            </h1>
            <p className="text-muted-foreground">
              Submit new cover entries or corrections. All fields match what reviewers see on Submission Detail page.
            </p>
          </div>

          <div className="grid lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-6">
              <Card className="shadow-archival-lg">
                <CardHeader>
                  <CardTitle className="font-heading text-xl">
                    {mode === "create"
                      ? editContributionId != null
                        ? "Continue Cover Draft"
                        : "Submit New Cover"
                      : "Edit Cover"}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-6">
                  {mode === "edit" && coverRow?.reviewStatus === "pending" && (
                    <p className="text-sm rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-amber-900 dark:text-amber-100">
                      This cover is <strong>pending editor review</strong>. It is only visible to you and assigned
                      editors until it is approved.
                    </p>
                  )}
                  {mode === "edit" && coverRow?.reviewStatus === "needs_revision" && (
                    <div className="text-sm rounded-md border border-orange-500/30 bg-orange-500/5 px-3 py-2 space-y-1">
                      <p className="font-medium text-foreground">Editor requested changes</p>
                      {(coverRow.reviewNotes ?? "").trim().length > 0 ? (
                        <p className="text-muted-foreground whitespace-pre-wrap">{coverRow.reviewNotes}</p>
                      ) : (
                        <p className="text-muted-foreground">Update the cover below, then save to send it back.</p>
                      )}
                    </div>
                  )}

                  <form onSubmit={handleFormSubmit} className="space-y-6" noValidate>
                    <div className="space-y-2">
                      <Label htmlFor="cover-type">
                        Type <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <Select
                        value={type || undefined}
                        onValueChange={(v) => {
                          setType(v);
                          setFieldErrors((prev) => ({ ...prev, type: undefined }));
                        }}
                      >
                        <SelectTrigger id="cover-type" className={cn(fieldErrors.type && "border-destructive")}>
                          <SelectValue placeholder="Select cover type..." />
                        </SelectTrigger>
                        <SelectContent>
                          {COVER_TYPE_OPTIONS.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {fieldErrors.type && <p className="text-sm text-destructive">{fieldErrors.type}</p>}
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="cover-date">
                        Date <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <Input
                        id="cover-date"
                        type="date"
                        value={coverDate.date}
                        onChange={(e) => {
                          setCoverDate((prev) => ({ ...prev, date: e.target.value }));
                          setFieldErrors((prev) => ({ ...prev, date: undefined }));
                        }}
                        disabled={submitting}
                        className={cn(fieldErrors.date && "border-destructive")}
                      />
                      {fieldErrors.date && <p className="text-sm text-destructive">{fieldErrors.date}</p>}
                    </div>

                    <div className="space-y-2" id="cover-images-zone">
                      <Label>
                        Cover images <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <input
                        ref={inputRef}
                        type="file"
                        accept={ALLOWED_IMAGE_TYPES.join(",")}
                        multiple
                        className="hidden"
                        onChange={(e) => mergePickedFiles(e.target.files)}
                      />
                      <div
                        role="presentation"
                        tabIndex={0}
                        onKeyDown={(ev) => {
                          if (ev.key === "Enter" || ev.key === " ") {
                            ev.preventDefault();
                            inputRef.current?.click();
                          }
                        }}
                        onClick={() => inputRef.current?.click()}
                        onDragEnter={(e) => {
                          e.preventDefault();
                          setDropActive(true);
                        }}
                        onDragOver={(e) => {
                          e.preventDefault();
                          setDropActive(true);
                        }}
                        onDragLeave={(e) => {
                          e.preventDefault();
                          setDropActive(false);
                        }}
                        onDrop={(e) => {
                          e.preventDefault();
                          setDropActive(false);
                          mergePickedFiles(e.dataTransfer.files);
                        }}
                        className={cn(
                          "rounded-lg border-2 border-dashed border-border bg-muted/20 px-4 py-6 cursor-pointer transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          dropActive && "border-primary bg-muted/40",
                          fieldErrors.images && "border-destructive",
                        )}
                      >
                        <div className="pointer-events-none flex flex-col items-center gap-2 text-center select-none">
                          <Upload className="h-10 w-10 text-muted-foreground" aria-hidden />
                          <p className="text-sm text-muted-foreground">
                            Click or drag images here (PNG, JPG, TIFF -- max {MAX_IMAGE_SIZE_MB}MB each).
                          </p>
                        </div>

                        {imagesLoading ? (
                          <div className="pointer-events-none flex items-center gap-2 text-sm text-muted-foreground mt-4 justify-center">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Loading images...
                          </div>
                        ) : (
                          gallery.length > 0 && (
                            <div
                              className="mt-6 grid grid-cols-1 sm:grid-cols-2 gap-6 pointer-events-auto"
                              onClick={(ev) => ev.stopPropagation()}
                            >
                              {gallery.map((item, idx) => {
                                const isDefault = idx === 0;
                                const url =
                                  item.kind === "existing"
                                    ? normalizeImageUrl(item.img.imageUrl)
                                    : item.upload.previewUrl;
                                const tracing =
                                  item.kind === "existing"
                                    ? item.img.isTracing === true
                                    : item.upload.tracing;
                                return (
                                  <div key={item.key} className="flex flex-col items-center gap-2 rounded border border-border p-3">
                                    <div className="relative h-36 w-36 rounded overflow-hidden bg-muted shrink-0 flex items-center justify-center border border-border">
                                      {url ? (
                                        <img src={url} alt="" className="max-h-full max-w-full object-contain" />
                                      ) : (
                                        <span className="text-xs text-muted-foreground text-center px-2">No preview</span>
                                      )}
                                      {isDefault && (
                                        <span className="absolute top-1 left-1 rounded bg-primary px-1.5 py-0.5 text-[10px] font-semibold uppercase text-primary-foreground">
                                          Default
                                        </span>
                                      )}
                                    </div>
                                    <div className="flex items-center justify-center gap-1">
                                      <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8"
                                        aria-label="Move left"
                                        disabled={idx === 0 || submitting}
                                        onClick={() => moveGalleryBy(idx, -1)}
                                      >
                                        <ArrowLeft className="h-4 w-4" />
                                      </Button>
                                      <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8"
                                        aria-label="Move right"
                                        disabled={idx === gallery.length - 1 || submitting}
                                        onClick={() => moveGalleryBy(idx, 1)}
                                      >
                                        <ArrowRight className="h-4 w-4" />
                                      </Button>
                                      <Button
                                        type="button"
                                        variant={isDefault ? "secondary" : "ghost"}
                                        size="icon"
                                        className="h-8 w-8"
                                        aria-label="Set as default"
                                        title="Set as default catalog thumbnail"
                                        disabled={isDefault || submitting}
                                        onClick={() => setGalleryDefault(idx)}
                                      >
                                        <Star className={cn("h-4 w-4", isDefault && "fill-current")} />
                                      </Button>
                                      <Button
                                        type="button"
                                        variant="destructive"
                                        size="icon"
                                        className="h-8 w-8"
                                        aria-label="Remove"
                                        disabled={submitting}
                                        onClick={() => removeGalleryAt(idx)}
                                      >
                                        <Trash2 className="h-4 w-4" />
                                      </Button>
                                    </div>
                                    <label className="flex items-center gap-2 text-sm">
                                      <Checkbox
                                        checked={tracing}
                                        onCheckedChange={(v) => setGalleryTracingAt(idx, v === true)}
                                        disabled={submitting}
                                      />
                                      Tracing
                                    </label>
                                    {item.kind === "pending" && (
                                      <p className="text-xs text-muted-foreground truncate max-w-[200px]">{item.upload.file.name}</p>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )
                        )}
                      </div>
                      {gallery.length > 0 && (
                        <p className="text-xs text-muted-foreground">
                          The first image becomes the Catalog Search thumbnail. Use the arrows to reorder or
                          the star to set the default; check Tracing on hand-traced or computer-traced diagrams.
                        </p>
                      )}
                      {fieldErrors.images && <p className="text-sm text-destructive">{fieldErrors.images}</p>}
                    </div>

                    <div className="space-y-3 pt-1">
                      <label className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={isInstitutional}
                          onCheckedChange={(v) => setIsInstitutional(v === true)}
                          disabled={submitting}
                        />
                        Institutionally Owned
                      </label>
                      <label className="flex items-center gap-2 text-sm">
                        <Checkbox checked={isBackstamp} onCheckedChange={(v) => setIsBackstamp(v === true)} disabled={submitting} />
                        Backstamp
                      </label>
                      <label className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={displaySubmitterName}
                          onCheckedChange={(v) => setDisplaySubmitterName(v === true)}
                          disabled={submitting}
                        />
                        Would you like your name to display as the submitter?
                      </label>
                    </div>

                    <div className="space-y-3" id="cover-reference-works">
                      <div className="space-y-2">
                        <Label>Reference Works</Label>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button type="button" variant="outline" className="w-full justify-between" disabled={referenceWorksLoading}>
                              <span>
                                {referenceWorksLoading
                                  ? "Loading reference works..."
                                  : selectedReferenceWorks.length > 0
                                    ? `${selectedReferenceWorks.length} selected`
                                    : "Select reference works"}
                              </span>
                              <ChevronDown className="h-4 w-4 opacity-60" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent
                            className="w-[var(--radix-dropdown-menu-trigger-width)] max-h-72 overflow-y-auto"
                            align="start"
                          >
                            {referenceWorksError ? (
                              <div className="px-2 py-1.5 text-sm text-destructive">{referenceWorksError}</div>
                            ) : referenceWorks.length === 0 ? (
                              <div className="px-2 py-1.5 text-sm text-muted-foreground">No reference works found.</div>
                            ) : (
                              referenceWorks.map((work) => {
                                const checked = selectedReferenceWorks.some((w) => w.id === work.id);
                                return (
                                  <DropdownMenuCheckboxItem
                                    key={work.id}
                                    checked={checked}
                                    onCheckedChange={(next) => {
                                      if (next) {
                                        setSelectedReferenceWorks((prev) =>
                                          prev.some((w) => w.id === work.id) ? prev : [...prev, work],
                                        );
                                        setReferenceDetailsById((prev) => ({
                                          ...prev,
                                          [work.id]: prev[work.id] ?? { pageNumber: "", citationUrl: "" },
                                        }));
                                      } else {
                                        setSelectedReferenceWorks((prev) => prev.filter((w) => w.id !== work.id));
                                        setReferenceDetailsById((prev) => {
                                          const u = { ...prev };
                                          delete u[work.id];
                                          return u;
                                        });
                                        setReferenceDetailErrorsById((prev) => {
                                          const u = { ...prev };
                                          delete u[work.id];
                                          return u;
                                        });
                                      }
                                      setFieldErrors((prev) => ({ ...prev, referenceWorks: undefined }));
                                    }}
                                  >
                                    {formatReferenceWorkLabel(work, `Reference #${work.id}`)}
                                  </DropdownMenuCheckboxItem>
                                );
                              })
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>

                      {selectedReferenceWorks.length > 0 && (
                        <div className="space-y-2">
                          {selectedReferenceWorks.map((work) => (
                            <div key={work.id} className="rounded-md border border-border px-3 py-2 space-y-3">
                              <p className="text-sm font-medium truncate">{formatReferenceWorkLabel(work, "Untitled")}</p>
                              <div className="grid gap-3 sm:grid-cols-2">
                                <div className="space-y-1">
                                  <Label className="text-xs text-muted-foreground">Page number</Label>
                                  <Input
                                    value={referenceDetailsById[work.id]?.pageNumber ?? ""}
                                    className={referenceDetailErrorsById[work.id]?.pageNumber ? "border-destructive" : ""}
                                    onChange={(e) => {
                                      const v = e.target.value;
                                      setReferenceDetailsById((prev) => ({
                                        ...prev,
                                        [work.id]: {
                                          pageNumber: v,
                                          citationUrl: prev[work.id]?.citationUrl ?? "",
                                        },
                                      }));
                                      setReferenceDetailErrorsById((prev) => ({
                                        ...prev,
                                        [work.id]: { ...prev[work.id], pageNumber: undefined },
                                      }));
                                    }}
                                  />
                                  {referenceDetailErrorsById[work.id]?.pageNumber && (
                                    <p className="text-xs text-destructive">{referenceDetailErrorsById[work.id]?.pageNumber}</p>
                                  )}
                                </div>
                                <div className="space-y-1">
                                  <Label className="text-xs text-muted-foreground">Citation URL</Label>
                                  <Input
                                    value={referenceDetailsById[work.id]?.citationUrl ?? ""}
                                    className={referenceDetailErrorsById[work.id]?.citationUrl ? "border-destructive" : ""}
                                    onChange={(e) => {
                                      const v = e.target.value;
                                      setReferenceDetailsById((prev) => ({
                                        ...prev,
                                        [work.id]: {
                                          pageNumber: prev[work.id]?.pageNumber ?? "",
                                          citationUrl: v,
                                        },
                                      }));
                                      setReferenceDetailErrorsById((prev) => ({
                                        ...prev,
                                        [work.id]: { ...prev[work.id], citationUrl: undefined },
                                      }));
                                    }}
                                  />
                                  {referenceDetailErrorsById[work.id]?.citationUrl && (
                                    <p className="text-xs text-destructive">{referenceDetailErrorsById[work.id]?.citationUrl}</p>
                                  )}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      {fieldErrors.referenceWorks && (
                        <p className="text-sm text-destructive">{fieldErrors.referenceWorks}</p>
                      )}
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="contributor-comment">Comment for editor</Label>
                      <Textarea
                        id="contributor-comment"
                        rows={3}
                        placeholder="Optional notes for reviewers."
                        value={contributorComment}
                        onChange={(e) => setContributorComment(e.target.value)}
                        disabled={submitting}
                      />
                      {fieldErrors.contributorComment && (
                        <p className="text-sm text-destructive">{fieldErrors.contributorComment}</p>
                      )}
                    </div>

                    <div className="flex flex-col sm:flex-row gap-3 pt-2">
                      {mode === "create" && (
                        <Button
                          type="button"
                          variant="outline"
                          className="w-full sm:flex-1"
                          disabled={submitting}
                          onClick={() => void submitCoverContribution(true)}
                        >
                          Save as Draft
                        </Button>
                      )}
                      <Button type="submit" className="w-full sm:flex-1" disabled={submitting}>
                        {submitting ? "Saving..." : mode === "create" ? "Submit Cover" : "Submit changes for review"}
                      </Button>
                    </div>
                  </form>
                </CardContent>
              </Card>
            </div>

            <div className="space-y-6">
              <Card className="shadow-archival-md">
                <CardHeader>
                  <CardTitle className="font-heading text-lg">Submission Guidelines</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm text-muted-foreground">
                  {COVER_SUBMISSION_GUIDELINES.map((g) => (
                    <p key={g.label} className="leading-relaxed">
                      <strong className="text-foreground">{g.label}:</strong> {g.body}
                    </p>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      </div>
      <Footer />
    </div>
  );
}
