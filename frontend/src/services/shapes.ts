/**
 * Shapes (v2 Shape entity): GET /shapes/.
 * Shape is a shared vocabulary used by Townmark, Ratemark, and Auxmark.
 */
import apiClient from "@/lib/api";

/** One item from GET /shapes/ (DRF snake_case) */
export interface ShapeApiResultItem {
  id: number;
  name: string;
  code?: string | null;
  notes?: string | null;
}

/** Paginated response from GET /shapes/ */
export interface ShapeApiResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: ShapeApiResultItem[];
}

/** Normalized option for dropdowns / filters */
export interface ShapeOption {
  id: number;
  name: string;
  description: string;
}

function mapApiResultToOption(item: ShapeApiResultItem): ShapeOption {
  return {
    id: item.id,
    name: item.name ?? "",
    description: (item.notes ?? "").trim(),
  };
}

/**
 * Fetches all pages of v2 shapes.
 */
export async function getShapes(): Promise<ShapeOption[]> {
  const allResults: ShapeOption[] = [];
  let nextUrl: string | null = "/shapes/";
  let safetyCounter = 0;

  while (nextUrl && safetyCounter < 50) {
    const res = await apiClient.get<ShapeApiResponse>(nextUrl);
    const data = res.data;
    if (!Array.isArray(data.results)) {
      throw new Error("Shapes API: invalid response (missing results array)");
    }

    allResults.push(
      ...data.results
        .map(mapApiResultToOption)
        .filter((x) => x.id > 0 && x.name.trim() !== "")
    );

    nextUrl = typeof data.next === "string" && data.next.trim() !== "" ? data.next : null;
    safetyCounter += 1;
  }

  return allResults;
}
