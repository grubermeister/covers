/**
 * Reference works (v2 ReferenceWork entity): GET /reference-works/.
 */
import apiClient from "@/lib/api";

/** User object in createdBy/modifiedBy */
export interface ReferenceWorkUser {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
}

/** One item from GET /reference-works/ (DRF snake_case, matching v2 model) */
export interface ReferenceWorkApiResultItem {
  reference_work_id: number;
  created_by?: ReferenceWorkUser;
  modified_by?: ReferenceWorkUser;
  created_date: string;
  modified_date: string;
  code: string | null;
  title: string;
  authorship: string;
  publisher: string;
  publication_year: number | null;
  edition: string;
  volume: string;
  isbn: string;
  url: string;
}

/** Paginated response from GET /reference-works/ */
export interface ReferenceWorkApiResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: ReferenceWorkApiResultItem[];
}

/** Normalized reference work for list/detail / dropdowns */
export interface ReferenceWorkRecord {
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
  createdDate: string;
  modifiedDate: string;
}

function mapApiResultToRecord(item: ReferenceWorkApiResultItem): ReferenceWorkRecord {
  return {
    id: item.reference_work_id,
    code: item.code ?? null,
    title: item.title,
    authorship: item.authorship,
    publisher: item.publisher,
    publicationYear: item.publication_year,
    edition: item.edition,
    volume: item.volume,
    isbn: item.isbn,
    url: item.url,
    createdDate: item.created_date,
    modifiedDate: item.modified_date,
  };
}

/**
 * Add an English ordinal suffix to a positive integer: 1 -> "1st", 2 -> "2nd",
 * 3 -> "3rd", 4-20 -> "Nth", 21 -> "21st", etc. Returns the string unchanged
 * when it is not a plain non-negative integer (so editions already typed as
 * "5th" or "Revised" pass through verbatim).
 */
function withOrdinalSuffix(raw: string): string {
  const t = raw.trim();
  if (!/^\d+$/.test(t)) return t;
  const n = parseInt(t, 10);
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  const mod10 = n % 10;
  if (mod10 === 1) return `${n}st`;
  if (mod10 === 2) return `${n}nd`;
  if (mod10 === 3) return `${n}rd`;
  return `${n}th`;
}

/**
 * Display label for a reference work in dropdowns and chips. Includes the code
 * and edition so different editions of the same title (e.g. two editions of
 * "American Stampless Cover Catalog") are visually distinct. Falls back to
 * title or a "#id" sentinel when code/title are missing.
 *
 * Examples:
 *   { code: "ASCC1", edition: "5", title: "American Stampless Cover Catalog" }
 *     -> "ASCC1 - American Stampless Cover Catalog (5th Ed.)"
 *   { code: "ASCC",  edition: "",  title: "American Stampless Cover Catalog" }
 *     -> "ASCC - American Stampless Cover Catalog"
 *   { code: null,    edition: "",  title: "Something" } -> "Something"
 */
export function formatReferenceWorkLabel(
  work: Pick<ReferenceWorkRecord, "id" | "code" | "title" | "edition">,
  fallback: string = `Reference work #${work.id}`,
): string {
  const code = (work.code ?? "").trim();
  const edition = (work.edition ?? "").trim();
  const title = (work.title ?? "").trim();
  const head = code ? (title ? `${code} - ${title}` : code) : title;
  const base = head || fallback;
  if (!edition) return base;
  return `${base} (${withOrdinalSuffix(edition)} Ed.)`;
}

/**
 * Fetches reference works from GET /reference-works/.
 */
export async function getReferenceWorks(): Promise<ReferenceWorkRecord[]> {
  const res = await apiClient.get<ReferenceWorkApiResponse>("/reference-works/");
  const data = res.data;
  if (!Array.isArray(data.results)) {
    throw new Error("Reference works API: invalid response (missing results array)");
  }
  return data.results.map(mapApiResultToRecord);
}
