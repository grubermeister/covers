import { History, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { MarkingChangelogEvent } from "@/services/markings";

const HISTORY_COLLAPSED_LIMIT = 3;
const HISTORY_EXPANDED_LIMIT = 10;
// Cap the per-event field-change list so a large edit does not flood the card.
const DIFF_FIELD_LIMIT = 6;

function humanizeFieldKey(key: string): string {
  return key.replace(/_/g, " ");
}

function formatDiffValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "(empty)";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function formatHistoryTimestamp(raw: string | null | undefined): string {
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function historyActorDisplay(event: MarkingChangelogEvent): string {
  const email = (event.actor_email ?? "").trim();
  if (email) return email;
  const actor = (event.actor ?? "").trim();
  if (actor) return actor;
  return "system";
}

export function EntryRecordHistoryCard({
  loading,
  error,
  events,
  expanded,
  onToggleExpanded,
  unavailableMessage,
}: {
  loading: boolean;
  error: string | null;
  events: MarkingChangelogEvent[];
  expanded: boolean;
  onToggleExpanded: () => void;
  unavailableMessage?: string;
}) {
  const visibleEvents = expanded
    ? events.slice(0, HISTORY_EXPANDED_LIMIT)
    : events.slice(0, HISTORY_COLLAPSED_LIMIT);
  const hasMoreHistory = events.length > HISTORY_COLLAPSED_LIMIT;
  const historyOverflow = Math.max(0, events.length - HISTORY_EXPANDED_LIMIT);

  return (
    <Card className="shadow-archival-md">
      <CardHeader>
        <CardTitle className="font-heading text-lg flex items-center gap-2">
          <History className="h-5 w-5 text-muted-foreground" />
          Record History
        </CardTitle>
      </CardHeader>
      <CardContent>
        {unavailableMessage ? (
          <p className="text-sm text-muted-foreground">{unavailableMessage}</p>
        ) : loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading history...
          </div>
        ) : error ? (
          <p className="text-sm text-muted-foreground">{error}</p>
        ) : events.length === 0 ? (
          <p className="text-sm text-muted-foreground">No audit events recorded for this marking yet.</p>
        ) : (
          <>
            <ul className="divide-y divide-border text-sm">
              {visibleEvents.map((event) => (
                <li key={event.event_id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium text-foreground">
                      {event.action_label || event.action}
                    </span>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {formatHistoryTimestamp(event.timestamp)}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground break-all">
                    {historyActorDisplay(event)}
                  </div>
                  {/* Field-level changes. Gate on version_no > 1 so the initial
                      create approval (version 1, before == empty) does not list
                      every field; reject/return events carry empty diffs. */}
                  {event.diff.length > 0 && event.version_no != null && event.version_no > 1 && (
                    <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                      {event.diff.slice(0, DIFF_FIELD_LIMIT).map((change) => (
                        <li key={change.field} className="break-all">
                          <span className="font-medium text-foreground">{humanizeFieldKey(change.field)}</span>
                          {`: ${formatDiffValue(change.before)} -> ${formatDiffValue(change.after)}`}
                        </li>
                      ))}
                      {event.diff.length > DIFF_FIELD_LIMIT && (
                        <li className="italic">+{event.diff.length - DIFF_FIELD_LIMIT} more</li>
                      )}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
            {hasMoreHistory && (
              <div className="mt-3 flex items-center justify-between gap-3">
                <Button type="button" variant="ghost" size="sm" onClick={onToggleExpanded}>
                  {expanded
                    ? `Show latest ${HISTORY_COLLAPSED_LIMIT}`
                    : `Show recent history (up to ${HISTORY_EXPANDED_LIMIT})`}
                </Button>
                {expanded && historyOverflow > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {historyOverflow} older event{historyOverflow === 1 ? "" : "s"} not shown
                  </span>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
