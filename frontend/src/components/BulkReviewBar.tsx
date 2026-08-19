/**
 * Bulk approve / reject for the editor review queue (issue #101).
 *
 * The VPHC ingest put 2,443 pending rows in the queue. Reviewing them one at a
 * time is not a tedious option, it is not an option -- so this exists.
 *
 * Two things shape the design and are easy to undo by accident:
 *
 * 1. "Select all matching" asks the SERVER for the id list. It is never
 *    derived from the rows on screen. That is #109's lesson: anything computed
 *    client-side from one fetched page lies at scale, and here the lie would
 *    approve rows the editor never saw.
 * 2. Approval is a ONE-WAY DOOR. It mints permanent catalog codes and
 *    consolidate_superseded_contributions deletes rows. Hence the typed
 *    confirmation above a threshold, and hence failures are surfaced per row
 *    rather than as a count.
 */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  bulkReviewContributions,
  listContributionIds,
  type BulkReviewFailure,
  type ContributionListParams,
} from "@/services/contributions";

// Above this, an editor types the count before anything happens. Approval
// cannot be undone, so the cost of a mis-click scales with the batch.
export const CONFIRM_THRESHOLD = 100;

export interface BulkReviewBarProps {
  /** The queue's current filters. Must be the same object the list was fetched with. */
  filters: ContributionListParams;
  /** Ids ticked on the current page. */
  selectedIds: number[];
  onSelectionChange: (ids: number[]) => void;
  /** Total rows matching `filters`, as reported by the list endpoint. */
  matchCount: number;
  /** Called after a run so the caller can refetch. */
  onCompleted: () => void;
}

type Phase = "idle" | "confirming" | "running" | "done";

export function BulkReviewBar({
  filters,
  selectedIds,
  onSelectionChange,
  matchCount,
  onCompleted,
}: BulkReviewBarProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [action, setAction] = useState<"approve" | "reject">("approve");
  const [targetIds, setTargetIds] = useState<number[] | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [failures, setFailures] = useState<BulkReviewFailure[]>([]);
  const [succeededCount, setSucceededCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // A filter change invalidates any selection: the ids were chosen against a
  // different match set, and silently carrying them forward is how an editor
  // approves something they have scrolled away from.
  const filterKey = JSON.stringify(filters);
  useEffect(() => {
    if (phase === "running") return;
    onSelectionChange([]);
    setTargetIds(null);
    setPhase("idle");
    setFailures([]);
    setError(null);
    // onSelectionChange is the caller's setState; depending on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const selectAllMatching = async () => {
    setError(null);
    try {
      const { ids } = await listContributionIds(filters);
      onSelectionChange(ids);
      setTargetIds(ids);
    } catch {
      setError("Could not load the full match set. Nothing was changed.");
    }
  };

  const begin = (next: "approve" | "reject") => {
    const ids = targetIds ?? selectedIds;
    if (!ids.length) return;
    setAction(next);
    setFailures([]);
    setError(null);
    setConfirmText("");
    setPhase(ids.length >= CONFIRM_THRESHOLD ? "confirming" : "running");
    if (ids.length < CONFIRM_THRESHOLD) void run(next, ids);
  };

  const run = async (which: "approve" | "reject", ids: number[]) => {
    setPhase("running");
    setProgress({ done: 0, total: ids.length });
    const controller = new AbortController();
    abortRef.current = controller;
    const result = await bulkReviewContributions(which, ids, {
      onProgress: (done, total) => setProgress({ done, total }),
      signal: controller.signal,
    });
    abortRef.current = null;
    setSucceededCount(result.succeeded.length);
    setFailures(result.failed);
    setPhase("done");
    onSelectionChange([]);
    setTargetIds(null);
    onCompleted();
  };

  const ids = targetIds ?? selectedIds;
  const verb = action === "approve" ? "Approve" : "Reject";

  if (phase === "running") {
    const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-4 mb-4" role="status">
        <div className="flex items-center gap-3 mb-2">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          <span className="text-sm font-medium">
            {verb === "Approve" ? "Approving" : "Rejecting"} {progress.done} of{" "}
            {progress.total}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => abortRef.current?.abort()}
          >
            Stop
          </Button>
        </div>
        <div className="h-2 w-full rounded bg-border overflow-hidden">
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          Rows already processed are saved. Stopping leaves the rest untouched.
        </p>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div className="rounded-lg border border-border bg-card p-4 mb-4">
        <div className="flex items-center gap-2 mb-2">
          <Check className="h-4 w-4 text-green-600" aria-hidden />
          <span className="text-sm font-medium">
            {succeededCount} {action === "approve" ? "approved" : "rejected"}
            {failures.length > 0 && `, ${failures.length} failed`}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => setPhase("idle")}
          >
            <X className="h-4 w-4" aria-hidden />
            <span className="sr-only">Dismiss</span>
          </Button>
        </div>
        {failures.length > 0 && (
          <div className="mt-2">
            {/* Listed, not counted: "12 failed" gives an editor nothing to act
                on across a 2,443-row run. */}
            <ul className="text-xs text-muted-foreground max-h-40 overflow-y-auto space-y-1">
              {failures.map((f) => (
                <li key={f.id}>
                  <span className="font-mono">#{f.id}</span> — {f.reason}
                </li>
              ))}
            </ul>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => {
                const retry = failures.map((f) => f.id);
                setFailures([]);
                void run(action, retry);
              }}
            >
              Retry {failures.length} failed
            </Button>
          </div>
        )}
      </div>
    );
  }

  if (phase === "confirming") {
    const expected = String(ids.length);
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 mb-4">
        <div className="flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-600 mt-0.5" aria-hidden />
          <div className="flex-1">
            <p className="text-sm font-medium">
              {verb} {ids.length} submissions?
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {action === "approve"
                ? "Approving publishes these to the catalog and assigns permanent catalog numbers. It cannot be undone."
                : "This marks all of them rejected."}{" "}
              Type <span className="font-mono font-medium">{expected}</span> to confirm.
            </p>
            <div className="flex items-center gap-2 mt-2">
              <Input
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                className="w-28"
                aria-label={`Type ${expected} to confirm`}
              />
              <Button
                size="sm"
                disabled={confirmText.trim() !== expected}
                onClick={() => void run(action, ids)}
              >
                {verb} {ids.length}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setPhase("idle")}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!selectedIds.length) return null;

  const allMatchingSelected = targetIds !== null && targetIds.length === matchCount;

  return (
    <div className="rounded-lg border border-border bg-muted/40 p-3 mb-4 flex flex-wrap items-center gap-3">
      <span className="text-sm font-medium">{ids.length} selected</span>

      {/* "on this page" and "all matching" are deliberately distinct (#101
          AC-2). Before #115 the client had no notion of a match set beyond the
          page it held, so this offer could not have been made truthfully. */}
      {!allMatchingSelected && matchCount > selectedIds.length && (
        <Button variant="link" size="sm" className="h-auto p-0" onClick={selectAllMatching}>
          Select all {matchCount} matching
        </Button>
      )}
      {allMatchingSelected && (
        <span className="text-xs text-muted-foreground">
          All {matchCount} matching rows selected
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        <Button size="sm" onClick={() => begin("approve")}>
          Approve
        </Button>
        <Button size="sm" variant="outline" onClick={() => begin("reject")}>
          Reject
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            onSelectionChange([]);
            setTargetIds(null);
          }}
        >
          Clear
        </Button>
      </div>

      {error && <p className="w-full text-xs text-destructive">{error}</p>}
    </div>
  );
}

export default BulkReviewBar;
