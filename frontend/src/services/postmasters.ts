/**
 * Postmasters (v2): GET /postmasters/ and GET /postmaster-tenures/.
 *
 * A tenure is one appointment *event*, not a span -- that is what the source
 * catalogs record. An end date is implied by the next appointment at the same
 * office and is deliberately not asserted here.
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
  let nextUrl: string | null = `/postmaster-tenures/?post_office=${postOfficeId}`;
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
