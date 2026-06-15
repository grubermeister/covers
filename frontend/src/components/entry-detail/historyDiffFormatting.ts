function humanizeFieldKey(key: string): string {
  return key.replace(/_/g, " ");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatScalarDiffValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "(empty)";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

function formatCitationDiffValue(value: Record<string, unknown>): string | null {
  if (!("reference_work_id" in value) && !("citation_detail" in value)) return null;

  const referenceWorkId = formatScalarDiffValue(value.reference_work_id);
  const citationDetail = formatScalarDiffValue(value.citation_detail);
  const base =
    referenceWorkId === "(empty)"
      ? "Reference work"
      : `Reference work #${referenceWorkId}`;

  return citationDetail === "(empty)" ? base : `${base}: ${citationDetail}`;
}

function formatObjectDiffValue(value: Record<string, unknown>): string {
  const citationValue = formatCitationDiffValue(value);
  if (citationValue) return citationValue;

  const entries = Object.entries(value)
    .filter(([, itemValue]) => itemValue !== null && itemValue !== undefined && itemValue !== "")
    .slice(0, 3)
    .map(([key, itemValue]) => {
      const scalar = formatScalarDiffValue(itemValue);
      return `${humanizeFieldKey(key)}: ${scalar || "[complex value]"}`;
    });

  return entries.length > 0 ? entries.join(", ") : "(empty)";
}

export function formatDiffValue(value: unknown): string {
  const scalar = formatScalarDiffValue(value);
  if (scalar) return scalar;

  if (Array.isArray(value)) {
    if (value.length === 0) return "(none)";
    const formattedItems = value.slice(0, 3).map((item) => formatDiffValue(item));
    const hiddenCount = value.length - formattedItems.length;
    const suffix = hiddenCount > 0 ? `; +${hiddenCount} more` : "";
    return `${formattedItems.join("; ")}${suffix}`;
  }

  if (isRecord(value)) return formatObjectDiffValue(value);

  return String(value);
}
