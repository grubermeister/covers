/**
 * Contributions (v2 Contribution moderation tickets): list, read, submit, and
 * editor decisions (approve / reject / request-revision).
 *
 * Single source for the contribution transport that used to be copy-pasted as
 * raw fetch() across ContributionDetail, CoverContributionDetail, Dashboard,
 * Contribute, CoverEdit, and EditSubmissionDialog. Each of those sites manually
 * re-read VITE_API_URL, read the CSRF cookie, hand-built headers, parsed res.ok,
 * and re-implemented camelCase/snake_case normalization. All of that lives here
 * now and goes through the configured apiClient.
 *
 * The backend renders responses with CamelCaseJSONRenderer, so reads tolerate
 * camelCase first and fall back to snake_case. ensureCsrfToken() runs before
 * every unsafe verb so a freshly-landed user does not 403 on the first write.
 */
import apiClient, { ensureCsrfToken } from "@/lib/api";

/**
 * One contribution row off the wire. Tolerates camelCase (renderer) and
 * snake_case, plus the long tail of optional display fields the editor lists
 * read. Kept loose on purpose -- this is the union of every shape the API has
 * historically returned for a contribution.
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
  postmark?: number | null;
  postmark_id?: number | null;
  postmarkId?: number | null;
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
  /** Unified postmark / postmark_id / postmarkId (set once approved). */
  postmarkId: number | null;
  markingId: number | null;
  coverId: number | null;
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
    // page sees a null marking id and the approved->record redirect never fires.
    postmarkId: toNumberOrNull(
      firstDefined(item.postmark_id, item.postmarkId, item.postmark, item.marking_id, item.markingId, item.marking),
    ),
    markingId: toNumberOrNull(firstDefined(item.marking_id, item.markingId, item.marking)),
    coverId: toNumberOrNull(firstDefined(item.cover_id, item.coverId)),
  };
}

/** Query filters for GET /contributions/. camelCase here maps to snake_case params. */
export interface ContributionListParams {
  mode?: "editor";
  status?: string;
  state?: string;
  kind?: "suggestion";
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
  if (params?.kind) query.kind = params.kind;
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
  postmarkId: number | null;
  coverId: number | null;
  raw: Record<string, unknown>;
}

function mapWriteResult(raw: unknown): ContributionWriteResult {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    contributionId: toNumberOrNull(
      firstDefined(o.contributionId, o.contribution_id, o.id),
    ),
    postmarkId: toNumberOrNull(firstDefined(o.postmark_id, o.postmarkId, o.postmark, o.marking_id, o.markingId)),
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
  const res = await apiClient.post("/contributions/", body);
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
}

export interface DecideResult {
  postmarkId: number | null;
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

  const res = await apiClient.post(`/contributions/${id}/${actionPath}/`, body);
  const o = res.data && typeof res.data === "object" ? (res.data as Record<string, unknown>) : {};
  return {
    postmarkId: toNumberOrNull(firstDefined(o.postmark_id, o.postmarkId, o.postmark, o.marking_id, o.markingId)),
    coverId: toNumberOrNull(firstDefined(o.cover_id, o.coverId)),
    raw: o,
  };
}

// DELETE /contributions/{id}/  -- hard-deletes a draft; backend enforces IsDraftOwner
// (status must be "draft" and requester must be owner or superuser). No response body.
export async function deleteDraftContribution(contributionId: number): Promise<void> {
  await ensureCsrfToken();
  await apiClient.delete(`/contributions/${contributionId}/`);
}
