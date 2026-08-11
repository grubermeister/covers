/**
 * Covers (v2 Cover entity): GET /covers/, plus full CRUD over the cover
 * graph (Cover, CoverMarking link, CoverDate child rows) used by the cover
 * add/edit dialog on the Record Detail page.
 */
import apiClient, { ensureCsrfToken } from "@/lib/api";
import type { DateSeenGranularity as PartialDateSeenGranularity } from "@/lib/partialDate";

/** One item from GET /covers/ (DRF snake_case) */
export interface CoverApiResultItem {
  cover_id: number;
  cover_key: string;
  owner_username: string;
  marking_count: number;
  created_date: string;
}

/** Paginated response from GET /covers/ */
export interface CoverApiResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: CoverApiResultItem[];
}

/** Normalized cover for list/detail */
export interface CoverRecord {
  id: number;
  coverKey: string;
  ownerUsername: string;
  markingCount: number;
  createdDate: string;
}

function mapApiResultToRecord(item: CoverApiResultItem): CoverRecord {
  return {
    id: item.cover_id,
    coverKey: item.cover_key,
    ownerUsername: item.owner_username,
    markingCount: item.marking_count,
    createdDate: item.created_date,
  };
}

/**
 * Fetches covers from GET /covers/.
 */
export async function getCovers(): Promise<CoverRecord[]> {
  const res = await apiClient.get<CoverApiResponse>("/covers/");
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Covers API: invalid response (missing results array)");
  }
  return data.results.map(mapApiResultToRecord);
}

/* -------------------------------------------------------------------------
 * Write API for the Cover graph
 *
 * The backend exposes three sibling resources that together describe a
 * marking's covers:
 *   * /covers/         -- the Cover row itself (code, color, type, dims, ...)
 *   * /cover-markings/ -- the link row tying a Cover to a Marking, with
 *                        is_backstamp/placement
 *   * /dates-seen/     -- polymorphic date rows (subject_type=COVER, subject_id=cover pk)
 *
 * The dialog drives all three from a single form, so the helpers below are
 * intentionally thin: they each map to one HTTP verb on one resource. The
 * dialog orchestrates the multi-call sequencing (create cover -> link it ->
 * write dates, or PATCH-then-diff dates for edit).
 *
 * `ensureCsrfToken()` is called before each unsafe verb so that a user who
 * just landed on the page (and therefore has no `csrftoken` cookie yet)
 * doesn't get a confusing 403 on the first save. Once the cookie is in
 * place axios picks it up via xsrfCookieName/xsrfHeaderName.
 * ----------------------------------------------------------------------- */

/** Body shape accepted by both POST and PATCH for /covers/. */
export interface CoverWritePayload {
  code?: string | null;
  /** Color FK id; null clears the colour. */
  color?: number | null;
  /** "FC" (Folded Cover) or "FL" (Folded Letter); null clears the choice. */
  type?: string | null;
  has_adhesive?: boolean;
  is_institutional?: boolean | null;
  /** Decimal mm; send "" or null to clear. */
  width?: string | number | null;
  height?: string | number | null;
}

/** Minimal response shape from /covers/ writes. */
export interface CoverWriteResult {
  id: number;
  code: string | null;
  color: number | null;
  type: string | null;
  has_adhesive: boolean;
  is_institutional: boolean | null;
  width: string | null;
  height: string | null;
}

/** POST /covers/ -- create a new Cover row. */
export async function createCover(
  payload: CoverWritePayload,
): Promise<CoverWriteResult> {
  await ensureCsrfToken();
  const res = await apiClient.post<CoverWriteResult>("/covers/", payload);
  return res.data;
}

/** PATCH /covers/{id}/ -- partial update of an existing Cover row. */
export async function updateCover(
  id: number,
  payload: CoverWritePayload,
): Promise<CoverWriteResult> {
  await ensureCsrfToken();
  const res = await apiClient.patch<CoverWriteResult>(
    `/covers/${id}/`,
    payload,
  );
  return res.data;
}

/* Note: there is no raw DELETE for covers. CoverV2ViewSet rejects DELETE with
 * 405; removing a cover goes through the audited, reversible recycle bin via
 * removeCover() / restoreCover() below. */

/** Body shape accepted by both POST and PATCH for /cover-markings/. */
export interface CoverMarkingWritePayload {
  cover?: number;
  marking?: number;
  is_backstamp?: boolean;
  placement?: string | null;
  contributor_comment?: string | null;
}

export interface CoverMarkingWriteResult {
  id: number;
  cover: number;
  marking: number;
  is_backstamp: boolean;
  placement: string | null;
}

/** POST /cover-markings/ -- link a Cover to a Marking. */
export async function createCoverMarking(
  payload: CoverMarkingWritePayload,
): Promise<CoverMarkingWriteResult> {
  await ensureCsrfToken();
  const res = await apiClient.post<CoverMarkingWriteResult>(
    "/cover-markings/",
    payload,
  );
  return res.data;
}

/** PATCH /cover-markings/{id}/ -- update is_backstamp/placement. */
export async function updateCoverMarking(
  id: number,
  payload: CoverMarkingWritePayload,
): Promise<CoverMarkingWriteResult> {
  await ensureCsrfToken();
  const res = await apiClient.patch<CoverMarkingWriteResult>(
    `/cover-markings/${id}/`,
    payload,
  );
  return res.data;
}

/** Granularity matches DateSeen.GRANULARITY_CHOICES on the backend. */
export type CoverDateGranularity = PartialDateSeenGranularity;

/** Body for creating a cover-scoped date via POST /dates-seen/. */
export interface CoverDateWritePayload {
  /** Cover pk; mapped to subject_type=COVER and subject_id on the API. */
  cover?: number;
  /** ISO date string (YYYY-MM-DD); month/year granularity uses YYYY-MM-01 / YYYY-01-01. */
  date?: string;
  granularity?: CoverDateGranularity;
}

export interface CoverDateWriteResult {
  id: number;
  cover: number;
  date: string;
  granularity: CoverDateGranularity;
}

type DateSeenApiRow = {
  id: number;
  subject_type?: string;
  subject_id?: number;
  date: string | null;
  granularity: CoverDateGranularity;
  date_year?: number | null;
  date_month?: number | null;
  date_day?: number | null;
};

function mapDateSeenToCoverDate(row: DateSeenApiRow): CoverDateWriteResult {
  const subjectId = row.subject_id;
  return {
    id: row.id,
    cover: typeof subjectId === "number" ? subjectId : 0,
    date: row.date ?? "",
    granularity: row.granularity,
  };
}

/** POST /dates-seen/ -- attach a date observation to a Cover. */
export async function createCoverDate(
  payload: CoverDateWritePayload,
): Promise<CoverDateWriteResult> {
  await ensureCsrfToken();
  const coverId = payload.cover;
  if (coverId == null) {
    throw new Error("cover id is required to create a cover date.");
  }
  const res = await apiClient.post<DateSeenApiRow>("/dates-seen/", {
    subject_type: "COVER",
    subject_id: coverId,
    date: payload.date,
    granularity: payload.granularity,
  });
  return mapDateSeenToCoverDate(res.data);
}

/** PATCH /dates-seen/{id}/ -- adjust date or granularity in place. */
export async function updateCoverDate(
  id: number,
  payload: CoverDateWritePayload,
): Promise<CoverDateWriteResult> {
  await ensureCsrfToken();
  const body: Record<string, unknown> = {};
  if (payload.date != null) body.date = payload.date;
  if (payload.granularity != null) body.granularity = payload.granularity;
  const res = await apiClient.patch<DateSeenApiRow>(`/dates-seen/${id}/`, body);
  return mapDateSeenToCoverDate(res.data);
}

/** DELETE /dates-seen/{id}/ -- remove a single date row. */
export async function deleteCoverDate(id: number): Promise<void> {
  await ensureCsrfToken();
  await apiClient.delete(`/dates-seen/${id}/`);
}

// Aliases for the in-progress DateSeen rename. Newer callers use these names;
// CoverEdit still uses CoverDate*. Both point at the same exports.
export type DateSeenGranularity = CoverDateGranularity;
export type DateSeenWritePayload = CoverDateWritePayload;
export type DateSeenWriteResult = CoverDateWriteResult;
export const createDateSeen = createCoverDate;
export const updateDateSeen = updateCoverDate;
export const deleteDateSeen = deleteCoverDate;

/* -------------------------------------------------------------------------
 * Cover detail (read)
 * ----------------------------------------------------------------------- */

export interface CoverDateSeenItem {
  id: number;
  date: string | null;
  granularity: PartialDateSeenGranularity;
  dateYear: number | null;
  dateMonth: number | null;
  dateDay: number | null;
}

function mapCoverDateSeen(raw: unknown): CoverDateSeenItem | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "number" ? o.id : Number(o.id);
  if (!Number.isFinite(id)) return null;
  const date = typeof o.date === "string" && o.date.trim() ? o.date : null;
  const dateYear = toIdOrNull(o.date_year ?? o.dateYear);
  const dateMonth = toIdOrNull(o.date_month ?? o.dateMonth);
  const dateDay = toIdOrNull(o.date_day ?? o.dateDay);
  if (!date && dateYear == null && dateMonth == null && dateDay == null) return null;
  const gRaw = String(o.granularity ?? "").toUpperCase();
  const granularity: CoverDateSeenItem["granularity"] =
    gRaw === "MONTH"
      ? "MONTH"
      : gRaw === "YEAR"
        ? "YEAR"
        : gRaw === "MONTH_ONLY"
          ? "MONTH_ONLY"
          : gRaw === "DAY_ONLY"
            ? "DAY_ONLY"
            : gRaw === "YEAR_DAY"
              ? "YEAR_DAY"
              : gRaw === "MONTH_DAY"
                ? "MONTH_DAY"
                : "DAY";
  return { id: id as number, date, granularity, dateYear, dateMonth, dateDay };
}

function decimalToString(v: unknown): string | null {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  return s || null;
}

function toIdOrNull(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Normalized GET /covers/{id}/ payload (aligned with AssociatedCoverDetails). */
export interface CoverDetail {
  id: number;
  code: string | null;
  colorId: number | null;
  colorName: string;
  type: string | null;
  hasAdhesive: boolean;
  isInstitutional: boolean | null;
  /** Submitter opted in to show their name on the public detail page. */
  displaySubmitterName: boolean;
  /** Submitter display name; null unless they opted in (server-gated). */
  submitterName: string | null;
  /** Free-text description / notes, shown on the public cover detail page. */
  description: string;
  width: string | null;
  height: string | null;
  datesSeen: CoverDateSeenItem[];
  /** True when this cover is in the recycle bin (soft-removed). */
  isRemoved: boolean;
  /** True when the current user may remove/restore this cover. */
  canRemove: boolean;
  createdDate: string;
  modifiedDate: string;
}

function mapCoverDetail(data: unknown): CoverDetail | null {
  if (!data || typeof data !== "object") return null;
  const o = data as Record<string, unknown>;
  const id = toIdOrNull(o.id);
  if (id == null) return null;
  const colorName =
    typeof o.color_name === "string" ? o.color_name : "";
  const datesRaw = Array.isArray(o.dates_seen) ? o.dates_seen : [];
  const datesSeen = datesRaw
    .map(mapCoverDateSeen)
    .filter((x): x is CoverDateSeenItem => x !== null);
  const hasAdhesive = o.has_adhesive == null ? false : Boolean(o.has_adhesive);
  return {
    id,
    code: typeof o.code === "string" && o.code ? o.code : null,
    colorId: toIdOrNull(o.color),
    colorName,
    type: typeof o.type === "string" && o.type ? o.type : null,
    hasAdhesive,
    isInstitutional:
      o.is_institutional == null ? null : Boolean(o.is_institutional),
    displaySubmitterName: Boolean(o.display_submitter_name),
    submitterName:
      typeof o.submitter_name === "string" && o.submitter_name
        ? o.submitter_name
        : null,
    description: typeof o.description === "string" ? o.description : "",
    width: decimalToString(o.width),
    height: decimalToString(o.height),
    datesSeen,
    isRemoved: Boolean((o as { is_removed?: boolean }).is_removed),
    canRemove: Boolean((o as { can_remove?: boolean }).can_remove),
    createdDate:
      typeof o.created_date === "string" ? o.created_date : "",
    modifiedDate:
      typeof o.modified_date === "string" ? o.modified_date : "",
  };
}

/** GET /covers/{id}/ -- read a single cover row. */
export async function getCoverById(coverId: number): Promise<CoverDetail | null> {
  try {
    const res = await apiClient.get(`/covers/${coverId}/`);
    return mapCoverDetail(res.data);
  } catch {
    return null;
  }
}

/* -------------------------------------------------------------------------
 * Recycle bin (editor soft-remove / restore)
 *
 * Mirrors removeMarking/restoreMarking/getRecycleBinMarkings in
 * services/markings.ts. Removing a cover moves it to the recycle bin (hidden
 * from the public catalog) and is reversible by restore. Both are editor-only
 * on the backend (responsible editor for the cover's region, or superuser).
 * ----------------------------------------------------------------------- */

/** Editor: POST /covers/{id}/remove/ -- soft-remove into recycle bin. */
export async function removeCover(
  coverId: number,
  reason?: string,
): Promise<{ ok: true } | { ok: false; message: string }> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/covers/${coverId}/remove/`, { reason: reason ?? "" });
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: string } } };
    const d = ax.response?.data?.detail;
    return { ok: false, message: typeof d === "string" ? d : "Could not remove cover." };
  }
}

/** Editor: POST /covers/{id}/restore/ -- restore from recycle bin. */
export async function restoreCover(
  coverId: number,
): Promise<{ ok: true } | { ok: false; message: string }> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/covers/${coverId}/restore/`, {});
    return { ok: true };
  } catch (err: unknown) {
    const ax = err as { response?: { data?: { detail?: string } } };
    const d = ax.response?.data?.detail;
    return { ok: false, message: typeof d === "string" ? d : "Could not restore cover." };
  }
}

/** One removed cover row from GET /covers/recycle-bin/ (CoverSerializer shape). */
export interface RecycleBinCover {
  id: number;
  code: string | null;
  colorName: string;
  type: string | null;
}

export interface GetRecycleBinCoversResult {
  results: RecycleBinCover[];
  count: number;
  next: string | null;
  previous: string | null;
}

/** Editor: GET /covers/recycle-bin/ -- removed covers (region-scoped). */
export async function getRecycleBinCovers(
  page: number = 1,
  pageSize: number = 10,
): Promise<GetRecycleBinCoversResult> {
  const params = { page: String(page), page_size: String(pageSize) };
  const res = await apiClient.get<{
    count: number;
    next: string | null;
    previous: string | null;
    results: unknown[];
  }>("/covers/recycle-bin/", { params });
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Cover recycle bin API: invalid response (missing results array)");
  }
  const results: RecycleBinCover[] = data.results.map((raw) => {
    const o = raw as Record<string, unknown>;
    return {
      id: Number(o.id),
      code: typeof o.code === "string" && o.code ? o.code : null,
      colorName: typeof o.color_name === "string" ? o.color_name : "",
      type: typeof o.type === "string" && o.type ? o.type : null,
    };
  });
  return { results, count: data.count, next: data.next, previous: data.previous };
}
