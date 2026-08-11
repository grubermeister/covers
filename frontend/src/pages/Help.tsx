import { useEffect, useMemo, useState } from "react";
import { marked } from "marked";
import { Search } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";

type HelpDoc = {
  slug: string;
  title: string;
  sourceFile: string;
  markdown: string;
};

type DocCategory = "guide" | "glossary" | "faq" | "other";

const getDocNavLabel = (doc: HelpDoc): string => {
  if (doc.sourceFile) {
    const fileName = doc.sourceFile.split("/").pop() ?? doc.sourceFile;
    const withoutExt = fileName.replace(/\.md$/i, "");
    return withoutExt.replace(/[_-]+/g, " ").trim();
  }

  // Fallback if backend doesn't send source_file for a document.
  return doc.slug.replace(/[-_]+/g, " ").trim();
};

const getDocCategory = (doc: HelpDoc): DocCategory => {
  const source = `${doc.sourceFile}`.toLowerCase();
  const slug = `${doc.slug}`.toLowerCase();
  const title = `${doc.title}`.toLowerCase();
  const text = `${source} ${slug} ${title}`;

  // Matched on slug/filename rather than title text, so a doc that merely
  // mentions "guide" in its heading doesn't displace the onboarding guide.
  if (source.includes("getting-started") || slug === "getting-started") return "guide";
  if (text.includes("glossary")) return "glossary";
  if (text.includes("faq") || text.includes("frequently asked")) return "faq";
  return "other";
};

const categoryLabel: Record<DocCategory, string> = {
  guide: "Getting Started",
  glossary: "Glossary",
  faq: "FAQ",
  other: "More Docs",
};

const Help = ({ singleDocSlug }: { singleDocSlug?: string }) => {
  const navigate = useNavigate();
  const { docSlug } = useParams<{ docSlug?: string }>();
  // When rendered as a single static doc (e.g. /acknowledgements), pin to
  // this slug and hide the sidebar; otherwise behave as the normal Help page.
  const effectiveSlug = singleDocSlug ?? docSlug;
  const [docs, setDocs] = useState<HelpDoc[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const fetchDocs = async () => {
      try {
        const response = await fetch("/api/v2/help-docs/");
        if (!response.ok) {
          throw new Error(`Failed to load help docs (${response.status})`);
        }
        const data = (await response.json()) as { results?: unknown[] } | unknown[];
        const rawItems = Array.isArray(data) ? data : data.results || [];
        const items: HelpDoc[] = rawItems
          .map((raw) => {
            if (!raw || typeof raw !== "object") return null;
            const item = raw as Record<string, unknown>;
            const slug = String(item.slug ?? "").trim();
            const title = String(item.title ?? "").trim();
            const sourceFile = String(item.source_file ?? "").trim();
            const markdown = String(item.markdown ?? "");
            if (!slug || !title || !markdown) return null;
            return { slug, title, sourceFile, markdown };
          })
          .filter(Boolean) as HelpDoc[];
        setDocs(items);
      } catch {
        setDocs([]);
      } finally {
        setIsLoading(false);
      }
    };

    void fetchDocs();
  }, []);

  const renderedDocs = useMemo(
    () =>
      docs.map((doc) => ({
        ...doc,
        category: getDocCategory(doc),
        html: marked.parse(doc.markdown) as string,
      })),
    [docs],
  );

  const filteredDocs = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return renderedDocs;
    return renderedDocs.filter((doc) =>
      `${doc.title} ${doc.sourceFile} ${doc.markdown}`.toLowerCase().includes(normalized),
    );
  }, [query, renderedDocs]);

  const orderedDocs = useMemo(() => {
    // The onboarding guide sorts first, which also makes it the doc a bare
    // /help lands on (see the effectiveSlug fallback to orderedDocs[0]).
    const priority: Record<DocCategory, number> = { guide: 0, glossary: 1, faq: 2, other: 3 };
    return [...filteredDocs].sort((a, b) => {
      const categoryDiff = priority[a.category] - priority[b.category];
      if (categoryDiff !== 0) return categoryDiff;
      return a.title.localeCompare(b.title);
    });
  }, [filteredDocs]);

  useEffect(() => {
    if (orderedDocs.length === 0) {
      setSelectedSlug(null);
      return;
    }

    if (effectiveSlug && orderedDocs.some((doc) => doc.slug === effectiveSlug)) {
      setSelectedSlug(effectiveSlug);
      return;
    }

    setSelectedSlug((current) => {
      if (current && orderedDocs.some((doc) => doc.slug === current)) return current;
      return orderedDocs[0].slug;
    });
  }, [effectiveSlug, orderedDocs]);

  const selectedDoc = useMemo(
    () => orderedDocs.find((doc) => doc.slug === selectedSlug) ?? null,
    [orderedDocs, selectedSlug],
  );

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />

      <main className="flex-1">
        <section className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12 md:py-16">
          <header className="mb-10">
            <h1 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-3">
              {singleDocSlug ? (selectedDoc?.title ?? "Acknowledgements") : "Help"}
            </h1>
            {!singleDocSlug && (
              <p className="text-muted-foreground">
                Read public documentation, including the system glossary and frequently asked questions.
              </p>
            )}
          </header>

          {isLoading ? (
            <div className="rounded-lg border border-border bg-card p-6 text-muted-foreground">
              Loading help documents...
            </div>
          ) : renderedDocs.length === 0 ? (
            <div className="rounded-lg border border-border bg-card p-6 text-muted-foreground">
              No help documents found.
            </div>
          ) : (
            <div className={singleDocSlug ? "" : "grid grid-cols-1 lg:grid-cols-12 gap-6 lg:gap-8"}>
              {!singleDocSlug && (
              <aside className="lg:col-span-4 xl:col-span-3">
                <div className="rounded-lg border border-border bg-card p-3 md:p-4">
                  <div className="relative mb-4">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Search docs"
                      className="w-full rounded-md border border-border bg-background pl-9 pr-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                  </div>
                  <h2 className="font-heading text-lg font-semibold text-foreground mb-3">
                    Documents
                  </h2>
                  <div className="mb-3 flex flex-wrap gap-2">
                    {(["guide", "glossary", "faq"] as DocCategory[]).map((category) => {
                      const doc = orderedDocs.find((item) => item.category === category);
                      if (!doc) return null;
                      const active = selectedSlug === doc.slug;
                      return (
                        <button
                          key={category}
                          type="button"
                          onClick={() => {
                            setSelectedSlug(doc.slug);
                            navigate(`/help/${doc.slug}`);
                          }}
                          className={`rounded-full px-3 py-1 text-xs border transition-colors ${
                            active
                              ? "bg-primary text-primary-foreground border-primary"
                              : "border-border text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          {categoryLabel[category]}
                        </button>
                      );
                    })}
                  </div>
                  <div className="space-y-1">
                    {orderedDocs.map((doc) => {
                      const isActive = doc.slug === selectedSlug;
                      return (
                        <button
                          key={doc.slug}
                          type="button"
                          onClick={() => {
                            setSelectedSlug(doc.slug);
                            navigate(`/help/${doc.slug}`);
                          }}
                          className={`w-full text-left rounded-md px-3 py-2 transition-colors break-words ${
                            isActive
                              ? "bg-primary/10 text-primary font-medium"
                              : "text-muted-foreground hover:text-foreground hover:bg-muted"
                          }`}
                        >
                          <span className="block text-sm">{getDocNavLabel(doc)}</span>
                          <span className="block text-[11px] uppercase tracking-wide opacity-70 mt-0.5">
                            {categoryLabel[doc.category]}
                          </span>
                        </button>
                      );
                    })}
                    {orderedDocs.length === 0 && (
                      <p className="text-sm text-muted-foreground px-3 py-2">
                        No documents match your search.
                      </p>
                    )}
                  </div>
                </div>
              </aside>
              )}

              <article className={`rounded-lg border border-border bg-card p-6 md:p-8 ${singleDocSlug ? "" : "lg:col-span-8 xl:col-span-9"}`}>
                {selectedDoc ? (
                  <>
                    {!singleDocSlug && (
                      <h2 className="font-heading text-2xl font-semibold text-foreground mb-4">
                        {selectedDoc.title}
                      </h2>
                    )}
                    <div className="w-full overflow-x-auto">
                      <div
                        className="prose prose-slate max-w-none dark:prose-invert break-words [&_p]:my-4 [&_p]:leading-7 [&_li]:my-1.5 [&_li]:leading-7 [&_h1]:mb-5 [&_h1]:mt-8 [&_h2]:mb-4 [&_h2]:mt-8 [&_h3]:mb-3 [&_h3]:mt-6 [&_hr]:my-8 [&_table]:block [&_table]:w-full [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:text-sm [&_table]:border [&_table]:border-border [&_table]:border-collapse [&_th]:whitespace-normal [&_th]:border [&_th]:border-border [&_th]:bg-muted/60 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_td]:whitespace-normal [&_td]:break-words [&_td]:align-top [&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_code]:break-words"
                        dangerouslySetInnerHTML={{ __html: selectedDoc.html }}
                      />
                    </div>
                  </>
                ) : (
                  <p className="text-muted-foreground">Select a document to view its content.</p>
                )}
              </article>
            </div>
          )}
        </section>
      </main>

      <Footer />
    </div>
  );
};

export default Help;
