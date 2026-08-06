export const TRUE_CIRCLE_SHAPE_CODES = ["C", "CDS", "DC", "DLC", "DLDC"] as const;
const TRUE_CIRCLE_SHAPE_LABELS = new Set([
  "CIRCLE",
  "CDS",
  "DOUBLE CIRCLE",
  "DOUBLE LINE CIRCLE",
  "DOUBLE LINE DOUBLE CIRCLE",
]);

export function shapeCodeFromName(shapeName: string | null | undefined): string {
  const s = String(shapeName ?? "").trim();
  if (!s) return "";
  return s.split(" - ", 1)[0].trim().toUpperCase();
}

export function isTrueCircleShapeName(shapeName: string | null | undefined): boolean {
  const s = String(shapeName ?? "").trim();
  if (!s) return false;
  const code = shapeCodeFromName(shapeName);
  if ((TRUE_CIRCLE_SHAPE_CODES as readonly string[]).includes(code)) return true;
  return TRUE_CIRCLE_SHAPE_LABELS.has(s.replace(/\s+/g, " ").toUpperCase());
}
