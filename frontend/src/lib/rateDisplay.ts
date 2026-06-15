export function formatRateValue(value: string | number | null | undefined): string {
  if (value == null || String(value).trim() === "") return "";
  const cents = Number(String(value).trim());
  if (!Number.isFinite(cents)) return "";
  return `${cents}¢`;
}
