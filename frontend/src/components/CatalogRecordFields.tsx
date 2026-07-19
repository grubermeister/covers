import type { CatalogFieldValues } from "@/lib/catalogRecordDisplay";
import type { MarkingRecord } from "@/services/markings";

/**
 * Fixed catalog fields for Catalog Search and Record Detail.
 * Always shows all labels; values use the empty marker when missing
 * (see `buildCatalogFieldValues`). Search list and gallery cards use
 * different, type-specific field sets. Dates render in a dedicated bottom
 * row so Earliest/Latest stay side-by-side.
 */
type CatalogRecordField = {
  label: string;
  value: string;
};

function hasValue(value: string): boolean {
  return value.trim() !== "" && value !== "-";
}

function truncateWithEllipsis(value: string, maxChars: number): string {
  const s = String(value ?? "").trim();
  if (!s) return s;
  if (s.length <= maxChars) return s;
  return `${s.slice(0, Math.max(0, maxChars - 3)).trimEnd()}...`;
}

function townmarkDescriptor(row: CatalogFieldValues): CatalogRecordField {
  if (hasValue(row.lettering)) {
    return { label: "Lettering Style", value: row.lettering };
  }
  if (hasValue(row.impression)) {
    return { label: "Impression", value: row.impression };
  }
  if (hasValue(row.irregular)) {
    return { label: "Irregular", value: row.irregular };
  }
  return { label: "Lettering Style", value: row.lettering };
}

function searchFields(
  row: CatalogFieldValues,
  variant: "list" | "gallery",
): CatalogRecordField[] {
  const type = { label: "Type", value: row.type };
  const manuscript = { label: "Manuscript", value: row.manuscript };
  const dimensions = { label: "Dimensions", value: row.dimensions };
  const color = { label: "Color", value: row.color };
  const isRatemark = row.type.trim().toLowerCase() === "ratemark";

  if (variant === "gallery") {
    return isRatemark
      ? [
          type,
          manuscript,
          dimensions,
          { label: "Rate Value", value: row.rateValue },
        ]
      : [type, manuscript, dimensions, color];
  }

  return isRatemark
    ? [
        type,
        manuscript,
        { label: "Shape", value: row.shape },
        { label: "Rate Value", value: row.rateValue },
        dimensions,
        color,
      ]
    : [
        type,
        manuscript,
        { label: "Shape", value: row.shape },
        townmarkDescriptor(row),
        dimensions,
        color,
      ];
}

function contributionFields(row: CatalogFieldValues): CatalogRecordField[] {
  const fields: CatalogRecordField[] = [
    { label: "Type", value: row.type },
    { label: "Manuscript", value: row.manuscript },
  ];
  const isManuscript = row.manuscript.trim().toLowerCase() === "yes";
  const type = row.type.trim().toLowerCase();
  const isRatemark = type === "ratemark";
  const isAuxmark = type === "auxmark";

  if (isManuscript || type !== "townmark") {
    const description = truncateWithEllipsis(row.desc === "-" ? "" : row.desc, 140);
    if (description) fields.push({ label: "Description", value: description });
  } else {
    fields.push(
      { label: "Shape", value: row.shape },
      { label: "Lettering style", value: row.lettering },
      { label: "Dimensions", value: row.dimensions },
    );
  }

  fields.push({ label: "Color", value: row.color });
  if (isRatemark) {
    fields.push({ label: "Rate Value", value: row.rateValue });
  } else if (isAuxmark) {
    fields.push({ label: "Shape", value: row.shape });
  }
  return fields;
}

export function CatalogRecordFields({
  row,
  variant = "list",
}: {
  row: CatalogFieldValues;
  record?: MarkingRecord;
  variant?: "list" | "gallery" | "detail" | "contribution";
}) {
  const fields =
    variant === "detail"
      ? [
          { label: "Type", value: row.type },
          { label: "Manuscript", value: row.manuscript },
          { label: "Shape", value: row.shape },
          { label: "Lettering style", value: row.lettering },
          { label: "Dimensions", value: row.dimensions },
          { label: "Color", value: row.color },
        ]
      : variant === "contribution"
        ? contributionFields(row)
      : searchFields(row, variant);

  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
      {fields.map((field) => (
        <div key={field.label} className="min-w-0">
          <span className="text-muted-foreground">{field.label}:</span>{" "}
          <span className="text-foreground break-words">{field.value}</span>
        </div>
      ))}
      <div className="min-w-0 sm:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2">
        <div className="min-w-0">
          <span className="text-muted-foreground">Earliest Seen:</span>{" "}
          <span className="text-foreground break-words">{row.earliestSeen}</span>
        </div>
        <div className="min-w-0">
          <span className="text-muted-foreground">Latest Seen:</span>{" "}
          <span className="text-foreground break-words">{row.latestSeen}</span>
        </div>
      </div>
    </dl>
  );
}
