/**
 * Citations (v2 Citation entity): GET /citations/.
 * Links a ReferenceWork to a subject (Cover or Marking) with a
 * free-form `citation_detail` string.
 */
import apiClient, { ensureCsrfToken } from "@/lib/api";

/** Subject polymorphic discriminator (v2 model) */
export type CitationSubjectType = "COVER" | "MARKING";

/** One item from GET /citations/ (DRF snake_case) */
export interface CitationApiResultItem {
  citation_id: number;
  reference_work_id: number;
  subject_id: number;
  subject_type: CitationSubjectType;
  citation_detail: string;
  created_date: string;
  modified_date: string;
}

/** Paginated response from GET /citations/ */
export interface CitationApiResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: CitationApiResultItem[];
}

/** Normalized citation for list/detail */
export interface CitationRecord {
  id: number;
  referenceWorkId: number;
  subjectId: number;
  subjectType: CitationSubjectType;
  citationDetail: string;
  createdDate: string;
  modifiedDate: string;
}

function mapApiResultToRecord(item: CitationApiResultItem): CitationRecord {
  return {
    id: item.citation_id,
    referenceWorkId: item.reference_work_id,
    subjectId: item.subject_id,
    subjectType: item.subject_type,
    citationDetail: item.citation_detail,
    createdDate: item.created_date,
    modifiedDate: item.modified_date,
  };
}

/**
 * Fetches citations from GET /citations/.
 */
export async function getCitations(): Promise<CitationRecord[]> {
  const res = await apiClient.get<CitationApiResponse>("/citations/");
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Citations API: invalid response (missing results array)");
  }
  return data.results.map(mapApiResultToRecord);
}

/** Polymorphic subject type accepted by v2 Citation rows. */
export type WritableCitationSubjectType = "COVER" | "MARKING";

export interface CitationSubjectRow {
  id: number;
  referenceWorkId: number;
  subjectType: WritableCitationSubjectType;
  subjectId: number;
  citationDetail: string;
}

function mapCitationSubjectRow(raw: unknown): CitationSubjectRow | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "number" ? o.id : Number(o.id);
  const ref =
    typeof o.reference_work === "number"
      ? o.reference_work
      : typeof o.reference_work === "string"
        ? Number(o.reference_work)
        : Number((o as { reference_work_id?: unknown }).reference_work_id);
  const sid = typeof o.subject_id === "number" ? o.subject_id : Number(o.subject_id);
  const st = String(o.subject_type ?? "").toUpperCase();
  const subjectType: WritableCitationSubjectType = st === "COVER" ? "COVER" : "MARKING";
  const citationDetail = typeof o.citation_detail === "string" ? o.citation_detail : "";
  if (!Number.isFinite(id) || !Number.isFinite(ref) || !Number.isFinite(sid)) return null;
  return {
    id,
    referenceWorkId: ref,
    subjectType,
    subjectId: sid,
    citationDetail,
  };
}

export async function listCitationsForSubject(params: {
  subjectType: WritableCitationSubjectType;
  subjectId: number;
}): Promise<CitationSubjectRow[]> {
  try {
    const res = await apiClient.get<{ results?: unknown[] }>("/citations/", {
      params: {
        subject_type: params.subjectType,
        subject_id: params.subjectId,
      },
    });
    const rows = Array.isArray(res.data?.results) ? res.data.results : [];
    return rows.map(mapCitationSubjectRow).filter((x): x is CitationSubjectRow => x != null);
  } catch {
    return [];
  }
}

export async function createCitationSubject(params: {
  referenceWorkId: number;
  subjectType: WritableCitationSubjectType;
  subjectId: number;
  citationDetail: string;
}): Promise<CitationSubjectRow | null> {
  try {
    await ensureCsrfToken();
    const res = await apiClient.post("/citations/", {
      reference_work: params.referenceWorkId,
      subject_type: params.subjectType,
      subject_id: params.subjectId,
      citation_detail: params.citationDetail,
    });
    return mapCitationSubjectRow(res.data);
  } catch {
    return null;
  }
}

export async function updateCitationSubject(
  id: number,
  citationDetail: string,
): Promise<boolean> {
  try {
    await ensureCsrfToken();
    await apiClient.patch(`/citations/${id}/`, { citation_detail: citationDetail });
    return true;
  } catch {
    return false;
  }
}

export async function deleteCitationSubject(id: number): Promise<boolean> {
  try {
    await ensureCsrfToken();
    await apiClient.delete(`/citations/${id}/`);
    return true;
  } catch {
    return false;
  }
}
