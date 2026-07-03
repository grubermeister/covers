import apiClient, { ensureCsrfToken } from "@/lib/api";
import { listContributions } from "@/services/contributions";
import {
  coverContributionDisplayName,
  isCoverContributionData,
  parentMarkingIdFromContribution,
} from "@/lib/contributionDisplay";
import {
  contributionImageMetasFromSubmittedData,
  contributionMetaImageUrl,
} from "@/lib/contributionImages";

/**
 * Unified Marking service. The v2 API returns one row per marking
 * (TOWNMARK | RATEMARK | AUXMARK), so the frontend does not fan out
 * per-row to type-specific side endpoints.
 *
 * Endpoints (under /api/v2):
 *   GET    /markings/                       list, paginated
 *   GET    /markings/{id}/                  detail
 *   GET    /markings/{id}/changelog/        version history
 *   POST   /markings/{id}/restore-version/  restore prior version
 *   POST   /markings/{id}/remove/           soft-remove into recycle bin
 *   POST   /markings/{id}/restore/          restore from recycle bin
 *   GET    /markings/recycle-bin/           removed markings (editor-scoped)
 *   GET    /markings/my-assigned/           editor's assigned-region rows
 *   GET    /markings-range/                 catalog earliest/latest year
 *   GET    /images/?subject_type=MARKING&subject_id=<id>  marking images
 */

export interface MarkingApiResponse {
  count: number | null;
  next: string | null;
  previous: string | null;
  results: unknown[];
  count_capped?: boolean;
}

export interface MarkingIdNameRef {
  id: number;
  name: string;
}

export type MarkingTypeValue = "TOWNMARK" | "RATEMARK" | "AUXMARK";

/** Image attached to a Marking or Cover via (subject_type, subject_id). */
export interface MarkingImage {
  imageId: number;
  subjectType: "COVER" | "MARKING";
  subjectId: number;
  imageUrl: string | null;
  imageView: string;
  originalFilename: string;
  storageFilename: string;
  imageDescription: string;
  isTracing: boolean;
  displayOrder: number;
}

export type ImageSubjectType = MarkingImage["subjectType"];

interface ImageApiResponse {
  count?: number;
  next?: string | null;
  previous?: string | null;
  results?: unknown[];
}

interface ImageApiRow {
  image_id?: unknown;
  subject_type?: unknown;
  subject_id?: unknown;
  image_url?: unknown;
  image_view?: unknown;
  original_filename?: unknown;
  storage_filename?: unknown;
  image_description?: unknown;
  is_tracing?: unknown;
  display_order?: unknown;
}

function mapImageRow(raw: unknown): MarkingImage | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as ImageApiRow;
  const imageId = toIdOrNull(o.image_id);
  const subjectTypeRaw = String(o.subject_type ?? "").toUpperCase();
  const subjectType: MarkingImage["subjectType"] =
    subjectTypeRaw === "COVER" ? "COVER" : "MARKING";
  const subjectId = toIdOrNull(o.subject_id);
  if (imageId == null || subjectId == null) return null;
  return {
    imageId,
    subjectType,
    subjectId,
    imageUrl: typeof o.image_url === "string" ? o.image_url : null,
    imageView: toStr(o.image_view),
    originalFilename: toStr(o.original_filename),
    storageFilename: toStr(o.storage_filename),
    imageDescription: toStr(o.image_description),
    isTracing: Boolean(o.is_tracing),
    displayOrder: toNumOrNull(o.display_order) ?? 0,
  };
}

/** Pixel size for multipart image metadata (server still recomputes from bytes). */
async function readImagePixelSize(file: File): Promise<{ width: number; height: number }> {
  if (typeof createImageBitmap === "function") {
    const bmp = await createImageBitmap(file);
    try {
      return { width: bmp.width, height: bmp.height };
    } finally {
      bmp.close?.();
    }
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({
        width: img.naturalWidth || img.width || 1,
        height: img.naturalHeight || img.height || 1,
      });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Could not decode image"));
    };
    img.src = url;
  });
}

export async function getImagesForSubject(params: {
  subjectType: ImageSubjectType;
  subjectId: number;
}): Promise<MarkingImage[]> {
  try {
    const res = await apiClient.get<ImageApiResponse>("/images/", {
      params: {
        subject_type: params.subjectType,
        subject_id: String(params.subjectId),
        ordering: "display_order",
      },
    });
    const results = Array.isArray(res.data?.results) ? res.data.results : [];
    return results.map(mapImageRow).filter((x): x is MarkingImage => x != null);
  } catch {
    return [];
  }
}

export async function createImageForSubject(params: {
  file: File;
  subjectType: ImageSubjectType;
  subjectId: number;
  imageView: string;
  imageDescription?: string | null;
  isTracing?: boolean;
  displayOrder?: number;
}): Promise<MarkingImage | null> {
  const form = new FormData();
  form.append("file", params.file, params.file.name);
  const safeName = (params.file.name || "image").slice(0, 255);
  form.append("original_filename", safeName);
  const mime =
    params.file.type && params.file.type.startsWith("image/")
      ? params.file.type
      : "image/jpeg";
  form.append("mime_type", mime);
  form.append("file_size_bytes", String(params.file.size));
  try {
    const { width, height } = await readImagePixelSize(params.file);
    form.append("image_width", String(width));
    form.append("image_height", String(height));
  } catch {
    form.append("image_width", "1");
    form.append("image_height", "1");
  }
  form.append("subject_type", params.subjectType);
  form.append("subject_id", String(params.subjectId));
  form.append("image_view", params.imageView);
  if (params.imageDescription != null) {
    form.append("image_description", params.imageDescription);
  }
  if (params.isTracing != null) {
    form.append("is_tracing", String(params.isTracing));
  }
  if (params.displayOrder != null) {
    form.append("display_order", String(params.displayOrder));
  }
  try {
    await ensureCsrfToken();
    const res = await apiClient.post("/images/", form);
    return mapImageRow(res.data);
  } catch {
    return null;
  }
}

export async function updateImage(
  imageId: number,
  patch: Partial<Pick<MarkingImage, "displayOrder" | "isTracing" | "imageDescription">>,
): Promise<boolean> {
  const payload: Record<string, unknown> = {};
  if (patch.displayOrder != null) payload.display_order = patch.displayOrder;
  if (patch.isTracing != null) payload.is_tracing = patch.isTracing;
  if (patch.imageDescription != null) payload.image_description = patch.imageDescription;
  try {
    await apiClient.patch(`/images/${imageId}/`, payload);
    return true;
  } catch {
    return false;
  }
}

/**
 * PATCH /api/v2/images/{image_id}/ -- reassign an image to another subject
 * (issue #48). image_view must be valid for the target subject type
 * (MARKING: FULL/DETAIL; COVER: FRONT/BACK/INTERIOR/DETAIL); the backend
 * validates target existence and view pairing and handles default-image
 * promotion on the source subject.
 */
export async function moveImageSubject(
  imageId: number,
  subjectType: "MARKING" | "COVER",
  subjectId: number,
  imageView: string,
): Promise<{ ok: true } | { ok: false; message: string }> {
  try {
    await ensureCsrfToken();
    await apiClient.patch(`/images/${imageId}/`, {
      subject_type: subjectType,
      subject_id: subjectId,
      image_view: imageView,
    });
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as {
      response?: {
        data?: { detail?: string; subject_id?: string[]; image_view?: string[] };
      };
    };
    const data = ax.response?.data;
    const message =
      data?.detail ?? data?.subject_id?.[0] ?? data?.image_view?.[0];
    return {
      ok: false,
      message: typeof message === "string" ? message : "Could not reassign image.",
    };
  }
}

/**
 * DELETE /api/v2/images/{image_id}/ -- remove one image row.
 *
 * Cwd for manual verification: frontend/.
 * Command: npm run build
 * Expected exit code: 0.
 *
 * The backend deletes only the Image row; shared files remain on disk. That is
 * the intended quick-delete behavior for an editor cleaning up a bad catalog
 * image from a detail page without visiting the full edit flow.
 */
export async function deleteImage(
  imageId: number,
): Promise<{ ok: true } | { ok: false; message: string }> {
  await ensureCsrfToken();
  try {
    await apiClient.delete(`/images/${imageId}/`);
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: string } } };
    const detail = ax.response?.data?.detail;
    return {
      ok: false,
      message: typeof detail === "string" ? detail : "Could not delete image.",
    };
  }
}

/**
 * Toggle the state-editor "reviewed/confirmed" flag on a marking (Issue #22).
 * Persisted via the same editor-gated PATCH path as other catalog edits; the
 * backend rejects the write unless the caller is the state editor responsible
 * for the marking's region (or an admin). Returns false on any error.
 */
export async function updateMarkingReviewed(
  markingId: number,
  reviewed: boolean,
): Promise<boolean> {
  try {
    await apiClient.patch(`/markings/${markingId}/`, { is_reviewed: reviewed });
    return true;
  } catch {
    return false;
  }
}

/**
 * Subset of ReferenceWork fields embedded in a Citation row, sufficient
 * to render a citation entry without a second /reference-works/ call.
 * Mirrors the read-only `reference_work_details` field on the v2
 * CitationSerializer. Numeric ids come from the Django default `id` PK.
 */
export interface MarkingCitationReferenceWork {
  id: number;
  code: string | null;
  title: string;
  authorship: string;
  publisher: string;
  publicationYear: number | null;
  edition: string;
  volume: string;
  isbn: string;
  url: string;
}

/** One Citation row attached to a marking via (subject_type=MARKING, subject_id=id). */
export interface MarkingCitation {
  id: number;
  /**
   * Page, section, or URL within the reference work. Backend column is
   * non-null with max_length=500; we still defensively trim/coerce.
   */
  citationDetail: string;
  /** Nested reference work data; null only if the API omits it. */
  referenceWork: MarkingCitationReferenceWork | null;
}

/** One territory/state a town belonged to (multi-territory support, #24). */
export interface MarkingRegion {
  id: number;
  name: string;
  abbrev: string;
  regionTier: string;
}

/** Canonical UI shape for a marking row (list or detail). */
export interface MarkingRecord {
  id: number;
  code: string;
  type: MarkingTypeValue;
  catalogTxt: string;
  inscriptionTxt: string;
  desc: string;
  isManuscript: boolean;
  isIrreg: boolean | null;
  width: string | null;
  height: string | null;
  sizeDisplay: string | null;
  dateFmt: string;
  impression: string;
  rateVal: string | null;
  postOfficeId: number | null;
  shapeId: number | null;
  letteringId: number | null;
  colorId: number | null;
  state: string;
  stateAbbrev: string;
  town: string;
  shapeName: string;
  letteringName: string;
  colorName: string;
  postOfficeName: string;
  regionName: string;
  // All territories/states this town belonged to over time (current-first).
  // regionName/state remain the single primary region for back-compat.
  regions: MarkingRegion[];
  earliestSeen: string | null;
  earliestSeenGranularity: string | null;
  latestSeen: string | null;
  latestSeenGranularity: string | null;
  // All MARKING-scoped DateSeen rows the catalog records for this marking (one
  // per observed date), ordered by date. Detail view only; used to render a
  // "Dates Seen" listing when there are multiple dates (issue #25).
  datesSeen: AssociatedDateSeen[];
  mainImage: MarkingImage | null;
  secondImage: MarkingImage | null;
  images: MarkingImage[];
  citations: MarkingCitation[];
  isRemoved: boolean;
  canRemove: boolean;
  // Whether a state editor has personally vetted this record (Issue #22).
  isReviewed: boolean;
  // Contributor's "comment for editor" and the editor's review feedback, sourced
  // from the marking's approved Contribution. The backend returns "" to anyone
  // who is not that contributor or an editor, so a non-empty value is safe to show.
  commentForEditor: string;
  editorFeedback: string;
}

export interface MarkingChangelogEvent {
  event_id: number;
  transaction_uuid: string;
  timestamp: string;
  action: string;
  action_label: string;
  actor: string | null;
  /**
   * Email address of the user who performed the event. Surfaced separately
   * from `actor` so the Record History panel can guarantee an email-based
   * audit trail even when the username happens to differ.
   */
  actor_email: string | null;
  source: string;
  contribution_id: number | null;
  version_no: number | null;
  summary: string;
  diff: Array<{ field: string; before: unknown; after: unknown }>;
}

export interface MarkingVersionRow {
  version_no: number;
  created_at: string;
  created_by: string | null;
  transaction_id: number | null;
  action?: string | null;
  action_label?: string | null;
  snapshot?: {
    catalog_txt?: string;
    code?: string;
    town?: string;
    state?: string;
    type?: string;
    inscription_txt?: string;
    desc?: string;
    is_manuscript?: boolean;
    impression?: string;
    is_irreg?: boolean | null;
    shape_id?: number | null;
    lettering_id?: number | null;
    color_id?: number | null;
    date_fmt?: string;
    rate_val?: number | string | null;
    width?: number | string | null;
    height?: number | string | null;
  };
}

export interface MarkingChangelogResponse {
  id: number;
  events: MarkingChangelogEvent[];
  versions: MarkingVersionRow[];
  approved_versions?: MarkingVersionRow[];
}

export interface MarkingYearRange {
  earliestYear: number | null;
  latestYear: number | null;
}

export interface GetMarkingsPageResult {
  results: MarkingRecord[];
  count: number | null;
  next: string | null;
  previous: string | null;
  count_capped?: boolean;
}

/** Normalize image URL to absolute using the API origin when needed. */
export function normalizeImageUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  if (/^https?:\/\//i.test(path)) return path;
  const base = apiClient.defaults.baseURL;
  if (!base || !/^https?:\/\//i.test(base)) return path;
  try {
    const url = new URL(base);
    const relative = path.startsWith("/") ? path : `/${path}`;
    return `${url.origin}${relative}`;
  } catch {
    return path;
  }
}

/** Pull a usable URL out of an API image payload (or string). */
export function getMarkingListImageUrl(
  img: MarkingImage | string | null | undefined
): string | null {
  if (img == null) return null;
  if (typeof img === "string") return img || null;
  return img.imageUrl ?? null;
}

function toStr(v: unknown): string {
  if (v == null) return "";
  return String(v).trim();
}

function toNumOrNull(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
}

function toIdOrNull(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    return toIdOrNull(o.id);
  }
  const n = parseInt(String(v), 10);
  return Number.isFinite(n) ? n : null;
}

function decimalToString(v: unknown): string | null {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  return s.length > 0 ? s : null;
}

function mapImage(raw: unknown): MarkingImage | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const subjectType = toStr(o.subject_type) || "MARKING";
  const subjectId = toIdOrNull(o.subject_id) ?? 0;
  const imageId = toIdOrNull(o.image_id) ?? 0;
  return {
    imageId,
    subjectType: (subjectType === "COVER" ? "COVER" : "MARKING") as "COVER" | "MARKING",
    subjectId,
    imageUrl: typeof o.image_url === "string" ? o.image_url : null,
    imageView: toStr(o.image_view),
    originalFilename: toStr(o.original_filename),
    storageFilename: toStr(o.storage_filename),
    imageDescription: toStr(o.image_description),
    isTracing: Boolean(o.is_tracing),
    displayOrder: toNumOrNull(o.display_order) ?? 0,
  };
}

function mapImageList(raw: unknown): MarkingImage[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(mapImage).filter((x): x is MarkingImage => x !== null);
}

function mapCitationReferenceWork(raw: unknown): MarkingCitationReferenceWork | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  if (id == null) return null;
  return {
    id,
    code: typeof o.code === "string" && o.code ? o.code : null,
    title: toStr(o.title),
    authorship: toStr(o.authorship),
    publisher: toStr(o.publisher),
    publicationYear: toNumOrNull(o.publication_year),
    edition: toStr(o.edition),
    volume: toStr(o.volume),
    isbn: toStr(o.isbn),
    url: toStr(o.url),
  };
}

function mapCitation(raw: unknown): MarkingCitation | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  if (id == null) return null;
  return {
    id,
    citationDetail: toStr(o.citation_detail),
    referenceWork: mapCitationReferenceWork(o.reference_work_details),
  };
}

function mapCitationList(raw: unknown): MarkingCitation[] {
  if (!Array.isArray(raw)) return [];
  return raw.map(mapCitation).filter((x): x is MarkingCitation => x !== null);
}

/**
 * Display string for the "State/Territory" row: every territory a town belonged
 * to, current-first, comma-separated (multi-territory support, #24). Falls back
 * to the single primary region (`state`) when the regions list is absent (e.g.
 * search-list payloads that don't include it). Exported for unit testing.
 */
export function regionsDisplay(
  record: Pick<MarkingRecord, "regions" | "state">,
): string {
  const names = record.regions.map((r) => r.name.trim()).filter((n) => n !== "");
  return names.length > 0 ? names.join(", ") : record.state;
}

function mapRegionList(raw: unknown): MarkingRegion[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((r): MarkingRegion | null => {
      const o = (r && typeof r === "object" ? r : {}) as Record<string, unknown>;
      const id = toIdOrNull(o.id);
      const name = toStr(o.name);
      if (id == null || !name) return null;
      return { id, name, abbrev: toStr(o.abbrev), regionTier: toStr(o.region_tier) };
    })
    .filter((x): x is MarkingRegion => x !== null);
}

/**
 * Convert a /markings/ list or detail payload into MarkingRecord.
 * Single-pass mapper: snake_case in, camelCase out, no fallbacks. The API
 * is now the canonical shape; if a field is missing, the empty default is
 * what the UI renders.
 */
export function mapApiMarkingToRecord(raw: unknown): MarkingRecord {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const idVal = toIdOrNull(o.id) ?? 0;
  const images = mapImageList(o.images);
  const mainImage = mapImage(o.main_image) ?? images[0] ?? null;
  const secondImage = mapImage(o.second_image) ?? images[1] ?? null;
  const typeRaw = toStr(o.type).toUpperCase();
  const type: MarkingTypeValue =
    typeRaw === "RATEMARK" ? "RATEMARK" : typeRaw === "AUXMARK" ? "AUXMARK" : "TOWNMARK";
  return {
    id: idVal,
    code: toStr(o.code),
    type,
    catalogTxt: toStr(o.catalog_txt),
    inscriptionTxt: toStr(o.inscription_txt),
    desc: toStr(o.desc),
    isManuscript: Boolean(o.is_manuscript),
    isIrreg: o.is_irreg == null ? null : Boolean(o.is_irreg),
    width: decimalToString(o.width),
    height: decimalToString(o.height),
    sizeDisplay: typeof o.size_display === "string" ? o.size_display : null,
    dateFmt: toStr(o.date_fmt),
    impression: toStr(o.impression),
    rateVal: decimalToString(o.rate_val),
    postOfficeId: toIdOrNull(o.post_office),
    shapeId: toIdOrNull(o.shape),
    letteringId: toIdOrNull(o.lettering),
    colorId: toIdOrNull(o.color),
    state: toStr(o.state),
    stateAbbrev: toStr(o.state_abbrev),
    town: toStr(o.town),
    shapeName: toStr(o.shape_name),
    letteringName: toStr(o.lettering_name),
    colorName: toStr(o.color_name),
    postOfficeName: toStr(o.post_office_name),
    regionName: toStr(o.region_name),
    regions: mapRegionList(o.regions),
    earliestSeen: typeof o.earliest_seen === "string" && o.earliest_seen ? o.earliest_seen : null,
    earliestSeenGranularity:
      typeof o.earliest_seen_granularity === "string" && o.earliest_seen_granularity
        ? o.earliest_seen_granularity
        : null,
    latestSeen: typeof o.latest_seen === "string" && o.latest_seen ? o.latest_seen : null,
    latestSeenGranularity:
      typeof o.latest_seen_granularity === "string" && o.latest_seen_granularity
        ? o.latest_seen_granularity
        : null,
    datesSeen: (Array.isArray(o.dates_seen) ? o.dates_seen : [])
      .map(mapAssociatedDateSeen)
      .filter((x): x is AssociatedDateSeen => x !== null),
    mainImage,
    secondImage,
    images,
    citations: mapCitationList(o.citations),
    isRemoved: Boolean((raw as { is_removed?: boolean }).is_removed),
    canRemove: Boolean((raw as { can_remove?: boolean }).can_remove),
    isReviewed: Boolean((raw as { is_reviewed?: boolean }).is_reviewed),
    commentForEditor: toStr(o.comment_for_editor),
    editorFeedback: toStr(o.editor_feedback),
  };
}

/** GET /markings/ paginated list with the standard catalog filters. */
export async function getMarkingsPage(
  page: number = 1,
  pageSize: number = 10,
  options?: {
    search?: string;
    shapeId?: string | number;
    type?: MarkingTypeValue | "all";
    manuscripts?: "both" | "only" | "none";
    color?: string;
    state?: string;
    town?: string;
    beginYear?: string;
    endYear?: string;
    height?: string;
    width?: string;
    hasImages?: boolean;
    institutional?: boolean;
    referenceWorkCode?: string;
    reviewed?: "reviewed" | "unreviewed";
    deferCount?: boolean;
    ordering?: string;
  }
): Promise<GetMarkingsPageResult> {
  const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
  const opt = options ?? {};
  if (opt.search?.trim()) params.search = opt.search.trim();
  if (opt.shapeId != null && opt.shapeId !== "" && String(opt.shapeId) !== "all") {
    params.shape = String(opt.shapeId);
  }
  if (opt.type && opt.type !== "all") params.type = opt.type;
  if (opt.manuscripts === "only") params.is_manuscript = "true";
  else if (opt.manuscripts === "none") params.is_manuscript = "false";
  if (opt.color != null && opt.color !== "" && opt.color !== "all") params.color = opt.color;
  if (opt.state != null && opt.state !== "" && opt.state !== "all") params.state = opt.state.trim();
  if (opt.town?.trim()) params.town = opt.town.trim();
  if (opt.beginYear?.trim()) params.earliest_use_year_min = opt.beginYear.trim();
  if (opt.endYear?.trim()) params.latest_use_year_max = opt.endYear.trim();
  if (opt.height?.trim()) params.height = opt.height.trim();
  if (opt.width?.trim()) params.width = opt.width.trim();
  if (opt.hasImages === true) params.has_images = "true";
  if (opt.institutional === true) params.institutional = "true";
  if (opt.referenceWorkCode?.trim()) params.reference_work_code = opt.referenceWorkCode.trim();
  if (opt.reviewed === "reviewed") params.reviewed = "true";
  else if (opt.reviewed === "unreviewed") params.reviewed = "false";
  if (opt.deferCount === true) params.include_count = "false";
  if (opt.ordering?.trim()) params.ordering = opt.ordering.trim();

  const res = await apiClient.get<MarkingApiResponse>("/markings/", { params });
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Markings API: invalid response (missing results array)");
  }
  return {
    results: data.results.map(mapApiMarkingToRecord),
    count: data.count,
    next: data.next,
    previous: data.previous,
    count_capped: data.count_capped,
  };
}

/** GET /markings/my-assigned/ for state editors (auth required). */
export async function getAssignedCatalogPage(
  page: number = 1,
  pageSize: number = 10,
  options?: {
    filters?: { state?: string; town?: string; shape?: string; color?: string; search?: string };
  }
): Promise<GetMarkingsPageResult> {
  const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
  const f = options?.filters;
  if (f?.state && f.state !== "all") params.state = f.state;
  if (f?.town?.trim()) params.town = f.town.trim();
  if (f?.shape && f.shape !== "all") params.shape = f.shape;
  if (f?.color && f.color !== "all") params.color = f.color;
  if (f?.search?.trim()) params.search = f.search.trim();

  const res = await apiClient.get<MarkingApiResponse>("/markings/my-assigned/", {
    params,
    withCredentials: true,
    headers: { Accept: "application/json" },
  });
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Assigned catalog API: invalid response (missing results array)");
  }
  return {
    results: data.results.map(mapApiMarkingToRecord),
    count: data.count ?? null,
    next: data.next ?? null,
    previous: data.previous ?? null,
    count_capped: data.count_capped,
  };
}

/** GET /markings/{id}/ - returns the canonical detail shape. */
export async function getMarkingById(markingId: number): Promise<MarkingRecord | null> {
  try {
    const res = await apiClient.get(`/markings/${markingId}/`);
    return mapApiMarkingToRecord(res.data);
  } catch {
    return null;
  }
}

/** Region string used to route contributions to a Collection (abbrev preferred). */
export function routingStateFromMarkingRecord(record: MarkingRecord | null): string {
  if (!record) return "";
  return (
    (record.stateAbbrev ?? "").trim() ||
    (record.state ?? "").trim() ||
    (record.regionName ?? "").trim()
  );
}

/**
 * Resolve collection routing state for a marking (for cover draft POST).
 * Falls back to GET /post-offices/{id}/ when marking detail omits region fields.
 */
export async function resolveMarkingRoutingState(markingId: number): Promise<string> {
  const record = await getMarkingById(markingId);
  let route = routingStateFromMarkingRecord(record);
  if (route) return route;

  const raw = await getMarkingByIdRaw(markingId);
  if (!raw) return "";

  route =
    toStr(raw.state_abbrev) ||
    toStr((raw as { stateAbbrev?: string }).stateAbbrev) ||
    toStr(raw.state) ||
    toStr(raw.region_name) ||
    toStr((raw as { regionName?: string }).regionName);
  if (route.trim()) return route.trim();

  const postOfficeId = record?.postOfficeId ?? toIdOrNull(raw.post_office);
  if (postOfficeId == null) return "";

  try {
    const res = await apiClient.get(`/post-offices/${postOfficeId}/`);
    const po = res.data as Record<string, unknown>;
    route =
      toStr(po.region_abbrev) ||
      toStr((po as { regionAbbrev?: string }).regionAbbrev) ||
      toStr(po.region_name) ||
      toStr((po as { regionName?: string }).regionName);
    return route.trim();
  } catch {
    return "";
  }
}

/** Raw detail payload (snake_case) for callers that need server fields directly. */
export async function getMarkingByIdRaw(markingId: number): Promise<Record<string, unknown> | null> {
  try {
    const res = await apiClient.get(`/markings/${markingId}/`);
    return (res.data ?? null) as Record<string, unknown> | null;
  } catch {
    return null;
  }
}

/**
 * PATCH /api/v2/images/{image_id}/ -- update display_order on a single image.
 * Returns the updated MarkingImage on success, or null on failure.
 *
 * Used by the editor reorder controls on the Record Detail page. The
 * server's ImageViewSet is a plain ModelViewSet so PATCHing display_order
 * is the canonical lever for "which image is the Catalog Search thumbnail"
 * (display_order=0) and "what order do thumbnails appear in the Associated
 * Thumbnails strip".
 */
export async function updateImageDisplayOrder(
  imageId: number,
  displayOrder: number,
): Promise<MarkingImage | null> {
  try {
    const res = await apiClient.patch(
      `/images/${imageId}/`,
      { display_order: displayOrder },
      {
        withCredentials: true,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
      },
    );
    return mapImage(res.data);
  } catch {
    return null;
  }
}

/**
 * Reassign display_order across an ordered list of image IDs in one round
 * of parallel PATCHes (index 0 -> display_order=0, index 1 -> 1, etc.).
 * Returns true when every PATCH succeeded so callers know whether to
 * refetch the marking or surface an error to the user.
 */
export async function reorderImages(
  imageIdsInOrder: number[],
): Promise<boolean> {
  if (imageIdsInOrder.length === 0) return true;
  const results = await Promise.all(
    imageIdsInOrder.map((id, idx) => updateImageDisplayOrder(id, idx)),
  );
  return results.every((row) => row !== null);
}

export async function getMarkingChangelog(markingId: number): Promise<MarkingChangelogResponse | null> {
  try {
    const res = await apiClient.get<MarkingChangelogResponse>(
      `/markings/${markingId}/changelog/`,
      {
        withCredentials: true,
        headers: { Accept: "application/json" },
      }
    );
    return res.data;
  } catch {
    return null;
  }
}

export async function restoreMarkingVersion(
  markingId: number,
  versionNo: number
): Promise<{ detail?: string; restored_from_version_no?: number; new_version_no?: number } | null> {
  try {
    const res = await apiClient.post(
      `/markings/${markingId}/restore-version/`,
      { version_no: versionNo },
      {
        withCredentials: true,
        headers: { "Content-Type": "application/json", Accept: "application/json" },
      }
    );
    return res.data ?? null;
  } catch {
    return null;
  }
}

/**
 * A single DateSeen row attached to an associated cover. The backing API row
 * is polymorphic (subject_type COVER|MARKING + subject_id); inside an
 * AssociatedCover the only rows the caller sees are the COVER-scoped ones,
 * so the discriminator is implicit and we expose just the observation fields.
 */
export interface AssociatedDateSeen {
  id: number;
  date: string;
  granularity: "DAY" | "MONTH" | "YEAR";
}

/** Cover attributes shown inside an Associated Cover row. */
export interface AssociatedCoverDetails {
  id: number;
  code: string | null;
  /**
   * FK id of the Color row (or null when no color is set). Carried alongside
   * colorName so the cover edit dialog can prefill the colour <Select>
   * (which is keyed by id) without a second round-trip.
   */
  colorId: number | null;
  colorName: string;
  type: string | null;
  width: string | null;
  height: string | null;
  hasAdhesive: boolean | null;
  isInstitutional: boolean | null;
  displaySubmitterName: boolean;
  /** Free-text description / notes; prefilled into the cover edit form. */
  description: string;
  datesSeen: AssociatedDateSeen[];
}

/** One CoverMarking row (a marking-on-cover association). */
export type CoverMarkingReviewStatus = "pending" | "approved" | "rejected" | "needs_revision";

export interface AssociatedCover {
  id: number;
  /** Editor moderation for this cover<->marking link (defaults to approved for legacy rows). */
  reviewStatus: CoverMarkingReviewStatus;
  reviewNotes: string | null;
  reviewedAt: string | null;
  reviewerUsername: string;
  isBackstamp: boolean;
  placement: string | null;
  /** Optional contributor note stored on the CoverMarking row for reviewers. */
  contributorComment?: string | null;
  coverDetails: AssociatedCoverDetails | null;
  /**
   * When set, this row is a saved Contribution draft (not yet a CoverMarking).
   * Thumbnail opens the cover editor with `?edit={id}`.
   */
  contributionDraftId?: number;
  /** Contribution.status for draft rows (`draft`, `needs_revision`, ...). */
  contributionStatus?: string;
  /** Human-readable title for draft rows (from submitted_data). */
  displayLabel?: string;
  /**
   * Optional client enrichment: URL of the cover's default image
   * (`display_order` 0, else first image). Set by
   * `enrichAssociatedCoversWithDefaultImages` on the record detail page.
   */
  defaultImageUrl?: string | null;
}

interface CoverMarkingApiResponse {
  count?: number;
  next?: string | null;
  previous?: string | null;
  results: unknown[];
}

function mapAssociatedDateSeen(raw: unknown): AssociatedDateSeen | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  const date = typeof o.date === "string" ? o.date : "";
  if (id == null || !date) return null;
  const gRaw = String(o.granularity ?? "").toUpperCase();
  const granularity: AssociatedDateSeen["granularity"] =
    gRaw === "MONTH" ? "MONTH" : gRaw === "YEAR" ? "YEAR" : "DAY";
  return { id, date, granularity };
}

function mapAssociatedCoverDetails(raw: unknown): AssociatedCoverDetails | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  if (id == null) return null;
  // Backend serializer exposes the nested array under `dates_seen` (it used
  // to be `cover_dates` before the polymorphic refactor). Caller code only
  // sees COVER-scoped rows here because CoverSerializer.get_dates_seen
  // filters by subject_type=COVER for this cover's pk.
  const datesRaw = Array.isArray(o.dates_seen) ? o.dates_seen : [];
  const datesSeen = datesRaw
    .map(mapAssociatedDateSeen)
    .filter((x): x is AssociatedDateSeen => x !== null);
  return {
    id,
    code: typeof o.code === "string" && o.code ? o.code : null,
    colorId: toIdOrNull(o.color),
    colorName: toStr(o.color_name),
    type: typeof o.type === "string" && o.type ? o.type : null,
    width: decimalToString(o.width),
    height: decimalToString(o.height),
    hasAdhesive: o.has_adhesive == null ? null : Boolean(o.has_adhesive),
    isInstitutional: o.is_institutional == null ? null : Boolean(o.is_institutional),
    displaySubmitterName: Boolean(o.display_submitter_name),
    description: typeof o.description === "string" ? o.description : "",
    datesSeen,
  };
}

function normalizeCoverMarkingReviewStatus(raw: unknown): CoverMarkingReviewStatus {
  const s = String(raw ?? "").toLowerCase();
  if (s === "pending" || s === "approved" || s === "rejected" || s === "needs_revision") return s;
  return "approved";
}

function mapAssociatedCover(raw: unknown): AssociatedCover | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  if (id == null) return null;
  return {
    id,
    reviewStatus: normalizeCoverMarkingReviewStatus(o.review_status),
    reviewNotes: typeof o.review_notes === "string" ? o.review_notes : null,
    reviewedAt: typeof o.reviewed_at === "string" ? o.reviewed_at : null,
    reviewerUsername:
      typeof o.reviewer_username === "string"
        ? o.reviewer_username
        : typeof (o as { reviewerUsername?: string }).reviewerUsername === "string"
          ? (o as { reviewerUsername?: string }).reviewerUsername!
          : "",
    isBackstamp: Boolean(o.is_backstamp),
    placement:
      typeof o.placement === "string" && o.placement ? o.placement : null,
    contributorComment:
      typeof o.contributor_comment === "string"
        ? o.contributor_comment
        : typeof (o as { contributorComment?: string }).contributorComment === "string"
          ? (o as { contributorComment?: string }).contributorComment!
          : null,
    coverDetails: mapAssociatedCoverDetails(o.cover_details),
  };
}

/**
 * GET /cover-markings/?marking={id} - covers associated with a marking.
 * Returns [] on error or empty.
 *
 * Example response item shape (snake_case in, camelCase out):
 *   {
 *     "id": 7,
 *     "is_backstamp": false,
 *     "placement": null,
 *     "cover_details": {
 *       "id": 12,
 *       "code": "C-1234",
 *       "color_name": "Black",
 *       "type": "FC",
 *       "width": "20.00",
 *       "height": "10.50",
 *       "has_adhesive": false,
 *       "is_institutional": null,
 *       "dates_seen": [
 *         { "id": 99, "date": "1851-04-12", "granularity": "DAY" }
 *       ]
 *     }
 *   }
 */
export type MarkingCoversResult = {
  covers: AssociatedCover[];
  error: string | null;
};

export async function getMarkingCovers(markingId: number): Promise<MarkingCoversResult> {
  try {
    const res = await apiClient.get<CoverMarkingApiResponse>(
      "/cover-markings/",
      { params: { marking: String(markingId) } },
    );
    const results = Array.isArray(res.data?.results) ? res.data.results : [];
    const covers = results
      .map(mapAssociatedCover)
      .filter((x): x is AssociatedCover => x !== null);
    return { covers, error: null };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: unknown } } };
    const detail = ax.response?.data?.detail;
    let message = "Could not load associated covers.";
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail)) {
      message = detail.map(String).join(" ");
    } else if (detail && typeof detail === "object") {
      message = JSON.stringify(detail);
    }
    return { covers: [], error: message };
  }
}

/** Cover<->marking link row from GET /cover-markings/?cover={id}. */
export interface CoverMarkingLink {
  id: number;
  coverId: number;
  markingId: number;
  isBackstamp: boolean;
  reviewStatus: CoverMarkingReviewStatus;
  reviewNotes: string | null;
}

function mapCoverMarkingLink(raw: unknown): CoverMarkingLink | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  const coverId = toIdOrNull(o.cover);
  const markingId = toIdOrNull(o.marking);
  if (id == null || coverId == null || markingId == null) return null;
  return {
    id,
    coverId,
    markingId,
    isBackstamp: Boolean(o.is_backstamp),
    reviewStatus: normalizeCoverMarkingReviewStatus(o.review_status),
    reviewNotes: typeof o.review_notes === "string" ? o.review_notes : null,
  };
}

export type CoverMarkingsByCoverResult = {
  links: CoverMarkingLink[];
  error: string | null;
};

/** GET /cover-markings/?cover={id} -- markings linked to a cover. */
export async function getCoverMarkingsByCover(
  coverId: number,
): Promise<CoverMarkingsByCoverResult> {
  try {
    const res = await apiClient.get<CoverMarkingApiResponse>("/cover-markings/", {
      params: { cover: String(coverId) },
    });
    const results = Array.isArray(res.data?.results) ? res.data.results : [];
    const links = results
      .map(mapCoverMarkingLink)
      .filter((x): x is CoverMarkingLink => x !== null);
    return { links, error: null };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: unknown } } };
    const detail = ax.response?.data?.detail;
    let message = "Could not load associated markings.";
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail)) {
      message = detail.map(String).join(" ");
    } else if (detail && typeof detail === "object") {
      message = JSON.stringify(detail);
    }
    return { links: [], error: message };
  }
}

export type AssociatedMarkingOnCover = {
  link: CoverMarkingLink;
  marking: MarkingRecord;
  defaultImageUrl: string | null;
};

/** Load marking rows (and default thumbnails) for each cover-marking link. */
export async function loadAssociatedMarkingsForCover(
  links: CoverMarkingLink[],
): Promise<AssociatedMarkingOnCover[]> {
  if (links.length === 0) return [];
  const markingIds = [...new Set(links.map((l) => l.markingId))];
  const markings = await Promise.all(markingIds.map((id) => getMarkingById(id)));
  const byId = new Map<number, MarkingRecord>();
  for (const m of markings) {
    if (m) byId.set(m.id, m);
  }
  const rows: AssociatedMarkingOnCover[] = [];
  for (const link of links) {
    const marking = byId.get(link.markingId);
    if (!marking) continue;
    let defaultImageUrl: string | null = null;
    const sorted = [...marking.images].sort((a, b) => a.displayOrder - b.displayOrder);
    const def = sorted.find((img) => img.displayOrder === 0) ?? sorted[0];
    if (def?.imageUrl) defaultImageUrl = normalizeImageUrl(def.imageUrl);
    rows.push({ link, marking, defaultImageUrl });
  }
  return rows;
}

function resolveCoverSubmissionThumbnail(
  submittedData: Record<string, unknown>,
  contrib?: Record<string, unknown>,
): string | null {
  const c = contrib ?? {};
  const mainImage = c.mainImage as { imageUrl?: unknown } | string | null | undefined;
  const mainImageFromList =
    (mainImage && typeof mainImage === "object" && typeof mainImage.imageUrl === "string"
      ? mainImage.imageUrl
      : null) ?? (typeof mainImage === "string" ? mainImage : null);
  const direct = normalizeImageUrl(
    mainImageFromList ??
      (typeof c.imageUrl === "string" ? c.imageUrl : null) ??
      (typeof c.image_url === "string" ? c.image_url : null) ??
      null,
  );
  if (direct) return direct;

  for (const meta of contributionImageMetasFromSubmittedData(submittedData)) {
    const url = contributionMetaImageUrl(meta);
    if (url) return normalizeImageUrl(url) ?? url;
  }
  return null;
}

function datesSeenFromCoverSubmission(sd: Record<string, unknown>): AssociatedDateSeen[] {
  const raw = sd.cover_date ?? sd.coverDate;
  if (raw == null || String(raw).trim() === "") return [];
  const date = String(raw).trim();
  const gRaw = String(
    sd.cover_granularity ?? sd.coverGranularity ?? sd.date_granularity ?? sd.dateGranularity ?? "DAY",
  ).toUpperCase();
  const granularity: AssociatedDateSeen["granularity"] =
    gRaw === "MONTH" ? "MONTH" : gRaw === "YEAR" ? "YEAR" : "DAY";
  return [{ id: 0, date, granularity }];
}

function mapCoverContributionToAssociatedCover(
  contrib: Record<string, unknown>,
  markingId: number,
): AssociatedCover | null {
  const id = toIdOrNull(contrib.id);
  if (id == null) return null;
  const sdRaw = contrib.submittedData ?? contrib.submitted_data;
  if (!sdRaw || typeof sdRaw !== "object") return null;
  const sd = sdRaw as Record<string, unknown>;
  if (!isCoverContributionData(sd)) return null;
  if (parentMarkingIdFromContribution(sd) !== markingId) return null;

  const status = String(contrib.status ?? "draft").trim().toLowerCase();
  if (status !== "draft") return null;
  const sdMaterialized =
    sd.materialized_cover_marking_id ??
    sd.materializedCoverMarkingId ??
    sd.cover_marking_id ??
    sd.coverMarkingId;
  if (sdMaterialized != null && String(sdMaterialized).trim() !== "") return null;

  // Only "draft" rows reach this point (guarded above), so a draft cover is
  // always surfaced as "pending" review.
  const reviewStatus: CoverMarkingReviewStatus = "pending";

  const widthRaw = sd.width_mm ?? sd.widthMm ?? sd.width;
  const heightRaw = sd.height_mm ?? sd.heightMm ?? sd.height;
  const typeRaw = sd.type;
  const type =
    typeof typeRaw === "string" && typeRaw.trim() ? typeRaw.trim() : null;

  return {
    id: -id,
    contributionDraftId: id,
    contributionStatus: status,
    displayLabel: coverContributionDisplayName(sd, id),
    reviewStatus,
    reviewNotes:
      typeof contrib.review_notes === "string"
        ? contrib.review_notes
        : typeof contrib.reviewNotes === "string"
          ? contrib.reviewNotes
          : null,
    reviewedAt: null,
    reviewerUsername: "",
    isBackstamp: Boolean(sd.is_backstamp ?? sd.isBackstamp),
    placement:
      typeof sd.placement === "string" && sd.placement.trim()
        ? sd.placement.trim()
        : null,
    coverDetails: {
      id: -id,
      code: null,
      colorId: null,
      colorName: typeof sd.color === "string" ? sd.color : "",
      type,
      width: widthRaw != null && String(widthRaw).trim() ? String(widthRaw) : null,
      height: heightRaw != null && String(heightRaw).trim() ? String(heightRaw) : null,
      hasAdhesive:
        typeof sd.has_adhesive === "boolean"
          ? sd.has_adhesive
          : typeof sd.hasAdhesive === "boolean"
            ? sd.hasAdhesive
            : null,
      isInstitutional:
        typeof sd.is_institutional === "boolean"
          ? sd.is_institutional
          : typeof sd.isInstitutional === "boolean"
            ? sd.isInstitutional
            : null,
      displaySubmitterName: Boolean(sd.display_submitter_name ?? sd.displaySubmitterName),
      description: typeof sd.description === "string" ? sd.description : "",
      datesSeen: datesSeenFromCoverSubmission(sd),
    },
    defaultImageUrl: resolveCoverSubmissionThumbnail(sd, contrib),
  };
}

/** Cover contribution drafts for a marking (not yet materialized as CoverMarking rows). */
export async function getCoverContributionDraftsForMarking(
  markingId: number,
): Promise<AssociatedCover[]> {
  try {
    const { rawItems } = await listContributions();
    return rawItems
      .map((row) =>
        mapCoverContributionToAssociatedCover(row as Record<string, unknown>, markingId),
      )
      .filter((x): x is AssociatedCover => x !== null);
  } catch {
    return [];
  }
}

/** Approved cover-marking rows for a marking (record-detail Associated Covers). */
export async function loadAssociatedCoversForMarking(
  markingId: number,
): Promise<MarkingCoversResult> {
  const { covers, error } = await getMarkingCovers(markingId);
  // Only editor-approved cover-marking links appear on the record's Associated
  // Covers panel (and its count). Covers still pending review (or rejected /
  // returned-for-revision) are hidden, mirroring how an unapproved marking
  // contribution does not appear on the record. The contributor's own cover
  // drafts -- pending, returned, or unsubmitted -- are reached from Dashboard /
  // My Submissions, NOT from this panel.
  const approved = covers.filter((c) => c.reviewStatus === "approved");
  const enriched = await enrichAssociatedCoversWithDefaultImages(approved);
  return { covers: enriched, error };
}

/** Parallel fetch of default thumbnail URLs for associated covers (cover image API). */
export async function enrichAssociatedCoversWithDefaultImages(
  covers: AssociatedCover[],
): Promise<AssociatedCover[]> {
  if (covers.length === 0) return [];
  return Promise.all(
    covers.map(async (c) => {
      if (c.contributionDraftId != null) {
        if (c.defaultImageUrl) {
          return { ...c, defaultImageUrl: normalizeImageUrl(c.defaultImageUrl) ?? c.defaultImageUrl };
        }
        return { ...c, defaultImageUrl: null };
      }
      try {
        const cid = c.coverDetails?.id;
        if (cid == null || cid < 0) return { ...c, defaultImageUrl: null };
        const imgs = await getImagesForSubject({
          subjectType: "COVER",
          subjectId: cid,
        });
        const sorted = [...imgs].sort((a, b) => a.displayOrder - b.displayOrder);
        const def = sorted.find((img) => img.displayOrder === 0) ?? sorted[0];
        return {
          ...c,
          defaultImageUrl: def?.imageUrl ? normalizeImageUrl(def.imageUrl) : null,
        };
      } catch {
        return { ...c, defaultImageUrl: null };
      }
    }),
  );
}

/** Editor moderation: POST /cover-markings/{id}/approve|reject|request-revision/ */
export type CoverMarkingReviewActionApi = "approve" | "reject" | "request-revision";

export async function postCoverMarkingReview(
  coverMarkingId: number,
  action: CoverMarkingReviewActionApi,
  reviewNotes?: string,
): Promise<{ ok: true } | { ok: false; message: string }> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/cover-markings/${coverMarkingId}/${action}/`, {
      review_notes: reviewNotes ?? "",
    });
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: string } } };
    const d = ax.response?.data?.detail;
    return { ok: false, message: typeof d === "string" ? d : "Request failed." };
  }
}

/** Contributor: POST /cover-markings/{id}/resubmit/ after addressing revision notes. */
export async function postCoverMarkingResubmit(coverMarkingId: number): Promise<void> {
  await ensureCsrfToken();
  await apiClient.post(`/cover-markings/${coverMarkingId}/resubmit/`);
}

/**
 * Editor: POST /markings/{id}/remove/ - soft-remove the marking into the
 * recycle bin (reversible). Optional reason is recorded in the changelog.
 * Backend gates on the responsible editor/superuser.
 */
export async function removeMarking(
  markingId: number,
  reason?: string,
): Promise<{ ok: true } | { ok: false; message: string }> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/markings/${markingId}/remove/`, { reason: reason ?? "" });
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: string } } };
    const d = ax.response?.data?.detail;
    return { ok: false, message: typeof d === "string" ? d : "Could not remove marking." };
  }
}

/** Editor: POST /markings/{id}/restore/ - restore a removed marking from the recycle bin. */
export async function restoreMarking(
  markingId: number,
): Promise<{ ok: true } | { ok: false; message: string }> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/markings/${markingId}/restore/`, {});
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: string } } };
    const d = ax.response?.data?.detail;
    return { ok: false, message: typeof d === "string" ? d : "Could not restore marking." };
  }
}

/**
 * Editor: GET /markings/recycle-bin/ - removed markings, region-scoped server
 * side. Same return shape as getMarkingsPage; uses MarkingListSerializer +
 * pagination, so isRemoved/canRemove default to false on these rows.
 */
export async function getRecycleBinMarkings(
  page: number = 1,
  pageSize: number = 10,
): Promise<GetMarkingsPageResult> {
  const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
  const res = await apiClient.get<MarkingApiResponse>("/markings/recycle-bin/", { params });
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Recycle bin API: invalid response (missing results array)");
  }
  return {
    results: data.results.map(mapApiMarkingToRecord),
    count: data.count,
    next: data.next,
    previous: data.previous,
    count_capped: data.count_capped,
  };
}

/** GET /markings/?page_size=1 - returns total marking count. */
export async function getMarkingCount(): Promise<number> {
  const res = await apiClient.get<MarkingApiResponse>("/markings/", {
    params: { page_size: "1" },
  });
  return typeof res.data.count === "number" ? res.data.count : 0;
}

/** GET /markings-range/ - earliest/latest dates_seen year across catalog. */
export async function getMarkingYearRange(): Promise<MarkingYearRange> {
  const res = await apiClient.get<Record<string, number | null | undefined>>(
    "/markings-range/"
  );
  const data = res.data ?? {};
  const earliest = data.earliest_year ?? data.earliestYear;
  const latest = data.latest_year ?? data.latestYear;
  return {
    earliestYear: typeof earliest === "number" ? earliest : null,
    latestYear: typeof latest === "number" ? latest : null,
  };
}
