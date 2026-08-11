/**
 * Contributions (v2 Contribution moderation tickets): list, read, submit, and
 * editor decisions (approve / reject / request-revision).
 *
 * Single source for the contribution transport that used to be copy-pasted as
 * raw fetch() across ContributionDetail, CoverContributionDetail, Dashboard,
 * Contribute, and CoverEdit. Each of those sites manually
 * re-read VITE_API_URL, read the CSRF cookie, hand-built headers, parsed res.ok,
 * and re-implemented camelCase/snake_case normalization. All of that lives here
 * now and goes through the configured apiClient.
 *
 * The backend renders responses with CamelCaseJSONRenderer, so reads use
 * camelCase first and fall back to snake_case. ensureCsrfToken() runs before
 * every unsafe verb so a freshly-landed user does not 403 on the first write.
 */
import apiClient, { ensureCsrfToken } from "@/lib/api";

const CONTRIBUTION_UPLOAD_TIMEOUT_MS = 300000;

/**
 * One contribution row off the wire. Tolerates camelCase (renderer) and
 * snake_case, plus the optional display fields the editor lists read.
 */
export interface ContributionApiItem {
  id: number;
  status?: string;
  contributor?: number | null;
  contributorId?: number | null;
  contributor_username?: string;
  contributorUsername?: string;
  review_notes?: string | null;
  reviewNotes?: string | null;
  created_at?: string;
  createdAt?: string;
  submitted_data?: Record<string, unknown>;
  submittedData?: Record<string, unknown>;
  display_name?: string;
  displayName?: string;
  marking_id?: number | null;
  markingId?: number | null;
  cover_id?: number | null;
  coverId?: number | null;
  // ContributionDetailSerializer exposes the approved marking link as plain
  // "marking" (the FK pk), distinct from the list serializer's "marking_id".
  marking?: number | null;
  [k: string]: unknown;
}

/** DRF paginated envelope for GET /contributions/ (count/results survive camelCasing). */
export interface ContributionListResponse {
  count?: number;
  next?: string | null;
  previous?: string | null;
  results?: ContributionApiItem[];
}

/**
 * Normalized contribution for the SPA. Replaces the inline Contribution /
 * ContributionRecord / SubmittedData interfaces that used to live in the page
 * components. `submittedData` stays a loose record; callers read it through the
 * lib/contribution* accessors (contributionToFields, contributionImages,
 * contributionDisplay).
 */
export interface Contribution {
  id: number;
  contributorId: number | null;
  status: string;
  contributorUsername: string;
  reviewNotes: string | null;
  createdAt: string;
  submittedData: Record<string, unknown>;
  displayName?: string;
  markingId: number | null;
  coverId: number | null;
}

export interface CatalogCodeSuggestion {
  catalogCode: string;
  prefix: string;
  referenceCode: string;
  regionAbbrev: string;
  subjectType: "MARKING" | "COVER";
}

function toNumberOrNull(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** First value that is neither null nor undefined, else null. */
function firstDefined(...vals: unknown[]): unknown {
  for (const v of vals) {
    if (v !== undefined && v !== null) return v;
  }
  return null;
}

export function mapApiItemToContribution(item: ContributionApiItem): Contribution {
  const submittedRaw = item.submitted_data ?? item.submittedData ?? {};
  const submittedData =
    submittedRaw && typeof submittedRaw === "object"
      ? (submittedRaw as Record<string, unknown>)
      : {};
  return {
    id: item.id,
    contributorId: toNumberOrNull(firstDefined(item.contributor, item.contributorId)),
    status: item.status ?? "pending",
    contributorUsername: String(
      item.contributor_username ?? item.contributorUsername ?? "",
    ),
    reviewNotes: (item.review_notes ?? item.reviewNotes ?? null) as string | null,
    createdAt: String(item.created_at ?? item.createdAt ?? ""),
    submittedData,
    displayName: (item.display_name ?? item.displayName) as string | undefined,
    // "marking" is what the detail serializer returns; without it the detail
    // page sees a null marking id and the approved-to-record redirect never fires.
    markingId: toNumberOrNull(firstDefined(item.marking_id, item.markingId, item.marking)),
    coverId: toNumberOrNull(firstDefined(item.cover_id, item.coverId)),
  };
}

/** Query filters for GET /contributions/. camelCase here maps to snake_case params. */
export interface ContributionListParams {
  /** "archived" is the editor recycle bin -- same scoping as "editor" (#89). */
  mode?: "editor" | "archived";
  status?: string;
  state?: string;
  page?: number;
  pageSize?: number;
  ordering?: string;
}

export interface ContributionListResult {
  items: Contribution[];
  /** null when the API returns a bare array instead of a paginated envelope. */
  count: number | null;
  /** Raw rows for callers (Dashboard) that derive image/display strings off the wire shape. */
  rawItems: ContributionApiItem[];
}

/** GET /contributions/ with optional filters. Handles array or paginated responses. */
export async function listContributions(
  params?: ContributionListParams,
): Promise<ContributionListResult> {
  const query: Record<string, string | number> = {};
  if (params?.mode) query.mode = params.mode;
  if (params?.status) query.status = params.status;
  if (params?.state) query.state = params.state;
  if (params?.page != null) query.page = params.page;
  if (params?.pageSize != null) query.page_size = params.pageSize;
  if (params?.ordering) query.ordering = params.ordering;

  const res = await apiClient.get("/contributions/", { params: query });
  const data = res.data;
  const rawItems: ContributionApiItem[] = Array.isArray(data)
    ? (data as ContributionApiItem[])
    : Array.isArray((data as ContributionListResponse)?.results)
      ? ((data as ContributionListResponse).results as ContributionApiItem[])
      : [];
  const count = Array.isArray(data)
    ? data.length
    : typeof (data as ContributionListResponse)?.count === "number"
      ? ((data as ContributionListResponse).count as number)
      : null;
  return { items: rawItems.map(mapApiItemToContribution), count, rawItems };
}

/** GET /contributions/{id}/ -- normalized single contribution. Errors propagate. */
export async function getContribution(id: number): Promise<Contribution> {
  const res = await apiClient.get(`/contributions/${id}/`);
  return mapApiItemToContribution(res.data as ContributionApiItem);
}

/** Minimal write result; `raw` is the untouched response for any field not unified here. */
export interface ContributionWriteResult {
  contributionId: number | null;
  markingId: number | null;
  coverId: number | null;
  raw: Record<string, unknown>;
}

function mapWriteResult(raw: unknown): ContributionWriteResult {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    contributionId: toNumberOrNull(
      firstDefined(o.contributionId, o.contribution_id, o.id),
    ),
    markingId: toNumberOrNull(firstDefined(o.marking_id, o.markingId)),
    coverId: toNumberOrNull(firstDefined(o.cover_id, o.coverId)),
    raw: o,
  };
}

/**
 * POST /contributions/ -- submit a contribution (new, draft, edit, or abandon).
 *
 * The caller builds the body. Pass a FormData when uploading images (apiClient
 * strips Content-Type so the browser sets the multipart boundary) or a plain
 * object for JSON submissions (axios sets application/json). The 30-field body
 * builders stay in the page components since they are tightly coupled to form
 * state; this function owns only CSRF + transport + response normalization.
 */
export async function createContribution(
  body: FormData | Record<string, unknown>,
): Promise<ContributionWriteResult> {
  await ensureCsrfToken();
  const isMultipart = typeof FormData !== "undefined" && body instanceof FormData;
  const res = await apiClient.post(
    "/contributions/",
    body,
    isMultipart ? { timeout: CONTRIBUTION_UPLOAD_TIMEOUT_MS } : undefined,
  );
  return mapWriteResult(res.data);
}

/**
 * Discard a cover Contribution draft after its cover was submitted via the
 * catalog API. Thin wrapper over createContribution with the abandon flags.
 */
export async function abandonCoverContributionDraft(contributionId: number): Promise<void> {
  const form = new FormData();
  form.append("submission_kind", "cover");
  form.append("abandon_draft", "true");
  form.append("edit_contribution_id", String(contributionId));
  await createContribution(form);
}

export type ContributionAction = "approve" | "reject" | "revision";

export interface DecideOptions {
  /** Saved as review_notes; sent only when non-empty. */
  reviewNotes?: string;
  /** Approve only; sent only when provided. */
  estimatedValue?: number;
  /** Approve only; editor-owned catalog code. Empty means regenerate. */
  catalogCode?: string | null;
}

export interface DecideResult {
  markingId: number | null;
  coverId: number | null;
  raw: Record<string, unknown>;
}

/**
 * POST /contributions/{id}/{approve|reject|request-revision}/.
 * "revision" maps to the "request-revision" path.
 */
export async function decideContribution(
  id: number,
  action: ContributionAction,
  opts?: DecideOptions,
): Promise<DecideResult> {
  await ensureCsrfToken();
  const actionPath =
    action === "approve" ? "approve" : action === "reject" ? "reject" : "request-revision";
  const body: Record<string, unknown> = {};
  const notes = opts?.reviewNotes?.trim();
  if (notes) body.review_notes = notes;
  if (opts?.estimatedValue != null) body.estimated_value = opts.estimatedValue;
  if (opts && "catalogCode" in opts) body.catalog_code = opts.catalogCode ?? "";

  const res = await apiClient.post(`/contributions/${id}/${actionPath}/`, body);
  const o = res.data && typeof res.data === "object" ? (res.data as Record<string, unknown>) : {};
  return {
    markingId: toNumberOrNull(firstDefined(o.marking_id, o.markingId)),
    coverId: toNumberOrNull(firstDefined(o.cover_id, o.coverId)),
    raw: o,
  };
}

function mapCatalogCodeSuggestion(raw: unknown): CatalogCodeSuggestion {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    catalogCode: String(o.catalog_code ?? o.catalogCode ?? ""),
    prefix: String(o.prefix ?? ""),
    referenceCode: String(o.reference_code ?? o.referenceCode ?? ""),
    regionAbbrev: String(o.region_abbrev ?? o.regionAbbrev ?? ""),
    subjectType:
      String(o.subject_type ?? o.subjectType ?? "MARKING").toUpperCase() === "COVER"
        ? "COVER"
        : "MARKING",
  };
}

export async function getContributionCatalogCodeSuggestion(
  contributionId: number,
  opts?: { force?: boolean },
): Promise<CatalogCodeSuggestion> {
  await ensureCsrfToken();
  const res = await apiClient.post(
    `/contributions/${contributionId}/catalog-code-suggestion/`,
    opts?.force ? { force: true } : {},
  );
  return mapCatalogCodeSuggestion(res.data);
}

export async function getDirectCatalogCodeSuggestion(payload: {
  subjectType: "MARKING" | "COVER";
  state?: string;
  regionId?: number | null;
  markingId?: number | null;
  referenceWorkId?: number | null;
  referenceWorkIds?: number[];
  excludeId?: number | null;
}): Promise<CatalogCodeSuggestion> {
  await ensureCsrfToken();
  const res = await apiClient.post("/catalog-code-suggestions/", {
    subject_type: payload.subjectType,
    state: payload.state,
    region_id: payload.regionId ?? undefined,
    marking_id: payload.markingId ?? undefined,
    reference_work_id: payload.referenceWorkId ?? undefined,
    reference_work_ids: payload.referenceWorkIds,
    exclude_id: payload.excludeId ?? undefined,
  });
  return mapCatalogCodeSuggestion(res.data);
}

// DELETE /contributions/{id}/  -- hard-deletes (withdraws) an unapproved
// contribution; backend enforces IsOwnDeletableContribution (status must NOT be
// "approved" and requester must be the owner or a superuser). No response body.
export async function deleteOwnContribution(contributionId: number): Promise<void> {
  await ensureCsrfToken();
  await apiClient.delete(`/contributions/${contributionId}/`);
}

/** Result shape shared by the archive/restore actions; mirrors removeMarking. */
type ArchiveResult = { ok: true } | { ok: false; message: string };

function archiveError(err: unknown, fallback: string): ArchiveResult {
  const ax = err as { response?: { data?: { detail?: string } } };
  const detail = ax.response?.data?.detail;
  return { ok: false, message: typeof detail === "string" ? detail : fallback };
}

/**
 * Editor: POST /contributions/{id}/archive/ -- clear a reviewed entry off the
 * review dashboard (Issue #89). Soft: the contribution is untouched and stays
 * visible to its contributor. The backend rejects a pending contribution.
 */
export async function archiveContribution(
  contributionId: number,
  reason?: string,
): Promise<ArchiveResult> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/contributions/${contributionId}/archive/`, {
      reason: reason ?? "",
    });
    return { ok: true };
  } catch (err: unknown) {
    return archiveError(err, "Could not archive this entry.");
  }
}

/** Editor: POST /contributions/{id}/restore/ -- put an archived entry back in the queue. */
export async function restoreContribution(
  contributionId: number,
): Promise<ArchiveResult> {
  await ensureCsrfToken();
  try {
    await apiClient.post(`/contributions/${contributionId}/restore/`, {});
    return { ok: true };
  } catch (err: unknown) {
    return archiveError(err, "Could not restore this entry.");
  }
}
