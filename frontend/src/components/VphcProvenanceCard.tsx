// Read-only provenance for a contribution produced by the VPHC ingest.
//
// The catalog fields above this block are what the ingest concluded; this is
// how much of that was guesswork. Nothing here is applied to the marking --
// it exists so a reviewer can tell a marking read cleanly off the sheet from
// one whose century was inferred and whose county could not be resolved.

import { describeVphcUnmatchedReason, type VphcProvenance } from "@/lib/vphcProvenance";

function Row({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-foreground break-all">{value}</dd>
    </>
  );
}

export function VphcProvenanceCard({ provenance }: { provenance: VphcProvenance }) {
  const { code, cancelNo, county, source, unmatchedReason, flags } = provenance;
  const hasIdentity = !!(code || cancelNo || county || source);
  if (!hasIdentity && !unmatchedReason && flags.length === 0) return null;

  return (
    <div
      data-testid="vphc-provenance"
      className="mt-4 rounded-md border border-border bg-muted/40 px-3 py-2"
    >
      <p className="text-xs uppercase tracking-wide text-muted-foreground">Catalog source</p>
      {hasIdentity ? (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
          <Row label="Reference" value={code} />
          <Row label="Cancel no." value={cancelNo} />
          <Row label="County" value={county} />
          <Row label="Sheet cell" value={source} />
        </dl>
      ) : null}
      {unmatchedReason ? (
        <p className="mt-2 text-sm text-muted-foreground">
          {describeVphcUnmatchedReason(unmatchedReason)}
        </p>
      ) : null}
      {flags.length > 0 ? (
        <div className="mt-3">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Needs checking</p>
          <ul className="mt-1 space-y-1">
            {flags.map((flag) => (
              <li key={flag.code} className="flex flex-wrap items-baseline gap-x-2">
                <span
                  className={
                    flag.severity === "uncertain"
                      ? "rounded border border-amber-600/40 bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:text-amber-300"
                      : "rounded border border-border bg-background px-1.5 py-0.5 text-xs font-medium text-muted-foreground"
                  }
                >
                  {flag.label}
                </span>
                <span className="text-xs text-muted-foreground">{flag.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default VphcProvenanceCard;
