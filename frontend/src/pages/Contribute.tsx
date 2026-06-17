import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SearchableSelect } from "@/components/ui/searchable-select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Upload, Loader2, ChevronDown, ArrowLeft, ArrowRight, Star, Trash2 } from "lucide-react";
import { useState, useEffect, useRef, useMemo } from "react";
import type { FormEvent, MouseEvent } from "react";
import { useNavigate, useLocation, useSearchParams, useParams } from "react-router-dom";
import { getColors, type ColorOption } from "@/services/colors";
import { getShapes, type ShapeOption } from "@/services/shapes";
import { getPostOffices, type PostOfficeOption } from "@/services/postOffices";
import { getRegions } from "@/services/regions";
import { getMarkingByIdRaw, normalizeImageUrl } from "@/services/markings";
import { deriveGranularityFromIso } from "@/lib/dateGranularity";
import { getLetterings, type LetteringOption } from "@/services/letterings";
import { getDateFormats, type DateFormatOption } from "@/constants/markingEnums";
import { formatReferenceWorkLabel, getReferenceWorks, type ReferenceWorkRecord } from "@/services/referenceWorks";
import {
  getContribution,
  listContributions,
  createContribution,
  deleteDraftContribution,
  getDirectCatalogCodeSuggestion,
} from "@/services/contributions";
import { ENTRY_LABELS } from "@/labels/entry";
import { SUBMISSION_LABELS } from "@/labels/submission";
import {
  MARKING_SUBMISSION_GUIDELINES,
  INSCRIPTION_TEXT_HELP,
  DATE_FORMAT_HELP,
} from "@/labels/guidelines";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/hooks/useAuth";
import {
  sanitizeMmInput,
  validateMmPair,
  submittedDataToWidthHeightStrings,
} from "@/lib/dimensionsMm";

const SUBMISSION_IMAGES_BUCKET = "submission-images";
const MAX_IMAGE_SIZE_MB = 100;
const ALLOWED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/jpg", "image/tiff"];


const IMPRESSION_OPTIONS = [
  { value: "Normal", label: "Normal" },
  { value: "Stencil", label: "Stencil" },
  { value: "Negative", label: "Negative" },
];

const MARKING_TYPE_OPTIONS = [
  { value: "TOWNMARK", label: ENTRY_LABELS.markingType.TOWNMARK },
  { value: "RATEMARK", label: ENTRY_LABELS.markingType.RATEMARK },
  { value: "AUXMARK", label: ENTRY_LABELS.markingType.AUXMARK },
];

/** docs/model.md lettering seed values; the dropdown is restricted to these. */
const LETTERING_SEED_NAMES = new Set([
  "Italic",
  "Serif",
  "Sans-serif",
  "Small",
  "Large",
  "Outline",
  "Bold",
  "Block",
  "Gothic",
]);

type ContributeMode = "new" | "edit-contribution" | "edit-marking";

const MODE_COPY: Record<ContributeMode, {
  h1: string;
  intro: string;
  card: string;
  button: string;
  toastTitle: string;
  toastBody: string;
}> = {
  "new": {
    h1: SUBMISSION_LABELS.action.submitNewMarking,
    intro: "Fill in the fields below. All fields match what reviewers see on the Submission Detail page.",
    card: "New Marking",
    button: SUBMISSION_LABELS.action.submitNewMarking,
    toastTitle: SUBMISSION_LABELS.toast.received,
    toastBody: "Your Marking has been submitted for approval. It will appear in Search after an editor approves it.",
  },
  "edit-contribution": {
    h1: "Edit and resubmit",
    intro: "Update your submission using the editor feedback, then submit to send it back for review.",
    card: "Edit submission",
    button: SUBMISSION_LABELS.action.resubmit,
    toastTitle: "Changes submitted",
    toastBody: "Your updated submission has been sent for review again.",
  },
  "edit-marking": {
    h1: "Submit Edit to Existing Marking",
    intro: "Edit requests are prefilled from the selected Marking and always go through the approval workflow before the published Marking is updated.",
    card: "Edit and Submit",
    button: SUBMISSION_LABELS.action.submitEditToMarking,
    toastTitle: "Submitted for review",
    toastBody: "Changes to published Marking fields are submitted for editor approval and do not update the catalog directly.",
  },
};

type MarkingTypeValue = "TOWNMARK" | "RATEMARK" | "AUXMARK";

function normalizeMarkingTypeValue(raw: string): MarkingTypeValue | "" {
  const v = String(raw || "").trim().toUpperCase().replace(/[\s/_-]+/g, "");
  if (v === "TOWNMARK") return "TOWNMARK";
  if (v === "RATEMARK") return "RATEMARK";
  if (v === "AUXMARK" || v.includes("AUX")) return "AUXMARK";
  return "";
}

const PAGE_NUMBER_RE = /^[A-Za-z0-9][A-Za-z0-9\s\-.,:;()/#]*$/;

/** Field-error key -> element id, in DOM order (drives scroll-to-first-error). */
const FIELD_ERROR_SCROLL_TARGETS: Array<[string, string]> = [
  ["markingType", "marking-type"],
  ["state", "state"],
  ["town", "town"],
  ["inscriptionText", "inscription-text"],
  ["shape", "shape"],
  ["color", "color"],
  ["dateFormat", "date-format"],
  ["widthMm", "width-mm"],
  ["heightMm", "height-mm"],
  ["lettering", "lettering"],
  ["rateValue", "rate-value"],
  ["images", "marking-images-input"],
];

function scrollToFirstError(errors: Record<string, string | undefined>) {
  for (const [key, id] of FIELD_ERROR_SCROLL_TARGETS) {
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

type ReferenceDetailInput = {
  pageNumber: string;
  citationUrl: string;
};

type ReferenceDetailPayload = {
  reference_work_id: number;
  page_number?: string;
  url?: string;
};

type ReferenceDetailFieldErrors = {
  pageNumber?: string;
  citationUrl?: string;
};

/**
 * Per-image tag persisted on Contribution.submitted_data. Marking-image
 * uploads can only ever be one of two things from the contributor's POV:
 *
 *   - "photograph" (default) -- a normal photo of the marking on a cover
 *   - "tracing"              -- a hand-traced or computer-traced diagram
 *
 * We retain the string form (rather than a bare boolean) for forward-compat
 * with any cover-image plumbing that may grow back later, and so the editor
 * portal can read the same key it always has (image_tag) and recognize a
 * "tracing" image without a schema migration.
 */
type UploadedImageTag = "photograph" | "tracing";

function tracingToTag(tracing: boolean): UploadedImageTag {
  return tracing ? "tracing" : "photograph";
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
    const s = String(value ?? "").trim();
    if (!s) continue;
    if (s.startsWith("[") && s.endsWith("]")) {
      try {
        const json = JSON.parse(s);
        if (Array.isArray(json)) {
          json.forEach((v) => values.push(v));
          continue;
        }
      } catch {
        // Ignore malformed payloads from legacy records.
      }
    }
    if (s.includes(",")) {
      s.split(",").forEach((chunk) => values.push(chunk.trim()));
      continue;
    }
    const n = Number.parseInt(s, 10);
    if (Number.isNaN(n) || n <= 0 || seen.has(n)) continue;
    seen.add(n);
    parsed.push(n);
  }

  return parsed;
}

function parseReferenceWorkDetails(raw: unknown): Record<number, ReferenceDetailInput> {
  if (raw == null) return {};
  let list: unknown = raw;
  if (typeof raw === "string") {
    const s = raw.trim();
    if (!s) return {};
    try {
      list = JSON.parse(s);
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
  const rows = list as unknown[];
  const out: Record<number, ReferenceDetailInput> = {};
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const rec = row as Record<string, unknown>;
    const idRaw = rec.reference_work_id ?? rec.referenceWorkId;
    const id = Number.parseInt(String(idRaw ?? ""), 10);
    if (Number.isNaN(id) || id <= 0) continue;
    out[id] = {
      pageNumber: String(rec.page_number ?? rec.pageNumber ?? "").trim(),
      citationUrl: String(rec.url ?? "").trim(),
    };
  }
  return out;
}

const Contribute = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { toast } = useToast();
  const markingFileInputRef = useRef<HTMLInputElement>(null);

  const user = useAuth();
  const { id: editMarkingIdParam } = useParams();
  const editMarkingIdRaw = editMarkingIdParam
    ? parseInt(String(editMarkingIdParam).replace(/^api-/, ""), 10)
    : NaN;
  const editMarkingId =
    Number.isFinite(editMarkingIdRaw) && editMarkingIdRaw > 0 ? editMarkingIdRaw : null;
  const editContributionIdRaw =
    location.state?.editContributionId ??
    (() => {
      const edit = searchParams.get("edit");
      if (!edit) return null;
      const n = parseInt(edit, 10);
      return Number.isNaN(n) ? null : n;
    })();
  const editContributionId = editContributionIdRaw ?? null;
  const mode: ContributeMode =
    editMarkingId != null
      ? "edit-marking"
      : editContributionId != null
        ? "edit-contribution"
        : "new";
  const isEditContribution = mode === "edit-contribution";
  const isEditMarking = mode === "edit-marking";
  const [editLoadDone, setEditLoadDone] = useState(!editContributionId);
  const [editLoadError, setEditLoadError] = useState<string | null>(null);
  // Status of the contribution we are editing (draft / needs_revision /
  // rejected / pending / approved). Only meaningful when mode is
  // "edit-contribution"; drives the header copy so a draft does not display
  // "use the editor feedback" wording.
  const [loadedContributionStatus, setLoadedContributionStatus] = useState<string | null>(null);
  // When resuming an edit-contribution draft that targets an existing
  // marking, this holds that marking's id. Drives both the staleness
  // banner and the page copy ("Submit edit to Marking" vs "Submit new
  // Marking"). Null for new-marking drafts.
  const [resumedEditMarkingId, setResumedEditMarkingId] = useState<number | null>(null);
  // Resolve page copy. For "edit-contribution" we further split on the
  // loaded contribution's status so a draft does not show the
  // "use the editor feedback" wording reserved for needs_revision /
  // rejected resubmissions, and further on whether the draft targets an
  // existing marking (edit-marking draft) vs a brand-new marking.
  const baseCopy = MODE_COPY[mode];
  // The "Resubmit" / "Edit and resubmit" / "use the editor feedback" wording
  // baked into MODE_COPY["edit-contribution"] only fits the
  // needs_revision / rejected resubmission flow. Drafts (and the brief
  // window before status loads) get a draft-style header + Submit button
  // instead. Status check is an explicit allowlist so a future status
  // value never silently falls back to "Resubmit".
  const isResubmissionStatus =
    loadedContributionStatus === "needs_revision" ||
    loadedContributionStatus === "rejected";
  const isResumingDraft = isEditContribution && !isResubmissionStatus;
  const isResumingMarkingEditDraft = isResumingDraft && resumedEditMarkingId != null;
  const copy = isResumingDraft
    ? isResumingMarkingEditDraft
      ? {
          ...baseCopy,
          h1: "Continue your draft",
          intro: "Pick up where you left off. Keep saving as a draft or submit your edit when you are ready for editor review.",
          card: "Edit draft",
          button: SUBMISSION_LABELS.action.submitEditToMarking,
          toastTitle: "Submitted for review",
          toastBody: "Changes to published Marking fields are submitted for editor approval and do not update the catalog directly.",
        }
      : {
          ...baseCopy,
          h1: "Continue your draft",
          intro: "Pick up where you left off. Keep saving as a draft or submit when you are ready for editor review.",
          card: "Edit draft",
          button: SUBMISSION_LABELS.action.submitNewMarking,
          toastTitle: SUBMISSION_LABELS.toast.received,
          toastBody:
            "Your Marking has been submitted for approval. It will appear in Search after an editor approves it.",
        }
    : baseCopy;
  const [loadingRecord, setLoadingRecord] = useState(isEditMarking);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [colorOptions, setColorOptions] = useState<ColorOption[]>([]);
  const [stateOptions, setStateOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [shapeOptions, setShapeOptions] = useState<ShapeOption[]>([]);
  const [postOffices, setPostOffices] = useState<PostOfficeOption[]>([]);
  const [loadingShapes, setLoadingShapes] = useState(true);
  const [shapeOptionsError, setShapeOptionsError] = useState<string | null>(null);
  const [loadingTowns, setLoadingTowns] = useState(false);
  const [townOptionsError, setTownOptionsError] = useState<string | null>(null);
  const [townFocused, setTownFocused] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Form state - all fields shown on Submission Detail
  const [state, setState] = useState("");

  const [town, setTown] = useState("");
  const [markingType, setMarkingType] = useState("");
  const [rateValue, setRateValue] = useState("");
  const [description, setDescription] = useState("");
  const [contributorComment, setContributorComment] = useState("");
  // One entry in the combined image gallery: "existing" wraps an already-saved
  // image (edit-marking or draft-resume); "new" wraps a freshly-picked file.
  type MarkingGalleryItem =
    | { kind: "existing"; key: string; url: string; tracing: boolean }
    | { kind: "new"; key: string; file: File; previewUrl: string; tracing: boolean };
  // Single ordered gallery: already-saved images interleaved with new uploads.
  // Reorder + set-default work across the whole list; the chosen order is sent as
  // image_order and applied at approval (see backend _reorder_metas_by_image_order
  // in common/api/v2/views.py). The edit form never writes to the catalog directly.
  const [gallery, setGallery] = useState<MarkingGalleryItem[]>([]);
  // Monotonic key source for new-upload items: previews resolve asynchronously,
  // so each new file needs a stable key independent of its array position.
  const newImageKeyCounter = useRef(0);
  const [removedExistingImageKeys, setRemovedExistingImageKeys] = useState<string[]>([]);
  // Timestamp of the marking record as observed when this edit session loaded
  // (Marking.modified_date). Stamped into save-as-draft payloads so a later
  // resume can detect upstream edits and warn the user before they overwrite.
  const [markingModifiedAtBaseline, setMarkingModifiedAtBaseline] = useState<string | null>(null);
  // True when resume detected that the marking was modified after the
  // baseline -- drives the staleness banner. Reset on every load.
  const [markingChangedSinceDraft, setMarkingChangedSinceDraft] = useState(false);
  // Whether the discard action is in flight (disables both banner buttons).
  const [discardingDraft, setDiscardingDraft] = useState(false);
  // Whether the hard-delete-draft action is in flight (disables the button).
  const [deletingDraft, setDeletingDraft] = useState(false);
  const [shape, setShape] = useState("");
  const [color, setColor] = useState("");
  const [widthMm, setWidthMm] = useState("");
  const [heightMm, setHeightMm] = useState("");
  const [isManuscript, setIsManuscript] = useState(false);
  const [isIrregular, setIsIrregular] = useState(false);
  const [impression, setImpression] = useState("Normal");
  const [inscriptionText, setInscriptionText] = useState("");
  const [catalogCode, setCatalogCode] = useState("");
  const [catalogCodeTouched, setCatalogCodeTouched] = useState(false);
  const [catalogCodeLoading, setCatalogCodeLoading] = useState(false);
  const [catalogCodeError, setCatalogCodeError] = useState<string | null>(null);
  const [selectedReferenceWorks, setSelectedReferenceWorks] = useState<ReferenceWorkRecord[]>([]);
  const [referenceDetailsById, setReferenceDetailsById] = useState<Record<number, ReferenceDetailInput>>({});
  const [referenceDetailErrorsById, setReferenceDetailErrorsById] = useState<Record<number, ReferenceDetailFieldErrors>>({});
  // New-upload files/previews/tags now live as "new" items inside `gallery`.
  const [letteringId, setLetteringId] = useState("");
  const [dateFormatIds, setDateFormatIds] = useState<string[]>([]);
  // ERD/LRD (earliest/latest recorded date) for markings sourced from another
  // catalog. Stored as MARKING DateSeen rows server-side and merged with
  // cover-derived dates at read time. *Baseline holds the value prefilled on
  // edit so we send a boundary only when the user changed it (a no-touch edit
  // must not re-write the catalog's date history).
  const [erd, setErd] = useState("");
  const [lrd, setLrd] = useState("");
  const [erdBaseline, setErdBaseline] = useState("");
  const [lrdBaseline, setLrdBaseline] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{
    markingType?: string;
    state?: string;
    town?: string;
    shape?: string;
    color?: string;
    widthMm?: string;
    heightMm?: string;
    images?: string;
    imageTags?: string;
    lettering?: string;
    dateFormat?: string;
    inscriptionText?: string;
    rateValue?: string;
    erd?: string;
    lrd?: string;
  }>({});

  // Contributor: lettering, framing, date format (loaded for all)
  const [letteringOptions, setLetteringOptions] = useState<LetteringOption[]>([]);
  const [dateFormatOptions, setDateFormatOptions] = useState<DateFormatOption[]>([]);
  const [catalogOptionsLoading, setCatalogOptionsLoading] = useState(false);
  const [referenceWorks, setReferenceWorks] = useState<ReferenceWorkRecord[]>([]);
  const [pendingReferenceWorkIds, setPendingReferenceWorkIds] = useState<number[]>([]);
  const [referenceWorksLoading, setReferenceWorksLoading] = useState(false);
  const [referenceWorksError, setReferenceWorksError] = useState<string | null>(null);
  const [referenceWorksFetched, setReferenceWorksFetched] = useState(false);

  const isStateEditor =
    user?.role === "editor" || user?.role === "administrator" || user?.is_superuser === true;

  const canEditCatalogCode = Boolean(isStateEditor);

  /** Town/City: letters, spaces, hyphens, apostrophes only */
  const sanitizeTown = (v: string) => v.replace(/[^a-zA-Z\s\-']/g, "");
  useEffect(() => {
    getColors()
      .then(setColorOptions)
      .catch(() => setColorOptions([{ id: 0, name: "Black", value: "" }]));
  }, []);

  useEffect(() => {
    if (color.trim()) return;
    const black = colorOptions.find((c) => c.name.trim().toLowerCase() === "black");
    if (black) setColor(black.name);
  }, [colorOptions, color]);

  useEffect(() => {
    setLoadingTowns(true);
    setTownOptionsError(null);
    getPostOffices()
      .then(setPostOffices)
      .catch((err) => {
        setTownOptionsError(err instanceof Error ? err.message : "Failed to load towns");
        setPostOffices([]);
      })
      .finally(() => setLoadingTowns(false));
  }, []);

  // State options for the Contribute form come from the Regions endpoint
  // (same source as Catalog Search), not from the post-office town-options
  // payload. Deriving states from post offices silently filtered the
  // dropdown down to whichever states currently have post_office_regions
  // links populated (commonly just Virginia in dev fixtures), even though
  // the contributor may have authorization to submit for any state.
  useEffect(() => {
    let cancelled = false;
    getRegions(false)
      .then((regions) => {
        if (cancelled) return;
        const seen = new Set<string>();
        const options = regions
          .map((r) => ({ value: String(r.value || "").trim(), label: String(r.label || "").trim() }))
          .filter((opt) => {
            if (!opt.value) return false;
            const key = opt.value.toLowerCase();
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          })
          .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
        setStateOptions(options);
      })
      .catch((err) => {
        if (cancelled) return;
        setTownOptionsError(err instanceof Error ? err.message : "Failed to load states");
        setStateOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setLoadingShapes(true);
    setShapeOptionsError(null);
    getShapes()
      .then(setShapeOptions)
      .catch((err) => {
        setShapeOptionsError(err instanceof Error ? err.message : "Failed to load marking shapes");
        setShapeOptions([]);
      })
      .finally(() => setLoadingShapes(false));
  }, []);

  /**
   * Default Shape rule: when Ratemark or Auxmark is selected and Manuscript
   * is False (non-handstamped), the catalog convention is that the marking is
   * a straight-line strike unless the contributor specifies otherwise. We
   * therefore prefill Shape with the SL ("Straight Line") option whenever:
   *
   *   - the type is RATEMARK or AUXMARK,
   *   - manuscript is "No",
   *   - the user hasn't already chosen a shape (we never override an explicit
   *     selection),
   *   - the shape options have finished loading (otherwise the SL row isn't
   *     in the dropdown yet and the prefill silently no-ops).
   *
   * Townmark intentionally does NOT trigger this default -- Townmarks have a
   * far wider variety of shapes and forcing SL would be misleading.
   */
  useEffect(() => {
    if (loadingShapes || shapeOptions.length === 0) return;
    if (isManuscript) return;
    if (shape.trim()) return;
    const t = normalizeMarkingTypeValue(markingType);
    if (t !== "RATEMARK" && t !== "AUXMARK") return;
    const slOption =
      shapeOptions.find(
        (opt) => String(opt.name).trim().toUpperCase() === "SL",
      ) ??
      shapeOptions.find((opt) =>
        String(opt.name).trim().toUpperCase().startsWith("SL"),
      ) ??
      shapeOptions.find((opt) =>
        String(opt.name).trim().toLowerCase().includes("straight line"),
      );
    if (slOption) setShape(slOption.name);
  }, [markingType, isManuscript, shape, shapeOptions, loadingShapes]);

  useEffect(() => {
    setCatalogOptionsLoading(true);
    Promise.all([getLetterings(), getDateFormats()])
      .then(([lettering, dateFmt]) => {
        setLetteringOptions(lettering);
        setDateFormatOptions(dateFmt);

        // Default Lettering = Normal when creating a new submission.
        if (!editContributionId) {
          if (!letteringId) {
            const normal = lettering.find((o) => String(o.name || "").trim().toLowerCase() === "normal");
            if (normal) setLetteringId(String(normal.id));
          }
        }
      })
      .catch(() => {
        setLetteringOptions([]);
        setDateFormatOptions([]);
      })
      .finally(() => setCatalogOptionsLoading(false));
  }, [editContributionId, letteringId]);

  useEffect(() => {
    if (referenceWorksFetched || referenceWorksLoading) return;
    setReferenceWorksLoading(true);
    setReferenceWorksError(null);
    getReferenceWorks()
      .then((rows) => setReferenceWorks(rows))
      .catch((err) => {
        setReferenceWorksError(
          err instanceof Error ? err.message : "Failed to load reference works"
        );
      })
      .finally(() => {
        setReferenceWorksFetched(true);
        setReferenceWorksLoading(false);
      });
  }, [referenceWorksFetched, referenceWorksLoading]);

  useEffect(() => {
    if (pendingReferenceWorkIds.length === 0 || referenceWorks.length === 0) return;
    const mapped = pendingReferenceWorkIds
      .map((id) => referenceWorks.find((w) => w.id === id))
      .filter((w): w is ReferenceWorkRecord => w != null);
    if (mapped.length > 0) {
      setSelectedReferenceWorks(mapped);
      setReferenceDetailsById((prev) => {
        const next = { ...prev };
        mapped.forEach((work) => {
          next[work.id] = next[work.id] ?? { pageNumber: "", citationUrl: "" };
        });
        return next;
      });
    }
    setPendingReferenceWorkIds([]);
  }, [pendingReferenceWorkIds, referenceWorks]);

  const fetchCatalogCodeSuggestion = async (force = false): Promise<string> => {
    if (!canEditCatalogCode) return "";
    const stateVal = state.trim();
    if (!stateVal) {
      setCatalogCodeError("Select a state before generating a catalog code.");
      return "";
    }
    setCatalogCodeLoading(true);
    setCatalogCodeError(null);
    try {
      const suggestion = await getDirectCatalogCodeSuggestion({
        subjectType: "MARKING",
        state: stateVal,
        referenceWorkIds: selectedReferenceWorks.map((work) => work.id),
        excludeId: editMarkingId ?? resumedEditMarkingId ?? null,
      });
      if (force || !catalogCodeTouched || !catalogCode.trim()) {
        setCatalogCode(suggestion.catalogCode);
      }
      return suggestion.catalogCode;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not generate catalog code.";
      setCatalogCodeError(message);
      throw err;
    } finally {
      setCatalogCodeLoading(false);
    }
  };

  useEffect(() => {
    if (!canEditCatalogCode) return;
    if (!state.trim()) return;
    if (catalogCodeTouched) return;
    let cancelled = false;
    setCatalogCodeLoading(true);
    setCatalogCodeError(null);
    getDirectCatalogCodeSuggestion({
      subjectType: "MARKING",
      state: state.trim(),
      referenceWorkIds: selectedReferenceWorks.map((work) => work.id),
      excludeId: editMarkingId ?? resumedEditMarkingId ?? null,
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
  }, [
    canEditCatalogCode,
    state,
    selectedReferenceWorks,
    editMarkingId,
    resumedEditMarkingId,
    catalogCodeTouched,
  ]);

  // Load contribution for edit-and-resubmit (rejected / needs_revision)
  useEffect(() => {
    if (!editContributionId) {
      setEditLoadDone(true);
      return;
    }
    let cancelled = false;
    getContribution(editContributionId)
      .then((contribution) => {
        if (cancelled) return;
        setLoadedContributionStatus(String(contribution.status ?? "").toLowerCase() || null);
        const sd = contribution.submittedData as Record<string, unknown> | undefined;
        if (!sd || typeof sd !== "object") {
          setEditLoadDone(true);
          return;
        }
        const getStr = (v: unknown) => (v != null && v !== "" ? String(v).trim() : "");
        const stateVal = getStr(sd.state);
        const townVal = getStr(sd.town);
        const shapeVal = getStr(sd.shape);
        const colorVal = getStr(sd.color);
        const typeVal = getStr(sd.type);
        const loadedCatalogCode = getStr(
          (sd as Record<string, unknown>).catalog_code ??
            (sd as Record<string, unknown>).catalogCode ??
            (sd as Record<string, unknown>).code,
        );
        setState(stateVal);
        setTown(townVal);
        if (loadedCatalogCode) {
          setCatalogCode(loadedCatalogCode);
          setCatalogCodeTouched(true);
        }
        if (typeVal) setMarkingType(typeVal);
        setContributorComment(getStr((sd as Record<string, unknown>).contributor_comment));
        setShape(shapeVal || "");
        setColor(colorVal || "");
        const wh = submittedDataToWidthHeightStrings(sd as Record<string, unknown>);
        setWidthMm(wh.width);
        setHeightMm(wh.height);
        setIsManuscript(sd.is_manuscript === true);
        const loadedImpression = getStr(sd.impression);
        setImpression(loadedImpression || "Normal");
        setIsIrregular(Boolean(sd.is_irreg));
        setInscriptionText(getStr(sd.inscription_txt));
        setDescription(getStr(sd.desc));
        setRateValue(getStr((sd as Record<string, unknown>).rate_val));
        const referenceWorkIds = parseReferenceWorkIds(
          (sd as Record<string, unknown>).reference_work_ids
        );
        const referenceDetails = parseReferenceWorkDetails(
          (sd as Record<string, unknown>).reference_work_details
        );
        setPendingReferenceWorkIds(referenceWorkIds);
        setReferenceDetailsById(referenceDetails);
        if (referenceWorkIds.length === 0) {
          setSelectedReferenceWorks([]);
        }
        const lid = sd.lettering_id ?? sd.lettering_style_id;
        setLetteringId(lid != null ? String(lid) : "");
        const dateFmtCode = getStr(sd.date_fmt ?? sd.dateFmt);
        if (dateFmtCode) {
          // dateFormatOptions is loaded by a separate effect; resolve once
          // the options arrive. For draft-rehydrate we look up by description
          // (the actual MD/YMD/... code) and fall back to no selection.
          const tryResolve = () => {
            const opt = dateFormatOptions.find(
              (o) =>
                String(o.description ?? "").trim().toLowerCase() === dateFmtCode.toLowerCase() ||
                String(o.name ?? "").trim().toLowerCase() === dateFmtCode.toLowerCase(),
            );
            setDateFormatIds(opt ? [String(opt.id)] : []);
          };
          tryResolve();
        } else {
          setDateFormatIds([]);
        }
        // Restore any ERD/LRD the contributor typed before saving the draft.
        // These ride submitted_data verbatim; no baseline (a draft has not
        // touched the catalog), so resubmitting sends them as a fresh boundary.
        setErd(getStr(sd.marking_erd));
        setLrd(getStr(sd.marking_lrd));
        setErdBaseline("");
        setLrdBaseline("");
        // Load prior images as "existing" gallery items (same path used by the
        // edit-marking flow) so the remove button records removals in
        // `removedExistingImageKeys` and the contributor can reorder / set the
        // default across them and any new uploads.
        const submittedMarkingImages = Array.isArray(sd.marking_images)
          ? (sd.marking_images as unknown[])
              .map((v) => (typeof v === "string" ? v.trim() : ""))
              .filter((v) => v.length > 0)
          : [];
        let existingUrls: string[] = submittedMarkingImages;
        if (existingUrls.length === 0) {
          const metas = Array.isArray(sd.marking_image_metas)
            ? (sd.marking_image_metas as Array<{ storage_filename?: string }>)
            : [];
          const baseUrl =
            (import.meta.env.VITE_IMAGE_URL ?? "").replace(/\/+$/, "") || "/media";
          existingUrls = metas
            .map((m) => {
              const sf = m?.storage_filename;
              return sf ? `${baseUrl}/${sf.replace(/^\/+/, "")}` : "";
            })
            .filter((u) => u.length > 0);
        }
        if (existingUrls.length > 0) {
          setGallery(
            existingUrls.map((url) => ({
              kind: "existing" as const,
              key: url,
              url,
              tracing: false,
            })),
          );
          setRemovedExistingImageKeys([]);
        }
        setEditLoadError(null);
        setEditLoadDone(true);

        // Marking-edit draft rehydration: if the draft targets an existing
        // marking, fetch the marking so (a) the existing-image rail shows
        // the marking's current images (the draft only snapshots field
        // edits, not images) and (b) we can compare its modified_date to
        // the baseline stamped in submitted_data and flag a stale draft.
        const editMarkingIdRaw = (sd as Record<string, unknown>).edit_marking_id;
        const editMarkingIdNum =
          typeof editMarkingIdRaw === "number"
            ? editMarkingIdRaw
            : typeof editMarkingIdRaw === "string" && editMarkingIdRaw.trim() !== ""
              ? Number(editMarkingIdRaw)
              : NaN;
        if (Number.isFinite(editMarkingIdNum) && editMarkingIdNum > 0) {
          setResumedEditMarkingId(editMarkingIdNum);
          const baselineRaw = (sd as Record<string, unknown>).marking_modified_at_baseline;
          const baseline = typeof baselineRaw === "string" ? baselineRaw : null;
          const removedRaw = (sd as Record<string, unknown>).removed_existing_image_keys;
          if (Array.isArray(removedRaw)) {
            setRemovedExistingImageKeys(removedRaw.map((k) => String(k)));
          }
          getMarkingByIdRaw(editMarkingIdNum)
            .then((m) => {
              if (cancelled || !m) return;
              if (existingUrls.length === 0 && Array.isArray(m.images)) {
                const rows = (m.images as unknown[])
                  .map((img) => {
                    const o = img as Record<string, unknown>;
                    const url = normalizeImageUrl(
                      typeof o.image_url === "string" ? o.image_url : null,
                    );
                    if (!url) return null;
                    const tracing = Boolean(o.is_tracing);
                    return { url, tracing };
                  })
                  .filter(
                    (row): row is { url: string; tracing: boolean } => row !== null,
                  );
                if (rows.length > 0) {
                  setGallery(
                    rows.map((r) => ({
                      kind: "existing" as const,
                      key: r.url,
                      url: r.url,
                      tracing: r.tracing,
                    })),
                  );
                }
              }
              const modified = typeof m.modified_date === "string" ? m.modified_date : null;
              if (baseline && modified && modified > baseline) {
                setMarkingChangedSinceDraft(true);
              }
            })
            .catch(() => {
              // Non-fatal: the form is still usable, just without the
              // marking's existing images and without the staleness check.
            });
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setEditLoadError(err instanceof Error ? err.message : "Failed to load contribution");
          setEditLoadDone(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // shapeOptions/colorOptions may not be loaded yet; we set shape/color as
    // strings. dateFormatOptions is read inside the closure for code -> id
    // resolution; adding it to deps would refire the entire fetch every
    // time options load, so we accept the snapshot at fetch time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editContributionId]);

  // Load Marking record for edit-marking mode (from /edit/:id).
  //
  // Before loading, dedupe against any existing open draft this user has
  // for the same marking. If one is found, redirect to its resume URL so
  // we never fork a parallel draft. The backend also enforces this in
  // ContributionSubmitView.post (collapse-on-create) -- this fetch is a
  // UX optimization so the user is not put through "fresh form, then
  // suddenly redirected" once they start typing.
  useEffect(() => {
    if (!isEditMarking || editMarkingId == null) return;
    let cancelled = false;
    setLoadingRecord(true);
    setRecordError(null);

    const loadMarking = () => {
      if (cancelled) return;
      getMarkingByIdRaw(editMarkingId)
      .then((data) => {
        if (cancelled) return;
        if (!data) {
          setRecordError("Record not found");
          return;
        }

        const existingRows = Array.isArray(data.images)
          ? (data.images as unknown[])
              .map((img) => {
                const o = img as Record<string, unknown>;
                const url = normalizeImageUrl(
                  typeof o.image_url === "string" ? o.image_url : null,
                );
                if (!url) return null;
                const tracing = Boolean(o.is_tracing);
                return { url, tracing };
              })
              .filter((row): row is { url: string; tracing: boolean } => row !== null)
          : [];
        setGallery(
          existingRows.map((r) => ({
            kind: "existing" as const,
            key: r.url,
            url: r.url,
            tracing: r.tracing,
          })),
        );
        setRemovedExistingImageKeys([]);

        const modifiedDate = typeof data.modified_date === "string" ? data.modified_date : null;
        setMarkingModifiedAtBaseline(modifiedDate);
        setMarkingChangedSinceDraft(false);

        const typeRaw = String(data.type ?? "").trim().toUpperCase();
        if (typeRaw === "RATEMARK") setMarkingType("RATEMARK");
        else if (typeRaw === "AUXMARK") setMarkingType("AUXMARK");
        else if (typeRaw === "TOWNMARK") setMarkingType("TOWNMARK");

        setState(typeof data.state === "string" ? data.state : "");
        setTown(sanitizeTown(typeof data.town === "string" ? data.town : ""));
        if (typeof data.code === "string" && data.code.trim()) {
          setCatalogCode(data.code.trim());
          setCatalogCodeTouched(true);
        }

        const shapeName = typeof data.shape_name === "string" ? data.shape_name : "";
        if (shapeName) {
          setShape(shapeName);
        } else if (data.shape != null) {
          const shapeById = shapeOptions.find((opt) => String(opt.id) === String(data.shape));
          setShape(shapeById?.name ?? "");
        } else {
          setShape("");
        }
        setColor(typeof data.color_name === "string" ? data.color_name : "");
        setWidthMm(String(data.width ?? "").replace(/[^0-9.]/g, ""));
        setHeightMm(String(data.height ?? "").replace(/[^0-9.]/g, ""));
        setIsManuscript(Boolean(data.is_manuscript));
        setIsIrregular(Boolean(data.is_irreg));
        const impressionRaw = String(data.impression ?? "").trim();
        const normalizedImpression = impressionRaw
          ? IMPRESSION_OPTIONS.find((opt) => opt.value.toLowerCase() === impressionRaw.toLowerCase())?.value
          : undefined;
        setImpression(normalizedImpression ?? "Normal");
        setInscriptionText(typeof data.inscription_txt === "string" ? data.inscription_txt : "");
        setDescription(typeof data.desc === "string" ? data.desc : "");
        setRateValue(String(data.rate_val ?? "").trim());

        setLetteringId(data.lettering != null ? String(data.lettering) : "");
        const dateFmt = String(data.date_fmt ?? "").trim();
        if (dateFmt) {
          const byCode = dateFormatOptions.find(
            (opt) =>
              String(opt.description ?? "").trim().toLowerCase() === dateFmt.toLowerCase() ||
              String(opt.name).trim().toLowerCase() === dateFmt.toLowerCase(),
          );
          setDateFormatIds(byCode ? [String(byCode.id)] : []);
        } else {
          setDateFormatIds([]);
        }

        // Prefill ERD/LRD from the marking's current earliest/latest. Baselines
        // let submit send a boundary only when the user actually changes it, so
        // a no-touch edit never re-writes the catalog's MARKING date rows.
        const erdRaw = typeof data.earliest_seen === "string" ? data.earliest_seen : "";
        const lrdRaw = typeof data.latest_seen === "string" ? data.latest_seen : "";
        setErd(erdRaw);
        setLrd(lrdRaw);
        setErdBaseline(erdRaw);
        setLrdBaseline(lrdRaw);
      })
      .catch(() => {
        if (!cancelled) setRecordError("Failed to load record");
      })
      .finally(() => {
        if (!cancelled) setLoadingRecord(false);
      });
    };

    listContributions({ status: "draft", ordering: "-created_at" })
      .then(({ rawItems }) => {
        if (cancelled) return;
        const match = rawItems.find((r) => {
          const sd = (r.submitted_data ?? r.submittedData) as
            | Record<string, unknown>
            | undefined;
          const epi = sd?.edit_marking_id;
          if (epi == null || epi === "") return false;
          return Number(epi) === Number(editMarkingId);
        });
        if (match && match.id != null) {
          navigate(`/contribute?edit=${match.id}`, { replace: true });
          return;
        }
        loadMarking();
      })
      .catch(() => {
        if (!cancelled) loadMarking();
      });
    return () => {
      cancelled = true;
    };
  }, [isEditMarking, editMarkingId, dateFormatOptions, shapeOptions, navigate]);

  const noAssignedStates = false;

  const townOptions = useMemo(() => {
    const normalizedState = state.trim().toLowerCase();
    if (!normalizedState) return [];
    const seen = new Set<string>();
    const towns: { value: string; label: string }[] = [];
    for (const facility of postOffices) {
      const facilityState = (facility.state || "").trim().toLowerCase();
      const facilityTown = (facility.town || "").trim();
      if (!facilityTown || facilityState !== normalizedState) continue;
      const key = facilityTown.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      towns.push({ value: facilityTown, label: facilityTown });
    }
    towns.sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
    return towns;
  }, [postOffices, state]);

  const selectedPostOfficeId = useMemo(() => {
    const normalizedState = state.trim().toLowerCase();
    const normalizedTown = town.trim().toLowerCase();
    if (!normalizedState || !normalizedTown) return null;
    const matched = postOffices.find((facility) => {
      const facilityState = (facility.state || "").trim().toLowerCase();
      const facilityTown = (facility.town || "").trim().toLowerCase();
      return facilityState === normalizedState && facilityTown === normalizedTown;
    });
    return matched?.id ?? null;
  }, [postOffices, state, town]);

  const inscriptionLabel = useMemo(() => {
    const t = normalizeMarkingTypeValue(markingType);
    if (t === "RATEMARK") return "Ratemark Text";
    if (t === "AUXMARK") return "Auxmark Text";
    if (t === "TOWNMARK") return "Townmark Text";
    return "Inscription Text";
  }, [markingType]);

  const normalizedMarkingType = useMemo(() => normalizeMarkingTypeValue(markingType), [markingType]);
  const isTownmark = normalizedMarkingType === "TOWNMARK";
  const isRatemark = normalizedMarkingType === "RATEMARK";
  const isAuxmark = normalizedMarkingType === "AUXMARK";
  const isHandstamped = !isManuscript;
  const showShapeField = isHandstamped;
  const showDateFormatField = isHandstamped && isTownmark;
  const showRateValueField = isRatemark;
  const showAnythingElseSection = isHandstamped && (isTownmark || isRatemark || isAuxmark);

  const selectedDateFormatSummary = useMemo(() => {
    if (dateFormatIds.length === 0) return "Select Date format";
    const selectedCodes = dateFormatOptions
      .filter((opt) => dateFormatIds.includes(String(opt.id)))
      .map((opt) => opt.description || opt.name);
    if (selectedCodes.length <= 2) return selectedCodes.join(", ");
    return `${selectedCodes.slice(0, 2).join(", ")} +${selectedCodes.length - 2} more`;
  }, [dateFormatIds, dateFormatOptions]);

  const processImageFiles = (files: File[]) => {
    const toAdd: File[] = [];
    const rejectedType: string[] = [];
    const rejectedSize: string[] = [];
    for (const file of files) {
      if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
        rejectedType.push(file.name);
        continue;
      }
      if (file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024) {
        rejectedSize.push(file.name);
        continue;
      }
      toAdd.push(file);
    }
    if (rejectedType.length) {
      toast({
        title: "Invalid file type",
        description: `Only PNG, JPG, or TIFF are allowed. Skipped: ${rejectedType.join(", ")}`,
        variant: "destructive",
      });
    }
    if (rejectedSize.length) {
      toast({
        title: "File too large",
        description: `Maximum size is ${MAX_IMAGE_SIZE_MB}MB per file. Skipped: ${rejectedSize.join(", ")}`,
        variant: "destructive",
      });
    }
    if (toAdd.length === 0) return;
    if (fieldErrors.images) {
      setFieldErrors((prev) => ({ ...prev, images: undefined }));
    }
    // Each new image starts as a "photograph" (tracing=false); the contributor
    // flips the per-image Tracing checkbox to mark it as a tracing diagram. The
    // preview (a data URL) resolves asynchronously, so the item is appended with
    // an empty previewUrl and patched by key once the FileReader finishes.
    const items = toAdd.map((file) => ({
      kind: "new" as const,
      key: `new-${newImageKeyCounter.current++}`,
      file,
      previewUrl: "",
      tracing: false,
    }));
    setGallery((prev) => [...prev, ...items]);
    items.forEach((item) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const previewUrl = reader.result as string;
        setGallery((prev) =>
          prev.map((g) =>
            g.kind === "new" && g.key === item.key ? { ...g, previewUrl } : g,
          ),
        );
      };
      reader.readAsDataURL(item.file);
    });
  };

  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length) return;
    processImageFiles(Array.from(files));
    e.target.value = "";
  };

  // Reorder + set-default + tracing + remove all act on the single combined
  // gallery, so they work uniformly across already-saved and newly-picked images.
  const removeGalleryAt = (index: number) => {
    setGallery((prev) => {
      const item = prev[index];
      if (!item) return prev;
      if (item.kind === "existing") {
        // Both catalog images and draft previews carry a URL whose tail is the
        // storage_filename; the backend tail-matches removed keys against it.
        setRemovedExistingImageKeys((keys) =>
          keys.includes(item.url) ? keys : [...keys, item.url],
        );
      }
      return prev.filter((_, i) => i !== index);
    });
    if (fieldErrors.images) {
      setFieldErrors((prev) => ({ ...prev, images: undefined }));
    }
    if (markingFileInputRef.current) markingFileInputRef.current.value = "";
  };

  const moveGalleryBy = (index: number, offset: -1 | 1) => {
    const target = index + offset;
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
      prev.map((item, i) => (i === index ? { ...item, tracing } : item)),
    );
  };

  const buildName = () => {
    const stateLabel = state.trim();
    return `${town.trim()}, ${stateLabel} ${shape}`.trim();
  };

  const isCircularType = (raw: string) => {
    const s = (raw || "").toLowerCase();
    return (
      s.includes("circle") ||
      s.includes("circular") ||
      s.includes("cds") ||
      s.includes("double circle") ||
      s.includes("single circle")
    );
  };

  const handleSubmit = async (
    e: FormEvent<HTMLFormElement> | MouseEvent<HTMLButtonElement>,
    saveAsDraft: boolean,
  ) => {
    e.preventDefault();

    if (!user) {
      toast({
        title: "Sign in required",
        description: "Please sign in to submit entries.",
        variant: "destructive",
      });
      return;
    }

    const stateVal = state.trim();
    const townVal = town.trim();
    const isManuscriptSelected = isManuscript;
    const shapeVal = isManuscriptSelected ? "" : shape.trim();
    const typeVal = normalizeMarkingTypeValue(markingType.trim()) || markingType.trim();
    const shapeIdVal = shapeOptions.find(
      (opt) => String(opt.name).trim().toLowerCase() === shapeVal.toLowerCase()
    )?.id;
    const colorVal = color.trim();
    const colorIdVal = colorOptions.find(
      (c) => c.name.trim().toLowerCase() === colorVal.toLowerCase()
    )?.id;
    const isCircular = isCircularType(shapeVal);
    const rateValueToSend = rateValue.trim();

    const errors: typeof fieldErrors = {};
    if (!saveAsDraft) {
      if (!typeVal) {
        errors.markingType = "Marking type is required";
      }
      if (!stateVal) {
        errors.state = "State is required";
      }
      if (!townVal) {
        errors.town = "Town/City is required";
      } else if (/[0-9]/.test(townVal)) {
        errors.town = "Town/City must contain only letters and spaces";
      }
      if (!isManuscriptSelected && !shapeVal) {
        errors.shape = "Shape is required when Manuscript is No";
      }
      if (!inscriptionText.trim()) {
        errors.inscriptionText = `${inscriptionLabel} is required`;
      }
      if (isRatemark && !rateValue.trim()) {
        errors.rateValue = "Rate Value is required for Ratemarks";
      } else if (showRateValueField && rateValueToSend && !/^\d+(?:\.\d{1,2})?$/.test(rateValueToSend)) {
        errors.rateValue = "Rate Value must be cents, like 3 or 3.5";
      }

      // At least one image must accompany every entry. The combined gallery
      // holds both kept existing images and new uploads, so a single emptiness
      // check covers all modes (new / edit-marking / edit-contribution).
      if (gallery.length === 0) {
        errors.images = "At least one image is required";
      }

      // Dimensions: only validated for handstamped markings. Manuscript markings
      // have no measurable shape, so we skip the check (and the fields are hidden).
      if (!isManuscriptSelected) {
        const effectiveHeight = isCircular ? widthMm : heightMm;
        const mmErr = validateMmPair(widthMm, effectiveHeight);
        if (mmErr.width) errors.widthMm = mmErr.width;
        if (mmErr.height && !isCircular) errors.heightMm = mmErr.height;
      }
    } else {
      // Drafts still require a state so the backend can map them to a
      // Collection, but other fields may be incomplete.
      if (!stateVal) {
        errors.state = "State is required to save a draft";
      }
    }

    // ERD/LRD are optional, but if both are given the earliest must not be
    // after the latest. (A type="date" input only yields a full ISO date or
    // empty, so format validity is otherwise guaranteed.)
    const erdForValidation = erd.trim();
    const lrdForValidation = lrd.trim();
    if (erdForValidation && lrdForValidation && erdForValidation > lrdForValidation) {
      errors.lrd = "Latest date must be on or after the earliest date";
    }

    const referenceDetailErrors: Record<number, ReferenceDetailFieldErrors> = {};
    for (const work of selectedReferenceWorks) {
      const detail = referenceDetailsById[work.id];
      if (!detail) continue;
      const page = detail.pageNumber.trim();
      const citationUrl = detail.citationUrl.trim();
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
    if (!saveAsDraft && Object.keys(referenceDetailErrors).length > 0) {
      toast({
        title: "Fix reference details",
        description: "Check page number and citation URL fields for selected references.",
        variant: "destructive",
      });
      return;
    }

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      scrollToFirstError(errors);
      return;
    }

    const referenceWorkIdsToSend = selectedReferenceWorks.map((work) => work.id);
    const referenceWorkDetailsToSend: ReferenceDetailPayload[] = selectedReferenceWorks.map((work) => {
      const detail = referenceDetailsById[work.id];
      return {
        reference_work_id: work.id,
        page_number: detail?.pageNumber?.trim() || undefined,
        url: detail?.citationUrl?.trim() || undefined,
      };
    });
    // Derive the ordered submission arrays from the combined gallery, in display
    // order: new-upload files + their positional tracing tags, plus an
    // image_order token per image ("__new__" for an upload, else the existing
    // image's key). The backend rebuilds the saved metas list in this order so
    // display_order at approval matches the on-screen order (index 0 = default).
    const allImageFiles: File[] = [];
    const orderedNewTags: string[] = [];
    const imageOrder: string[] = [];
    for (const item of gallery) {
      if (item.kind === "new") {
        allImageFiles.push(item.file);
        orderedNewTags.push(tracingToTag(item.tracing));
        imageOrder.push("__new__");
      } else {
        imageOrder.push(item.url);
      }
    }
    // existing_image_tags: {url: "tracing"|"photograph"} over kept existing
    // images. Emitted for every kept image (not just tracings) so unchecking
    // Tracing flips the value back to photograph.
    const existingTagMapEntries = gallery
      .filter((item) => item.kind === "existing")
      .map((item) => [item.url, tracingToTag(item.tracing)] as const);
    const oversized = allImageFiles.filter((f) => f.size > MAX_IMAGE_SIZE_MB * 1024 * 1024);
    if (oversized.length) {
      toast({
        title: "Image too large",
        description: `Please remove or replace images over ${MAX_IMAGE_SIZE_MB}MB.`,
        variant: "destructive",
      });
      return;
    }

    setSubmitting(true);
    try {
      let body: FormData | Record<string, unknown>;

      const derivedIsCircular = isCircularType(shapeVal);
      const widthToSend = widthMm.trim();
      const heightToSend = (derivedIsCircular ? widthMm : heightMm).trim();
      // Resolve the active date_fmt code (e.g. "MD", "YMD") from the
      // multi-select state. The picker stores DateFormatOption.id; the
      // backend wants the string code, which lives on .description.
      const dateFmtCode = (() => {
        const id = dateFormatIds[0];
        if (!id) return "";
        const opt = dateFormatOptions.find((o) => String(o.id) === String(id));
        return opt ? String(opt.description ?? "").trim() : "";
      })();
      const inscriptionToSend = inscriptionText.trim();
      let catalogCodeToSend = catalogCode.trim();
      if (canEditCatalogCode && !catalogCodeToSend) {
        catalogCodeToSend = await fetchCatalogCodeSuggestion(true);
        if (!catalogCodeToSend) {
          return;
        }
      }

      // ERD/LRD: send a boundary only when it is non-empty, parses to a date,
      // and differs from the value prefilled on edit. The backend sync is
      // additive (never deletes), so an unchanged or cleared field is a no-op
      // and the marking's existing catalog dates are preserved.
      const markingDateToSend = (
        value: string,
        baseline: string,
      ): { date: string; granularity: string } | null => {
        const trimmed = value.trim();
        if (!trimmed || trimmed === baseline.trim()) return null;
        const derived = deriveGranularityFromIso(trimmed);
        if (!derived) return null;
        return { date: derived.normalizedDate, granularity: derived.granularity };
      };
      const erdToSend = markingDateToSend(erd, erdBaseline);
      const lrdToSend = markingDateToSend(lrd, lrdBaseline);

      if (allImageFiles.length > 0 || isEditMarking) {
        const form = new FormData();
        if (isEditContribution && editContributionId != null) {
          form.append("edit_contribution_id", String(editContributionId));
        }
        if (isEditMarking && editMarkingId != null) {
          form.append("edit_marking_id", String(editMarkingId));
        }
        if (saveAsDraft) {
          form.append("save_as_draft", "true");
          if (isEditMarking && markingModifiedAtBaseline) {
            form.append("marking_modified_at_baseline", markingModifiedAtBaseline);
          }
        }
        if (selectedPostOfficeId != null) form.append("post_office_id", String(selectedPostOfficeId));
        form.append("state", stateVal);
        form.append("town", townVal);
        if (typeVal) form.append("type", typeVal);
        if (!isManuscriptSelected) form.append("shape", shapeVal);
        if (!isManuscriptSelected && shapeIdVal != null) form.append("shape_id", String(shapeIdVal));
        if (colorIdVal != null) form.append("color_id", String(colorIdVal));
        form.append("color", colorVal);
        if (widthToSend) form.append("width_mm", widthToSend);
        if (heightToSend) form.append("height_mm", heightToSend);
        form.append("is_manuscript", String(isManuscriptSelected));
        if (!isManuscriptSelected) {
          form.append("is_irreg", String(isIrregular));
          if (impression.trim()) form.append("impression", impression.trim());
        }
        if (showRateValueField && rateValueToSend) form.append("rate_val", rateValueToSend);
        if (description.trim()) {
          form.append("desc", description.trim());
        }
        if (canEditCatalogCode && catalogCodeToSend) {
          form.append("catalog_code", catalogCodeToSend);
        }
        if (inscriptionToSend) form.append("inscription_txt", inscriptionToSend);
        referenceWorkIdsToSend.forEach((id) => form.append("reference_work_ids[]", String(id)));
        if (referenceWorkDetailsToSend.length > 0) {
          form.append("reference_work_details", JSON.stringify(referenceWorkDetailsToSend));
        }
        if (!isManuscriptSelected && letteringId) form.append("lettering_style_id", letteringId);
        if (!isManuscriptSelected && letteringId) form.append("lettering_id", letteringId);
        if (showDateFormatField && dateFmtCode) form.append("date_fmt", dateFmtCode);
        if (erdToSend) {
          form.append("marking_erd", erdToSend.date);
          form.append("marking_erd_granularity", erdToSend.granularity);
        }
        if (lrdToSend) {
          form.append("marking_lrd", lrdToSend.date);
          form.append("marking_lrd_granularity", lrdToSend.granularity);
        }
        for (const file of allImageFiles) {
          form.append("marking_image", file, file.name);
        }
        form.append("marking_image_tags", JSON.stringify(orderedNewTags));
        if (imageOrder.length > 0) {
          form.append("image_order", JSON.stringify(imageOrder));
        }
        const trimmedComment = contributorComment.trim();
        if (trimmedComment) {
          form.append("contributor_comment", trimmedComment);
          form.append("comment_for_editor", trimmedComment);
        }
        if ((isEditMarking || isEditContribution) && removedExistingImageKeys.length > 0) {
          form.append("removed_existing_image_keys", JSON.stringify(removedExistingImageKeys));
        }
        if (isEditMarking && existingTagMapEntries.length > 0) {
          const existingTagMap: Record<string, UploadedImageTag> =
            Object.fromEntries(existingTagMapEntries);
          form.append("existing_image_tags", JSON.stringify(existingTagMap));
        }
        body = form;
        // Do not set Content-Type so browser sets multipart/form-data with boundary
      } else {
        const trimmedComment = contributorComment.trim();
        const existingTagMap: Record<string, UploadedImageTag> =
          Object.fromEntries(existingTagMapEntries);
        body = {
          ...(isEditContribution && editContributionId != null
            ? { edit_contribution_id: editContributionId }
            : {}),
          ...(isEditMarking && editMarkingId != null
            ? { edit_marking_id: editMarkingId }
            : {}),
          post_office_id: selectedPostOfficeId ?? undefined,
          state: stateVal,
          town: townVal,
          type: typeVal || undefined,
          shape: isManuscriptSelected ? null : shapeVal,
          shape_id: isManuscriptSelected ? null : shapeIdVal ?? null,
          color_id: colorIdVal ?? undefined,
          color: colorVal,
          width_mm: widthToSend || undefined,
          height_mm: heightToSend || undefined,
          is_manuscript: isManuscriptSelected,
          is_irreg: isManuscriptSelected ? null : isIrregular,
          impression: isManuscriptSelected ? null : impression.trim() || undefined,
          rate_val: showRateValueField ? rateValueToSend || undefined : undefined,
          desc: description.trim() || undefined,
          ...(canEditCatalogCode && catalogCodeToSend
            ? { catalog_code: catalogCodeToSend }
            : {}),
          inscription_txt: inscriptionToSend || undefined,
          reference_work_ids: referenceWorkIdsToSend.length > 0 ? referenceWorkIdsToSend : undefined,
          reference_work_details: referenceWorkDetailsToSend.length > 0 ? referenceWorkDetailsToSend : undefined,
          lettering_style_id: isManuscriptSelected ? null : letteringId ? Number(letteringId) : undefined,
          lettering_id: isManuscriptSelected ? null : letteringId ? Number(letteringId) : undefined,
          date_fmt: showDateFormatField && dateFmtCode ? dateFmtCode : undefined,
          ...(erdToSend
            ? { marking_erd: erdToSend.date, marking_erd_granularity: erdToSend.granularity }
            : {}),
          ...(lrdToSend
            ? { marking_lrd: lrdToSend.date, marking_lrd_granularity: lrdToSend.granularity }
            : {}),
          marking_image_tags: orderedNewTags,
          ...(imageOrder.length > 0 ? { image_order: imageOrder } : {}),
          ...(trimmedComment
            ? { contributor_comment: trimmedComment, comment_for_editor: trimmedComment }
            : {}),
          ...((isEditMarking || isEditContribution) && removedExistingImageKeys.length > 0
            ? { removed_existing_image_keys: removedExistingImageKeys }
            : {}),
          ...(isEditMarking && Object.keys(existingTagMap).length > 0
            ? { existing_image_tags: existingTagMap }
            : {}),
          ...(saveAsDraft ? { save_as_draft: true } : {}),
          ...(saveAsDraft && isEditMarking && markingModifiedAtBaseline
            ? { marking_modified_at_baseline: markingModifiedAtBaseline }
            : {}),
        };
      }

      const result = await createContribution(body);

      toast({
        title: saveAsDraft ? "Draft saved" : copy.toastTitle,
        description: saveAsDraft
          ? "Your draft has been saved. You can find it under My Submissions on your dashboard."
          : copy.toastBody,
      });

      if (saveAsDraft) {
        navigate("/dashboard", { state: { tab: "submissions" } });
        return;
      }

      if (isEditContribution) {
        const returnedId = result.contributionId ?? editContributionId;
        if (returnedId != null) {
          navigate(`/contribution/${returnedId}`, { state: { fromDashboard: true } });
          return;
        }
      }

      if (isEditMarking) {
        const fromDashboard = (location.state as Record<string, unknown> | null)?.fromDashboard;
        const fromDashboardDirect = (location.state as Record<string, unknown> | null)?.fromDashboardDirect;
        const fromDashboardViaDetail = (location.state as Record<string, unknown> | null)?.fromDashboardViaDetail;
        const fromSearch = (location.state as Record<string, unknown> | null)?.fromSearch;
        if (result.contributionId != null) {
          navigate(`/contribution/${result.contributionId}`, { state: { fromDashboard: true } });
        } else if (fromDashboardDirect || fromDashboard || fromDashboardViaDetail) {
          navigate("/dashboard");
        } else if (fromSearch) {
          navigate("/search");
        } else {
          navigate("/dashboard");
        }
        return;
      }

      // New-marking submit: return the user to wherever they came from
      // (Dashboard, Search, Index, etc.) so the post-submit landing is the
      // page that launched the contribute flow rather than an empty form.
      // Fall back to /dashboard when there is no prior history entry
      // (e.g. user opened /contribute directly).
      const navState = (location.state || {}) as Record<string, unknown>;
      const fromPath = typeof navState.from === "string" ? (navState.from as string) : "";
      if (fromPath) {
        navigate(fromPath);
      } else if (window.history.length > 1) {
        navigate(-1);
      } else {
        navigate("/dashboard");
      }
      return;
    } catch (err: unknown) {
      if ((err as { status?: number })?.status === 413) {
        toast({
          title: "Image too large",
          description: `The image file is too large for the server to accept. Please use an image under ${MAX_IMAGE_SIZE_MB}MB. If your image is already under ${MAX_IMAGE_SIZE_MB}MB, the server may have a lower limit-try a smaller file.`,
          variant: "destructive",
        });
        return;
      }
      toast({
        title: "Submission failed",
        description: err instanceof Error ? err.message : "Could not submit. Try again.",
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const effectiveShapeLabel = String(shape ?? "").trim();
  const isCircularShape = isCircularType(effectiveShapeLabel);

  const renderImageUploader = (label: string, helperText: string, required = false) => {
    const inputRef = markingFileInputRef;
    const hasAnyImage = gallery.length > 0;
    return (
      <div className="space-y-2">
        <Label>
          {label}
          {required ? <span className="text-destructive" aria-hidden="true"> *</span> : null}
        </Label>
        {required && fieldErrors.images && <p className="text-sm text-destructive">{fieldErrors.images}</p>}
        {fieldErrors.imageTags && <p className="text-sm text-destructive">{fieldErrors.imageTags}</p>}
        <input
          ref={inputRef}
          id="marking-images-input"
          type="file"
          accept={ALLOWED_IMAGE_TYPES.join(",")}
          multiple
          className="hidden"
          onChange={handleImageChange}
        />
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const dropped = Array.from(e.dataTransfer.files);
            if (dropped.length) processImageFiles(dropped);
          }}
          className="border-2 border-dashed border-border rounded-lg p-6 text-center hover:border-primary/50 transition-colors cursor-pointer"
        >
          {hasAnyImage ? (
            <div className="space-y-4">
              {/*
                One combined gallery: already-saved images and newly-uploaded
                files in a single ordered list. The arrows reorder and the star
                sets the default across the whole list; index 0 becomes the
                catalog Default (display_order=0 -- the Catalog Search thumbnail
                and the leading slide on the Record Detail gallery). The order is
                sent as image_order and applied at approval.
              */}
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                {gallery.map((item, i) => {
                  const isCatalogDefault = i === 0;
                  const src = item.kind === "existing" ? item.url : item.previewUrl;
                  const caption =
                    item.kind === "existing" ? "Current image" : item.file.name || "New image";
                  return (
                    <div
                      key={item.key}
                      className="relative rounded border bg-muted/30 overflow-hidden"
                    >
                      {src ? (
                        <img
                          src={src}
                          alt={`${label} image ${i + 1}`}
                          className="w-full aspect-square object-contain max-h-32"
                        />
                      ) : (
                        <div className="w-full aspect-square max-h-32 flex items-center justify-center text-xs text-muted-foreground">
                          Loading...
                        </div>
                      )}
                      <div className="flex items-center justify-between gap-2 px-2 py-1">
                        <span className="text-xs text-muted-foreground truncate">{caption}</span>
                        {isCatalogDefault && (
                          <span className="text-[10px] uppercase font-semibold text-primary whitespace-nowrap">
                            Default
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 px-2 pb-2">
                        <input
                          id={`gallery-tracing-${item.key}`}
                          type="checkbox"
                          className="h-4 w-4 accent-primary cursor-pointer"
                          checked={item.tracing}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setGalleryTracingAt(i, e.target.checked)}
                        />
                        <Label
                          htmlFor={`gallery-tracing-${item.key}`}
                          className="text-xs cursor-pointer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Tracing
                        </Label>
                      </div>
                      <div className="flex items-center justify-between gap-1 px-2 pb-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          aria-label="Move image left"
                          disabled={i === 0}
                          onClick={(e) => {
                            e.stopPropagation();
                            moveGalleryBy(i, -1);
                          }}
                        >
                          <ArrowLeft className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          aria-label="Move image right"
                          disabled={i === gallery.length - 1}
                          onClick={(e) => {
                            e.stopPropagation();
                            moveGalleryBy(i, 1);
                          }}
                        >
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          type="button"
                          variant={isCatalogDefault ? "secondary" : "ghost"}
                          size="icon"
                          className="h-7 w-7"
                          aria-label="Set as default catalog thumbnail"
                          title="Set as default catalog thumbnail"
                          disabled={isCatalogDefault}
                          onClick={(e) => {
                            e.stopPropagation();
                            setGalleryDefault(i);
                          }}
                        >
                          <Star
                            className={`h-3.5 w-3.5 ${isCatalogDefault ? "fill-current" : ""}`}
                          />
                        </Button>
                      </div>
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        className="absolute top-1 right-1 h-7 w-7 p-0"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeGalleryAt(i);
                        }}
                      >
                        x
                      </Button>
                    </div>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                The first image in this list becomes the Catalog Search
                thumbnail. Use the up / down arrows or the star button to
                change the order; check Tracing on hand-traced or computer-traced
                diagrams (everything else is treated as a photograph).
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  inputRef.current?.click();
                }}
              >
                Add more images
              </Button>
            </div>
          ) : (
            <>
              <Upload className="mx-auto h-10 w-10 text-muted-foreground mb-3" />
              <p className="text-sm text-muted-foreground mb-2">
                Click to upload or drag and drop (multiple allowed)
              </p>
              <p className="text-xs text-muted-foreground">{helperText}</p>
            </>
          )}
        </div>
      </div>
    );
  };


  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />

      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="mb-8">
            <h1 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-2">
              {copy.h1}
            </h1>
            <p className="text-muted-foreground">{copy.intro}</p>
          </div>

          {((isEditContribution && !editLoadDone) || (isEditMarking && loadingRecord)) && (
            <div className="flex items-center justify-center py-12 gap-2 text-muted-foreground">
              <Loader2 className="h-6 w-6 animate-spin" />
              <span>Loading record...</span>
            </div>
          )}

          {((isEditContribution && editLoadDone && editLoadError) ||
            (isEditMarking && !loadingRecord && recordError)) && (
            <Card className="border-destructive/50">
              <CardContent className="pt-6">
                <p className="text-destructive mb-4">{editLoadError ?? recordError}</p>
                <Button variant="outline" onClick={() => navigate(-1)}>
                  Go back
                </Button>
              </CardContent>
            </Card>
          )}

          {(mode === "new" ||
            (isEditContribution && editLoadDone && !editLoadError) ||
            (isEditMarking && !loadingRecord && !recordError)) && (
          <div className="grid lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2">
              <Card className="shadow-archival-lg">
                <CardHeader>
                  <CardTitle className="font-heading text-xl">{copy.card}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-6">
                  {noAssignedStates && (
                    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
                      You are signed in but do not have any states assigned to your account. Please contact an
                      administrator to be assigned to one or more states before submitting catalog entries.
                    </div>
                  )}
                  {!user && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 p-4 text-sm text-amber-800 dark:text-amber-200">
                      Sign in to add your name to the submission.{" "}
                      <Button variant="link" className="p-0 h-auto text-amber-700 dark:text-amber-300" onClick={() => navigate("/auth")}>
                        Sign in
                      </Button>
                    </div>
                  )}

                  {markingChangedSinceDraft && resumedEditMarkingId != null && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 p-4 text-sm text-amber-800 dark:text-amber-200 space-y-3">
                      <p className="leading-relaxed">
                        This marking has been updated since you saved this draft.
                        Review your changes before submitting -- your edits will overwrite the current values.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="border-amber-300 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40"
                          onClick={() => {
                            window.open(`/record/${resumedEditMarkingId}`, "_blank", "noopener,noreferrer");
                          }}
                        >
                          View current marking
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="border-amber-300 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40"
                          disabled={discardingDraft || editContributionId == null}
                          onClick={async () => {
                            if (editContributionId == null || resumedEditMarkingId == null) return;
                            const ok = window.confirm(
                              "Discard this draft and start over from the current marking? Your saved draft will be deleted.",
                            );
                            if (!ok) return;
                            setDiscardingDraft(true);
                            try {
                              await createContribution({
                                edit_contribution_id: editContributionId,
                                abandon_draft: "true",
                              });
                              navigate(`/contribute/edit/${resumedEditMarkingId}`, { replace: true });
                            } catch (err: unknown) {
                              toast({
                                title: "Could not discard draft",
                                description: err instanceof Error ? err.message : "Try again.",
                                variant: "destructive",
                              });
                            } finally {
                              setDiscardingDraft(false);
                            }
                          }}
                        >
                          Discard this draft and start fresh
                        </Button>
                      </div>
                    </div>
                  )}

                    <form
                      onSubmit={(e) => {
                        handleSubmit(e, false);
                      }}
                      className="space-y-6"
                      noValidate
                    >
                    <div className="space-y-2">
                      <Label htmlFor="marking-type">
                        Marking Type <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <Select
                        value={markingType}
                        onValueChange={(value) => {
                          setMarkingType(value);
                          if (fieldErrors.markingType) {
                            setFieldErrors((prev) => ({ ...prev, markingType: undefined }));
                          }
                        }}
                      >
                        <SelectTrigger
                          id="marking-type"
                          className={fieldErrors.markingType ? "border-destructive" : ""}
                        >
                          <SelectValue placeholder="Select marking type..." />
                        </SelectTrigger>
                        <SelectContent>
                          {MARKING_TYPE_OPTIONS.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {fieldErrors.markingType && (
                        <p className="text-sm text-destructive">{fieldErrors.markingType}</p>
                      )}
                    </div>

                    <div className="flex items-center gap-2">
                      <input
                        id="manuscript"
                        type="checkbox"
                        className="h-4 w-4 accent-primary"
                        checked={isManuscript}
                        onChange={(e) => {
                          const next = e.target.checked;
                          setIsManuscript(next);
                          if (next) {
                            setShape("");
                            setLetteringId("");
                            setImpression("");
                            setIsIrregular(false);
                            setWidthMm("");
                            setHeightMm("");
                            setFieldErrors((p) => ({
                              ...p,
                              shape: undefined,
                              widthMm: undefined,
                              heightMm: undefined,
                            }));
                          }
                        }}
                      />
                      <Label htmlFor="manuscript">Manuscript</Label>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="state">
                        State <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <SearchableSelect
                        id="state"
                        value={state}
                        onValueChange={(value) => {
                          setState(value);
                          if (fieldErrors.state) {
                            setFieldErrors((prev) => ({ ...prev, state: undefined }));
                          }
                        }}
                        placeholder="Select state..."
                        options={stateOptions}
                        loading={loadingTowns}
                        error={!!townOptionsError}
                        errorMessage={townOptionsError ?? "Failed to load states"}
                        searchPlaceholder="Search states..."
                        emptyMessage="No state found."
                        aria-label="State"
                        triggerClassName={fieldErrors.state ? "border-destructive" : ""}
                      />
                      {fieldErrors.state && (
                        <p className="text-sm text-destructive">{fieldErrors.state}</p>
                      )}
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="town">
                        Town/City <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <div className="relative">
                        <Input
                          id="town"
                          type="text"
                          autoComplete="off"
                          value={town}
                          onChange={(e) => {
                            setTown(sanitizeTown(e.target.value));
                            if (fieldErrors.town) {
                              setFieldErrors((prev) => ({ ...prev, town: undefined }));
                            }
                          }}
                          onFocus={() => setTownFocused(true)}
                          onBlur={() => {
                            // Delay so click on a suggestion can register first.
                            window.setTimeout(() => setTownFocused(false), 120);
                          }}
                          placeholder={state ? "Type a town or pick a suggestion" : "Select state first"}
                          aria-label="Town or city"
                          aria-autocomplete="list"
                          aria-expanded={townFocused && !!state}
                          disabled={!state}
                          className={fieldErrors.town ? "border-destructive" : ""}
                        />
                        {townFocused && state && (() => {
                          const needle = town.trim().toLowerCase();
                          const matches = needle
                            ? townOptions.filter((opt) =>
                                opt.label.toLowerCase().includes(needle),
                              )
                            : townOptions;
                          const limited = matches.slice(0, 25);
                          if (limited.length === 0) return null;
                          return (
                            <ul
                              role="listbox"
                              className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-popover py-1 shadow-md"
                            >
                              {limited.map((opt) => (
                                <li
                                  key={opt.value}
                                  role="option"
                                  aria-selected={opt.value === town}
                                  className="cursor-pointer px-3 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground"
                                  onMouseDown={(e) => {
                                    // Use mousedown so it fires before the input blur.
                                    e.preventDefault();
                                    setTown(sanitizeTown(opt.value));
                                    setTownFocused(false);
                                    if (fieldErrors.town) {
                                      setFieldErrors((prev) => ({ ...prev, town: undefined }));
                                    }
                                  }}
                                >
                                  {opt.label}
                                </li>
                              ))}
                            </ul>
                          );
                        })()}
                      </div>
                      {loadingTowns && state ? (
                        <p className="text-xs text-muted-foreground">Loading town suggestions...</p>
                      ) : null}
                      {townOptionsError && state ? (
                        <p className="text-xs text-destructive">{townOptionsError}</p>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          Start typing to see suggestions for the selected state. Unrecognized names are accepted as new towns.
                        </p>
                      )}
                      {fieldErrors.town && (
                        <p className="text-sm text-destructive">{fieldErrors.town}</p>
                      )}
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="inscription-text">
                        {inscriptionLabel} <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <Textarea
                        id="inscription-text"
                        placeholder="Exact text inscribed on the marking device (abbreviations/annotations)."
                        value={inscriptionText}
                        onChange={(e) => {
                          setInscriptionText(e.target.value);
                          if (fieldErrors.inscriptionText) {
                            setFieldErrors((prev) => ({ ...prev, inscriptionText: undefined }));
                          }
                        }}
                        rows={3}
                        className={fieldErrors.inscriptionText ? "border-destructive" : ""}
                      />
                      <p className="text-sm text-muted-foreground">{INSCRIPTION_TEXT_HELP}</p>
                      {fieldErrors.inscriptionText && (
                        <p className="text-sm text-destructive">{fieldErrors.inscriptionText}</p>
                      )}
                    </div>
                    {showShapeField && <div className="space-y-2">
                      <Label htmlFor="shape">
                        Shape
                        {!isManuscript ? (
                          <span className="text-destructive" aria-hidden="true">*</span>
                        ) : null}
                      </Label>
                      <SearchableSelect
                        id="shape"
                        value={shape}
                        onValueChange={(value) => {
                          setShape(value);
                          if (fieldErrors.shape) {
                            setFieldErrors((prev) => ({ ...prev, shape: undefined }));
                          }
                        }}
                        placeholder="Select shape..."
                        options={shapeOptions.map((t) => ({ value: t.name, label: t.name }))}
                        loading={loadingShapes}
                        error={!!shapeOptionsError}
                        errorMessage={shapeOptionsError ?? "Failed to load marking shapes"}
                        searchPlaceholder="Search shapes..."
                        emptyMessage="No shape found."
                        aria-label="Marking shape"
                        triggerClassName={fieldErrors.shape ? "border-destructive" : ""}
                      />
                      {fieldErrors.shape && (
                        <p className="text-sm text-destructive">{fieldErrors.shape}</p>
                      )}
                    </div>}

                    <div className="space-y-2">
                      <Label htmlFor="color">Color</Label>
                      <Select
                        value={color}
                        onValueChange={(value) => {
                          setColor(value);
                          if (fieldErrors.color) {
                            setFieldErrors((prev) => ({ ...prev, color: undefined }));
                          }
                        }}
                      >
                        <SelectTrigger
                          id="color"
                          className={fieldErrors.color ? "border-destructive" : ""}
                        >
                          <SelectValue placeholder="Select color..." />
                        </SelectTrigger>
                        <SelectContent>
                          {colorOptions.map((c) => (
                            <SelectItem key={c.id} value={c.name}>
                              {c.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {fieldErrors.color && (
                        <p className="text-sm text-destructive">{fieldErrors.color}</p>
                      )}
                    </div>
                    {showDateFormatField && <div className="space-y-2">
                      <Label>Date format</Label>
                      <p className="text-sm text-muted-foreground">{DATE_FORMAT_HELP}</p>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            type="button"
                            variant="outline"
                            className={`w-full justify-between ${fieldErrors.dateFormat ? "border-destructive" : ""}`}
                            disabled={catalogOptionsLoading}
                          >
                            {catalogOptionsLoading ? "Loading..." : selectedDateFormatSummary}
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent className="w-[var(--radix-dropdown-menu-trigger-width)] max-h-64 overflow-auto">
                          <DropdownMenuRadioGroup
                            value={dateFormatIds[0] ?? ""}
                            onValueChange={(value) => {
                              setDateFormatIds(value ? [value] : []);
                              setFieldErrors((prev) => ({ ...prev, dateFormat: undefined }));
                            }}
                          >
                            {dateFormatOptions.map((opt) => {
                              const value = String(opt.id);
                              // opt.description is the code (e.g. "MD"); opt.name
                              // is its meaning (e.g. "Month / Day"). Show both so
                              // MD/MDD/YMDD are self-explanatory at the point of choice.
                              const code = opt.description || opt.name;
                              const meaning =
                                opt.name && opt.name !== opt.description ? opt.name : "";
                              return (
                                <DropdownMenuRadioItem key={opt.id} value={value}>
                                  {meaning ? `${code} — ${meaning}` : code}
                                </DropdownMenuRadioItem>
                              );
                            })}
                          </DropdownMenuRadioGroup>
                        </DropdownMenuContent>
                      </DropdownMenu>
                      {fieldErrors.dateFormat && <p className="text-sm text-destructive">{fieldErrors.dateFormat}</p>}
                    </div>}
                    <div className="space-y-2">
                      <Label>Recorded date range</Label>
                      <p className="text-sm text-muted-foreground">
                        Optional. Earliest (ERD) and latest (LRD) recorded dates for a
                        marking added from another catalog. Leave blank to let the dates
                        come from uploaded covers. Enter a year (e.g. 1845) for a year-only date.
                      </p>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1">
                          <Label htmlFor="marking-erd" className="text-sm font-normal">Earliest (ERD)</Label>
                          <Input
                            id="marking-erd"
                            type="date"
                            value={erd}
                            onChange={(e) => {
                              setErd(e.target.value);
                              setFieldErrors((prev) => ({ ...prev, erd: undefined, lrd: undefined }));
                            }}
                            disabled={submitting}
                            className={fieldErrors.erd ? "border-destructive" : ""}
                          />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor="marking-lrd" className="text-sm font-normal">Latest (LRD)</Label>
                          <Input
                            id="marking-lrd"
                            type="date"
                            value={lrd}
                            onChange={(e) => {
                              setLrd(e.target.value);
                              setFieldErrors((prev) => ({ ...prev, lrd: undefined }));
                            }}
                            disabled={submitting}
                            className={fieldErrors.lrd ? "border-destructive" : ""}
                          />
                        </div>
                      </div>
                      {fieldErrors.lrd && <p className="text-sm text-destructive">{fieldErrors.lrd}</p>}
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="description">Description</Label>
                      <Textarea
                        id="description"
                        placeholder="Details about the marking..."
                        rows={4}
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                      />
                    </div>

                    {showRateValueField && <div className="space-y-2">
                      <Label htmlFor="rate-value">
                        Rate Value (cents) <span className="text-destructive" aria-hidden="true">*</span>
                      </Label>
                      <Input
                        id="rate-value"
                        type="text"
                        placeholder="e.g. 3 or 3.5"
                        value={rateValue}
                        onChange={(e) => {
                          setRateValue(e.target.value);
                          if (fieldErrors.rateValue) {
                            setFieldErrors((prev) => ({ ...prev, rateValue: undefined }));
                          }
                        }}
                        className={fieldErrors.rateValue ? "border-destructive" : ""}
                      />
                      {fieldErrors.rateValue && (
                        <p className="text-sm text-destructive">{fieldErrors.rateValue}</p>
                      )}
                    </div>}

                    {canEditCatalogCode && (
                      <div className="space-y-2">
                        <Label htmlFor="catalog-code">Catalog code</Label>
                        <Input
                          id="catalog-code"
                          value={catalogCode}
                          onChange={(e) => {
                            setCatalogCode(e.target.value);
                            setCatalogCodeTouched(true);
                            if (catalogCodeError) setCatalogCodeError(null);
                          }}
                          onBlur={() => {
                            if (!catalogCode.trim()) {
                              setCatalogCodeTouched(false);
                              void fetchCatalogCodeSuggestion(true);
                            }
                          }}
                          placeholder={catalogCodeLoading ? "Generating..." : "e.g. APMC-VA-M0001"}
                          disabled={submitting || catalogCodeLoading}
                        />
                        {catalogCodeError ? (
                          <p className="text-sm text-destructive">{catalogCodeError}</p>
                        ) : null}
                      </div>
                    )}

                    {isHandstamped && (
                      <>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                          <div className="space-y-2">
                            <Label htmlFor="width-mm">
                              {isCircularShape ? "Diameter (mm)" : "Width (mm)"}
                            </Label>
                            <Input
                              id="width-mm"
                              type="text"
                              inputMode="decimal"
                              placeholder="e.g. 34"
                              value={widthMm}
                              onChange={(e) => {
                                setWidthMm(sanitizeMmInput(e.target.value));
                                if (fieldErrors.widthMm || fieldErrors.heightMm) {
                                  setFieldErrors((prev) => ({
                                    ...prev,
                                    widthMm: undefined,
                                    heightMm: undefined,
                                  }));
                                }
                              }}
                              className={fieldErrors.widthMm ? "border-destructive" : ""}
                              aria-label={isCircularShape ? "Diameter in millimetres" : "Width in millimetres"}
                            />
                            {fieldErrors.widthMm && (
                              <p className="text-sm text-destructive">{fieldErrors.widthMm}</p>
                            )}
                          </div>
                          {!isCircularShape && (
                            <div className="space-y-2">
                              <Label htmlFor="height-mm">Height (mm)</Label>
                              <Input
                                id="height-mm"
                                type="text"
                                inputMode="decimal"
                                placeholder="e.g. 28"
                                value={heightMm}
                                onChange={(e) => {
                                  setHeightMm(sanitizeMmInput(e.target.value));
                                  if (fieldErrors.widthMm || fieldErrors.heightMm) {
                                    setFieldErrors((prev) => ({
                                      ...prev,
                                      widthMm: undefined,
                                      heightMm: undefined,
                                    }));
                                  }
                                }}
                                className={fieldErrors.heightMm ? "border-destructive" : ""}
                                aria-label="Height in millimetres"
                              />
                              {fieldErrors.heightMm && (
                                <p className="text-sm text-destructive">{fieldErrors.heightMm}</p>
                              )}
                            </div>
                          )}
                        </div>
                        {!isCircularShape && (
                          <p className="text-xs text-muted-foreground -mt-2">
                            Optional. If you enter one, enter both. Stored on the catalog as width x height (mm).
                          </p>
                        )}
                      </>
                    )}

                    <div className="space-y-3">
                      <div className="space-y-2">
                        <Label>Reference Works</Label>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              variant="outline"
                              className="w-full justify-between"
                              disabled={referenceWorksLoading}
                            >
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
                              <div className="px-2 py-1.5 text-sm text-destructive">
                                {referenceWorksError}
                              </div>
                            ) : referenceWorks.length === 0 ? (
                              <div className="px-2 py-1.5 text-sm text-muted-foreground">
                                No reference works found.
                              </div>
                            ) : (
                              referenceWorks.map((work) => {
                                const checked = selectedReferenceWorks.some((w) => w.id === work.id);
                                return (
                                  <DropdownMenuCheckboxItem
                                    key={work.id}
                                    checked={checked}
                                    onCheckedChange={(next) => {
                                      setPendingReferenceWorkIds([]);
                                      if (next) {
                                        setSelectedReferenceWorks((prev) =>
                                          prev.some((w) => w.id === work.id) ? prev : [...prev, work],
                                        );
                                        setReferenceDetailsById((prev) => ({
                                          ...prev,
                                          [work.id]: prev[work.id] ?? { pageNumber: "", citationUrl: "" },
                                        }));
                                        setReferenceDetailErrorsById((prev) => ({
                                          ...prev,
                                          [work.id]: prev[work.id] ?? {},
                                        }));
                                        return;
                                      }
                                      setSelectedReferenceWorks((prev) => prev.filter((w) => w.id !== work.id));
                                      setReferenceDetailsById((prev) => {
                                        const updated = { ...prev };
                                        delete updated[work.id];
                                        return updated;
                                      });
                                      setReferenceDetailErrorsById((prev) => {
                                        const updated = { ...prev };
                                        delete updated[work.id];
                                        return updated;
                                      });
                                    }}
                                  >
                                    {formatReferenceWorkLabel(work)}
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
                            <div
                              key={work.id}
                              className="rounded-md border border-border px-3 py-2 space-y-3"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <p className="text-sm font-medium text-foreground truncate">
                                    {formatReferenceWorkLabel(work, "Untitled")}
                                  </p>
                                  <p className="text-xs text-muted-foreground truncate">
                                    {[work.authorship, work.publisher]
                                      .filter((x) => (x ?? "").trim() !== "")
                                      .join(" -- ")}
                                  </p>
                                </div>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => {
                                    setPendingReferenceWorkIds([]);
                                    setSelectedReferenceWorks((prev) =>
                                      prev.filter((w) => w.id !== work.id),
                                    );
                                    setReferenceDetailsById((prev) => {
                                      const next = { ...prev };
                                      delete next[work.id];
                                      return next;
                                    });
                                    setReferenceDetailErrorsById((prev) => {
                                      const next = { ...prev };
                                      delete next[work.id];
                                      return next;
                                    });
                                  }}
                                >
                                  Remove
                                </Button>
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                <div className="space-y-1">
                                  <Label htmlFor={`reference-page-${work.id}`} className="text-xs">
                                    Page number
                                  </Label>
                                  <Input
                                    id={`reference-page-${work.id}`}
                                    placeholder="e.g. 42, plate 3"
                                    value={referenceDetailsById[work.id]?.pageNumber ?? ""}
                                    className={referenceDetailErrorsById[work.id]?.pageNumber ? "border-destructive" : ""}
                                    onChange={(e) =>
                                      {
                                        setReferenceDetailsById((prev) => ({
                                          ...prev,
                                          [work.id]: {
                                            pageNumber: e.target.value,
                                            citationUrl: prev[work.id]?.citationUrl ?? "",
                                          },
                                        }));
                                        setReferenceDetailErrorsById((prev) => ({
                                          ...prev,
                                          [work.id]: {
                                            ...prev[work.id],
                                            pageNumber: undefined,
                                          },
                                        }));
                                      }
                                    }
                                  />
                                  {referenceDetailErrorsById[work.id]?.pageNumber && (
                                    <p className="text-xs text-destructive">
                                      {referenceDetailErrorsById[work.id]?.pageNumber}
                                    </p>
                                  )}
                                </div>
                                <div className="space-y-1">
                                  <Label htmlFor={`reference-url-${work.id}`} className="text-xs">
                                    Citation URL
                                  </Label>
                                  <Input
                                    id={`reference-url-${work.id}`}
                                    type="url"
                                    placeholder="https://..."
                                    value={referenceDetailsById[work.id]?.citationUrl ?? ""}
                                    className={referenceDetailErrorsById[work.id]?.citationUrl ? "border-destructive" : ""}
                                    onChange={(e) =>
                                      {
                                        setReferenceDetailsById((prev) => ({
                                          ...prev,
                                          [work.id]: {
                                            pageNumber: prev[work.id]?.pageNumber ?? "",
                                            citationUrl: e.target.value,
                                          },
                                        }));
                                        setReferenceDetailErrorsById((prev) => ({
                                          ...prev,
                                          [work.id]: {
                                            ...prev[work.id],
                                            citationUrl: undefined,
                                          },
                                        }));
                                      }
                                    }
                                  />
                                  {referenceDetailErrorsById[work.id]?.citationUrl && (
                                    <p className="text-xs text-destructive">
                                      {referenceDetailErrorsById[work.id]?.citationUrl}
                                    </p>
                                  )}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      <p className="text-xs text-muted-foreground">
                        Optional. Select one or more established reference works to associate with this submission.
                      </p>
                    </div>

                    {showAnythingElseSection && <div className="space-y-3">
                      {/* <Label>Anything else?</Label> */}
                      <div className="space-y-2">
                        <Label>
                          <span
                            className="cursor-help border-b border-dotted border-muted-foreground/40"
                            title="Lettering style describes the shape/appearance of the letters used in the marking text."
                          >
                            Lettering style
                          </span>
                        </Label>
                        <Select value={letteringId} onValueChange={(v) => { setLetteringId(v); setFieldErrors((prev) => ({ ...prev, lettering: undefined })); }} disabled={catalogOptionsLoading}>
                          <SelectTrigger className={fieldErrors.lettering ? "border-destructive" : ""}>
                            <SelectValue placeholder={catalogOptionsLoading ? "Loading..." : "Select lettering style"} />
                          </SelectTrigger>
                          <SelectContent>
                            {letteringOptions
                              .filter((opt) => LETTERING_SEED_NAMES.has(opt.name))
                              .map((opt) => (
                                <SelectItem key={opt.id} value={String(opt.id)}>{opt.name}</SelectItem>
                              ))}
                            {(() => {
                              const selected = letteringOptions.find((opt) => String(opt.id) === letteringId);
                              if (!selected || LETTERING_SEED_NAMES.has(selected.name)) return null;
                              return (
                                <SelectItem key={`legacy-${selected.id}`} value={String(selected.id)}>
                                  {selected.name} (legacy)
                                </SelectItem>
                              );
                            })()}
                          </SelectContent>
                        </Select>
                        {fieldErrors.lettering && <p className="text-sm text-destructive">{fieldErrors.lettering}</p>}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="impression">Impression</Label>
                        <Select value={impression} onValueChange={setImpression}>
                          <SelectTrigger id="impression">
                            <SelectValue placeholder="Select impression..." />
                          </SelectTrigger>
                          <SelectContent>
                            {IMPRESSION_OPTIONS.map((opt) => (
                              <SelectItem key={opt.value} value={opt.value}>
                                {opt.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="flex items-center gap-2">
                        <input
                          id="is-irregular"
                          type="checkbox"
                          className="h-4 w-4 accent-primary"
                          checked={isIrregular}
                          onChange={(e) => setIsIrregular(e.target.checked)}
                        />
                        <Label htmlFor="is-irregular">Is Irregular</Label>
                      </div>
                    </div>}

                    {renderImageUploader(
                      "Marking Images",
                      `PNG, JPG, or TIFF up to ${MAX_IMAGE_SIZE_MB}MB each`,
                      true,
                    )}

                    <div className="space-y-2">
                      <Label htmlFor="contributor-comment">Comment for editor</Label>
                      <Textarea
                        id="contributor-comment"
                        placeholder="Optional. Anything else you want the reviewing editor to know."
                        rows={3}
                        value={contributorComment}
                        onChange={(e) => setContributorComment(e.target.value)}
                      />
                    </div>

                    <div className="flex flex-col sm:flex-row gap-3">
                      <Button
                        type="button"
                        variant="outline"
                        className="w-full sm:flex-1"
                        disabled={submitting || noAssignedStates}
                        onClick={(e) => {
                          handleSubmit(e, true);
                        }}
                      >
                        Save as Draft
                      </Button>
                      <Button
                        type="submit"
                        className="w-full sm:flex-1 bg-primary text-primary-foreground hover:bg-primary/90"
                        disabled={submitting || noAssignedStates}
                      >
                        {submitting ? "Submitting..." : copy.button}
                      </Button>
                    </div>
                    {isResumingDraft && editContributionId != null && (
                      <div className="pt-2 flex justify-end">
                        <Button
                          type="button"
                          variant="destructive"
                          className="w-full sm:w-auto"
                          disabled={submitting || deletingDraft}
                          onClick={async () => {
                            const ok = window.confirm(
                              "Delete this draft? This can't be undone.",
                            );
                            if (!ok) return;
                            setDeletingDraft(true);
                            try {
                              await deleteDraftContribution(editContributionId);
                              toast({ title: "Draft deleted" });
                              navigate("/dashboard");
                            } catch (err: unknown) {
                              toast({
                                title: "Could not delete draft",
                                description: err instanceof Error ? err.message : "Try again.",
                                variant: "destructive",
                              });
                              setDeletingDraft(false);
                            }
                          }}
                        >
                          <Trash2 className="mr-1.5 h-4 w-4" />
                          {deletingDraft ? "Deleting..." : "Delete Draft"}
                        </Button>
                      </div>
                    )}
                  </form>

                  <p className="text-xs text-muted-foreground">
                    Required: Marking Type, Manuscript, State, Town/City, Inscription Text, Shape (when not Manuscript), and at least one image.
                  </p>
                </CardContent>
              </Card>
            </div>

            <div className="space-y-6">
              <Card className="shadow-archival-md">
                <CardHeader>
                  <CardTitle className="font-heading text-lg">Submission Guidelines</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm text-muted-foreground">
                  {MARKING_SUBMISSION_GUIDELINES.map((g) => (
                    <p key={g.label} className="leading-relaxed">
                      <strong className="text-foreground">{g.label}:</strong> {g.body}
                    </p>
                  ))}
                </CardContent>
              </Card>

              {/* My Submissions list intentionally lives on the separate /dashboard page.
                  Keep this card reserved or remove entirely as needed. */}
            </div>
          </div>
          )}
        </div>
      </div>

      <Footer />
    </div>
  );
};

export default Contribute;
