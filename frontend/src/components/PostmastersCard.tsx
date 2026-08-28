// The postmasters who served the office this marking was struck at (#125).
//
// The town page lists every appointment event in full; this card answers the
// narrower question a catalogue reader actually has in front of a marking --
// who was running this office at the time -- so it opens centred on the term
// containing the marking's own date and shows the neighbours either side.
//
// Every end date here is inferred from the next record for the office and is
// not asserted by any source. That is stated on the card, not just in the
// code: see lib/postmasterSpans.ts.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buildPostmasterSpans } from "@/lib/postmasterSpans";
import type { PostmasterTenure } from "@/services/postmasters";

/** Rows shown collapsed: the focused term plus this many either side. */
const NEIGHBOURS = 2;
const COLLAPSED_LIMIT = NEIGHBOURS * 2 + 1;

export function PostmastersCard({
  tenures,
  postOfficeId,
  focusYear,
}: {
  tenures: PostmasterTenure[];
  postOfficeId: number | null;
  focusYear?: number | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const rows = useMemo(
    () => buildPostmasterSpans(tenures, focusYear),
    [tenures, focusYear],
  );

  // Nothing to say. Outside Virginia and West Virginia this is every marking,
  // so the card must vanish rather than render an empty shell.
  if (rows.length === 0) return null;

  const focusIndex = rows.findIndex((row) => row.isFocus);
  // Without a focus term, fall back to the earliest rows.
  const windowStart =
    focusIndex < 0
      ? 0
      : Math.min(
          Math.max(0, focusIndex - NEIGHBOURS),
          Math.max(0, rows.length - COLLAPSED_LIMIT),
        );
  const visible = expanded
    ? rows
    : rows.slice(windowStart, windowStart + COLLAPSED_LIMIT);
  const hidden = rows.length - visible.length;
  const earlier = expanded ? 0 : windowStart;
  const later = expanded ? 0 : rows.length - (windowStart + visible.length);
  // People, not entries. An undated row is still a postmaster -- we just do
  // not know when he served -- while an `event` row is the office opening or
  // closing and is nobody.
  const postmasterCount = rows.filter(
    (row) => row.kind === "tenure" || row.kind === "undated",
  ).length;

  return (
    <Card className="shadow-archival-md">
      <CardHeader>
        <CardTitle className="font-heading text-lg">
          Postmasters ({postmasterCount})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {earlier > 0 && (
          <p className="mb-2 text-xs text-muted-foreground">
            {earlier} earlier {earlier === 1 ? "postmaster" : "postmasters"}
          </p>
        )}
        <ol className="space-y-2 text-sm">
          {visible.map((row) => (
            <li
              key={row.key}
              title={row.precise}
              className={
                row.isFocus
                  ? "flex items-baseline justify-between gap-3 rounded-sm bg-muted/60 px-2 py-1 font-medium"
                  : "flex items-baseline justify-between gap-3 px-2 py-1"
              }
            >
              <span className={row.kind === "tenure" ? "" : "text-muted-foreground"}>
                {row.label}
                {row.note && (
                  <span className="text-muted-foreground"> — {row.note}</span>
                )}
                {row.isFocus && (
                  <span className="sr-only"> — serving when this marking was struck</span>
                )}
              </span>
              <span className="text-muted-foreground tabular-nums whitespace-nowrap">
                {row.years}
                {row.isOpenEnded && (
                  <span className="sr-only"> no later appointment recorded</span>
                )}
              </span>
            </li>
          ))}
        </ol>
        {later > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            {later} later {later === 1 ? "postmaster" : "postmasters"}
          </p>
        )}
        {(hidden > 0 || expanded) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => setExpanded((v) => !v)}
          >
            {/* States what the click does, so it can never disagree with the
                heading -- which counts people, while `rows` also holds the
                office's own opening and closing events. */}
            {expanded ? "Show fewer" : `Show ${hidden} more`}
          </Button>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          Dates of service after each appointment are inferred from the next
          record for this office.
          {postOfficeId != null && (
            <>
              {" "}
              <Link className="underline" to={`/post-office/${postOfficeId}`}>
                Full postmaster record for this town
              </Link>
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

export default PostmastersCard;
