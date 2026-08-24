/**
 * One town: which state and county it sat in, and who ran its post office.
 *
 * Towns previously appeared only as a search filter and as an unlinked line on
 * a marking record. The postmaster data has no other home, so this page is it.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import apiClient from "@/lib/api";
import {
  PostmasterTenure,
  getTenuresForPostOffice,
  tenureDateLabel,
  tenureEventLabel,
} from "@/services/postmasters";

interface PostOfficeDetailData {
  id: number;
  name: string;
  code: string | null;
  population: number | null;
  regionName: string;
  regionAbbrev: string;
}

const PostOfficeDetail = () => {
  const { id } = useParams<{ id: string }>();
  const postOfficeId = Number(id);

  const [office, setOffice] = useState<PostOfficeDetailData | null>(null);
  const [tenures, setTenures] = useState<PostmasterTenure[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isFinite(postOfficeId) || postOfficeId <= 0) {
      setError("That is not a valid post office.");
      setLoading(false);
      return;
    }
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiClient.get(`/post-offices/${postOfficeId}/`);
        const raw = res.data ?? {};
        const detail: PostOfficeDetailData = {
          id: raw.id,
          name: raw.name ?? "",
          code: raw.code ?? null,
          population: raw.population ?? null,
          regionName: raw.region_name ?? raw.regionName ?? "",
          regionAbbrev: raw.region_abbrev ?? raw.regionAbbrev ?? "",
        };
        const rows = await getTenuresForPostOffice(postOfficeId);
        if (cancelled) return;
        setOffice(detail);
        setTenures(rows);
      } catch {
        if (!cancelled) setError("That post office could not be loaded.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [postOfficeId]);

  // Appointments read as a succession; undated events sort to the end rather
  // than heading the list.
  const ordered = useMemo(
    () =>
      [...tenures].sort((a, b) => {
        if (a.dateAppointed && b.dateAppointed) {
          return a.dateAppointed.localeCompare(b.dateAppointed);
        }
        if (a.dateAppointed) return -1;
        if (b.dateAppointed) return 1;
        return 0;
      }),
    [tenures]
  );

  const title = office?.name ? office.name : "Post office";

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />
      <main className="flex-1 container mx-auto px-4 py-8 max-w-4xl">
        {loading && <p className="text-muted-foreground">Loading…</p>}
        {error && !loading && <p className="text-destructive">{error}</p>}

        {!loading && !error && office && (
          <>
            <h1 className="text-3xl font-semibold mb-6">{title}</h1>

            <div className="grid gap-6 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Post office</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {office.regionName && (
                    <p>
                      <span className="text-muted-foreground">State: </span>
                      <Link
                        className="underline"
                        to={`/search?state=${encodeURIComponent(office.regionAbbrev || office.regionName)}`}
                      >
                        {office.regionName}
                      </Link>
                    </p>
                  )}
                  {office.population != null && (
                    <p>
                      <span className="text-muted-foreground">
                        Population recorded:{" "}
                      </span>
                      {office.population.toLocaleString()}
                    </p>
                  )}
                  {office.code && (
                    <p>
                      <span className="text-muted-foreground">Reference: </span>
                      {office.code}
                    </p>
                  )}
                  <p>
                    <Link
                      className="underline"
                      to={`/search?town=${encodeURIComponent(office.name)}`}
                    >
                      Markings from this town
                    </Link>
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Postmasters</CardTitle>
                </CardHeader>
                <CardContent>
                  {ordered.length === 0 && (
                    <p className="text-sm text-muted-foreground">
                      No postmaster records for this town yet.
                    </p>
                  )}
                  {ordered.length > 0 && (
                    <ol className="space-y-2 text-sm">
                      {ordered.map((tenure) => (
                        <li key={tenure.id} className="flex gap-3">
                          <span className="text-muted-foreground tabular-nums whitespace-nowrap">
                            {tenureDateLabel(tenure)}
                          </span>
                          <span>
                            {tenure.postmasterName || tenureEventLabel(tenure.event)}
                            {tenure.postmasterName &&
                              tenure.event !== "appointment" && (
                                <span className="text-muted-foreground">
                                  {" "}
                                  — {tenureEventLabel(tenure.event)}
                                </span>
                              )}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </main>
      <Footer />
    </div>
  );
};

export default PostOfficeDetail;
