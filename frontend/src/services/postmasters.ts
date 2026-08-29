/**
 * Postmasters (v2): GET /postmasters/ and GET /postmaster-tenures/.
 *
 * A tenure is one appointment *event*, not a span -- that is what the source
 * catalogs record, and nothing here invents an end date for one. An end is
 * implied by the next appointment at the same office, and lib/postmasterSpans.ts
 * derives one for *display only*, where it can be labelled as inferred.
 *
 * Keep that derivation out of this layer and out of the API payload. A derived
 * end date sitting beside recorded ones reads as recorded, and every future
 * consumer would inherit the guess without seeing the caveat.
 */
import apiClient from "@/lib/api";

/** One item from GET /postmaster-tenures/ (DRF snake_case) */
export interface PostmasterTenureApiResultItem {
  tenure_id: number;
  post_office: number;
  post_office_name?: string | null;
  postmaster: number | null;
  postmaster_name?: string | null;
  event: string;
  date_appointed: string | null;
  date_appointed_granularity?: string | null;
  source_ref?: string | null;
}

export interface PostmasterTenureApiResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: PostmasterTenureApiResultItem[];
}

/** Normalized tenure for display */
export interface PostmasterTenure {
  id: number;
  postOfficeId: number;
  postOfficeName: string;
  postmasterId: number | null;
  postmasterName: string;
  event: string;
  dateAppointed: string | null;
  granularity: string;
  sourceRef: string;
}

const EVENT_LABELS: Record<string, string> = {
  appointment: "Appointed",
  reappointment: "Reappointed",
  discontinued: "Office discontinued",
  unknown: "Unknown",
};

export function tenureEventLabel(event: string): string {
  return EVENT_LABELS[event] ?? event;
}

/**
 * A date shown to the precision the source actually stated. Storing 1 January
 * for a year-only record would assert a precision nobody has, so do not
 * display one either.
 */
export function tenureDateLabel(tenure: PostmasterTenure): string {
  if (!tenure.dateAppointed) return "date unknown";
  const [year, month, day] = tenure.dateAppointed.split("-");
  if (tenure.granularity === "YEAR") return year;
  if (tenure.granularity === "MONTH") return `${year}-${month}`;
  return `${year}-${month}-${day}`;
}

function mapTenure(item: PostmasterTenureApiResultItem): PostmasterTenure {
  return {
    id: item.tenure_id,
    postOfficeId: item.post_office,
    postOfficeName: (item.post_office_name ?? "").trim(),
    postmasterId: item.postmaster ?? null,
    postmasterName: (item.postmaster_name ?? "").trim(),
    event: item.event ?? "unknown",
    dateAppointed: item.date_appointed ?? null,
    granularity: (item.date_appointed_granularity ?? "").trim(),
    sourceRef: (item.source_ref ?? "").trim(),
  };
}

/** Every appointment recorded for one post office, oldest first. */
export async function getTenuresForPostOffice(
  postOfficeId: number
): Promise<PostmasterTenure[]> {
  const all: PostmasterTenure[] = [];
  // Ask for the largest page the API allows (PageSizePagination.max_page_size).
  // The default is 10, and the loop below awaits each page in turn, so a busy
  // office like Abingdon cost four serial round trips for one short list.
  let nextUrl: string | null =
    `/postmaster-tenures/?post_office=${postOfficeId}&page_size=100`;
  let safetyCounter = 0;

  while (nextUrl && safetyCounter < 50) {
    const res = await apiClient.get<PostmasterTenureApiResponse>(nextUrl);
    const data = res.data;
    if (!Array.isArray(data.results)) {
      throw new Error("Postmaster tenures API: invalid response (missing results array)");
    }
    all.push(...data.results.map(mapTenure));
    nextUrl =
      typeof data.next === "string" && data.next.trim() !== "" ? data.next : null;
    safetyCounter += 1;
  }

  return all;
}
