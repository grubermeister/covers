import type { CatalogFieldValues } from "@/lib/catalogRecordDisplay";
import type { MarkingRecord } from "@/services/markings";

/**
 * Fixed catalog fields for Catalog Search (list) and Record Detail.
 * Always shows all labels; values use the empty marker when missing
 * (see `buildCatalogFieldValues`). Dates render in a dedicated bottom row
 * so Earliest/Latest stay side-by-side regardless of optional field count.
 */
function truncateWithEllipsis(value: string, maxChars: number): string {
  const s = String(value ?? "").trim();
  if (!s) return s;
  if (s.length <= maxChars) return s;
  return `${s.slice(0, Math.max(0, maxChars - 3)).trimEnd()}...`;
}

export function CatalogRecordFields({
  row,
  record,
  variant = "search",
}: {
  row: CatalogFieldValues;
  record?: MarkingRecord;
  variant?: "search" | "detail";
}) {
  const isManuscript =
    record?.isManuscript === true ||
    (!record && row.manuscript.trim().toLowerCase() === "yes");
  const isNonTownmark = record
    ? record.type !== "TOWNMARK"
    : row.type.trim().toLowerCase() !== "townmark";
  const isRatemark = record
    ? record.type === "RATEMARK"
    : row.type.trim().toLowerCase() === "ratemark";
  const isAuxmark = record
    ? record.type === "AUXMARK"
    : row.type.trim().toLowerCase() === "auxmark";
  const hidePhysicalFieldsOnSearch = variant === "search" && (isManuscript || isNonTownmark);
  const descForSearch = truncateWithEllipsis(row.desc === "-" ? "" : row.desc, 140);
  const showSearchRateValue = variant === "search" && isRatemark;
  const showSearchAuxmarkShape = variant === "search" && isAuxmark;

  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
      <div className="min-w-0">
        <span className="text-muted-foreground">Type:</span>{" "}
        <span className="text-foreground break-words">{row.type}</span>
      </div>
      <div className="min-w-0">
        <span className="text-muted-foreground">Manuscript:</span>{" "}
        <span className="text-foreground break-words">{row.manuscript}</span>
      </div>

      {hidePhysicalFieldsOnSearch ? (
        descForSearch ? (
          <div className="min-w-0 sm:col-span-2">
            <span className="text-muted-foreground">Description:</span>{" "}
            <span className="text-foreground break-words">{descForSearch}</span>
          </div>
        ) : null
      ) : (
        <>
          <div className="min-w-0">
            <span className="text-muted-foreground">Shape:</span>{" "}
            <span className="text-foreground break-words">{row.shape}</span>
          </div>
          <div className="min-w-0">
            <span className="text-muted-foreground">Lettering style:</span>{" "}
            <span className="text-foreground break-words">{row.lettering}</span>
          </div>
          <div className="min-w-0">
            <span className="text-muted-foreground">Dimensions:</span>{" "}
            <span className="text-foreground break-words">{row.dimensions}</span>
          </div>
        </>
      )}

      <div className="min-w-0">
        <span className="text-muted-foreground">Color:</span>{" "}
        <span className="text-foreground break-words">{row.color}</span>
      </div>
      {showSearchRateValue ? (
        <div className="min-w-0">
          <span className="text-muted-foreground">Rate Value:</span>{" "}
          <span className="text-foreground break-words">{row.rateValue}</span>
        </div>
      ) : null}
      {showSearchAuxmarkShape ? (
        <div className="min-w-0">
          <span className="text-muted-foreground">Shape:</span>{" "}
          <span className="text-foreground break-words">{row.shape}</span>
        </div>
      ) : null}
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
