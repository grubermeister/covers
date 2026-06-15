import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Search, BookOpen, Users, Database } from "lucide-react";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import heroImage from "@/assets/hero-cover.jpg";
import { getRegions } from "@/services/regions";
import { getPostOfficeCount } from "@/services/postOffices";
import { getMarkingCount } from "@/services/markings";
import { useMarkingYearRange } from "@/hooks/useMarkingYearRange";

type FAQItem = {
  id: string;
  question: string;
  answer: string;
};

function getSafeApiBaseUrl(): string {
  const raw =
    String(import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || "/api/v2")
      .trim()
      .replace(/\/+$/, "");

  // Relative API paths are always safe on the current origin/protocol.
  if (!/^https?:\/\//i.test(raw)) return raw || "/api/v2";

  try {
    const parsed = new URL(raw);
    // Avoid mixed content when site is loaded over HTTPS.
    if (window.location.protocol === "https:" && parsed.protocol === "http:") {
      // Same-host: keep path but use relative URL (best behind reverse proxy).
      if (parsed.host === window.location.host) {
        return parsed.pathname.replace(/\/+$/, "") || "/api/v2";
      }
      // Different host: attempt protocol upgrade.
      parsed.protocol = "https:";
      return parsed.toString().replace(/\/+$/, "");
    }
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return "/api/v2";
  }
}

function getFaqApiCandidates(): string[] {
  const candidates: string[] = [];
  candidates.push("/api/v2/faq-entries/");
  const safeBase = getSafeApiBaseUrl();
  if (safeBase) {
    candidates.push(`${safeBase}/faq-entries/`);
  }
  return candidates.filter((url, idx) => candidates.indexOf(url) === idx);
}

const Index = () => {
  const navigate = useNavigate();
  const user = useAuth();
  const [faqs, setFaqs] = useState<FAQItem[]>([]);
  const [isLoadingFaqs, setIsLoadingFaqs] = useState(false);

  useEffect(() => {
    const fetchFaqs = async () => {
      setIsLoadingFaqs(true);
      try {
        for (const endpoint of getFaqApiCandidates()) {
          const response = await fetch(endpoint);
          if (!response.ok) continue;
          const data = (await response.json()) as { results?: unknown[] } | unknown[];
          const rawItems = Array.isArray(data) ? data : data.results || [];
          const items: FAQItem[] = rawItems
            .map((raw, index: number) => {
              if (!raw || typeof raw !== "object") return null;
              const item = raw as Record<string, unknown>;
              const question = item.question ?? "";
              const answer = item.answer ?? "";
              if (!question || !answer) return null;
              return {
                id: String(item.faqEntryId ?? item.faq_entry_id ?? item.id ?? index),
                question,
                answer,
              };
            })
            .filter(Boolean) as FAQItem[];
          if (items.length > 0) {
            setFaqs(items);
            break;
          }
        }
      } catch {
        // If FAQ API fails, leave FAQs empty so the section stays hidden
      } finally {
        setIsLoadingFaqs(false);
      }
    };

    void fetchFaqs();
  }, []);

  const [stats, setStats] = useState<{
    markings: number | null;
    towns: number | null;
    states: number | null;
  }>({
    markings: null,
    towns: null,
    states: null,
  });

  const { earliestYear, latestYear } = useMarkingYearRange();

  useEffect(() => {
    let cancelled = false;

    const loadStats = async () => {
      const [markingsResult, officesResult, regionsResult] =
        await Promise.allSettled([
          getMarkingCount(),
          getPostOfficeCount(),
          getRegions(false),
        ]);

      if (cancelled) return;

      const markings =
        markingsResult.status === "fulfilled" ? markingsResult.value : null;
      const offices =
        officesResult.status === "fulfilled" ? officesResult.value : null;
      const regions =
        regionsResult.status === "fulfilled" ? regionsResult.value : [];

      setStats({
        markings: typeof markings === "number" ? markings : null,
        towns: typeof offices === "number" ? offices : null,
        states: Array.isArray(regions) ? regions.length : null,
      });
    };

    loadStats();

    return () => {
      cancelled = true;
    };
  }, []);

  const markingsStatDisplay =
    stats.markings != null ? stats.markings.toLocaleString() : "-";
  const historicalRangeDisplay = `${earliestYear}-${latestYear}`;

  const handleContributeClick = () => {
    if (user) {
      navigate('/contribute', { state: { from: '/' } });
    } else {
      navigate('/auth');
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />
      
      {/* Hero Section */}
      <section className="relative bg-gradient-to-br from-background to-secondary overflow-hidden">
        <div className="absolute inset-0 opacity-20">
          <img 
            src={heroImage} 
            alt="" 
            className="w-full h-full object-cover"
          />
        </div>
        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 md:py-32">
          <div className="max-w-3xl">
            <h1 className="font-heading text-4xl md:text-6xl font-bold text-foreground mb-6 leading-tight">
              American Postal Markings Catalogue
            </h1>
            <p className="text-lg md:text-xl text-muted-foreground mb-8 leading-relaxed">
              {stats.markings != null
                ? `Explore ${markingsStatDisplay} historical postal markings from across America. `
                : "Explore historical postal markings from across America. "}
              A comprehensive, open-access archive for researchers, collectors, and philatelic enthusiasts.
            </p>
            {!user && (
              <div className="flex flex-col sm:flex-row gap-4">
                <Button
                  size="lg"
                  className="bg-primary text-primary-foreground hover:bg-primary/90 shadow-archival-md"
                  onClick={() => navigate('/search')}
                >
                  <Search className="mr-2 h-5 w-5" />
                  Browse Catalog
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  className="border-primary text-primary hover:bg-primary/5"
                  onClick={handleContributeClick}
                >
                  <Users className="mr-2 h-5 w-5" />
                  Contribute
                </Button>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Statistics */}
      <section className="py-12 bg-card border-y border-border">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            <div className="text-center">
              <div className="text-3xl md:text-4xl font-heading font-bold text-primary mb-2">
                {markingsStatDisplay}
              </div>
              <div className="text-sm text-muted-foreground">Markings Cataloged</div>
            </div>
            <div className="text-center">
              <div className="text-3xl md:text-4xl font-heading font-bold text-primary mb-2">
                {stats.towns != null ? stats.towns.toLocaleString() : "-"}
              </div>
              <div className="text-sm text-muted-foreground">Towns Documented</div>
            </div>
            <div className="text-center">
              <div className="text-3xl md:text-4xl font-heading font-bold text-primary mb-2">
                {stats.states != null ? stats.states.toLocaleString() : "-"}
              </div>
              <div className="text-sm text-muted-foreground">States/Territories Covered</div>
            </div>
            <div className="text-center">
              <div className="text-3xl md:text-4xl font-heading font-bold text-primary mb-2">
                {historicalRangeDisplay}
              </div>
              <div className="text-sm text-muted-foreground">Historical Range</div>
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-16 md:py-24">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h2 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-4">
              Explore Historical Postal History
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Our comprehensive database provides researchers and collectors with detailed information about American postal markings from the pre-stamp era.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            <Card className="border-border shadow-archival-md hover:shadow-archival-lg transition-shadow">
              <CardContent className="pt-6">
                <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                  <Search className="h-6 w-6 text-primary" />
                </div>
                <h3 className="font-heading text-xl font-semibold mb-2">Advanced Search</h3>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  Filter by state, town, date range, marking type, color, and more. View results in list or gallery format.
                </p>
              </CardContent>
            </Card>

            <Card className="border-border shadow-archival-md hover:shadow-archival-lg transition-shadow">
              <CardContent className="pt-6">
                <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                  <Database className="h-6 w-6 text-primary" />
                </div>
                <h3 className="font-heading text-xl font-semibold mb-2">Detailed Records</h3>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  Each entry includes high-resolution images, metadata, references, valuations, and publication citations.
                </p>
              </CardContent>
            </Card>

            <Card className="border-border shadow-archival-md hover:shadow-archival-lg transition-shadow">
              <CardContent className="pt-6">
                <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mb-4">
                  <BookOpen className="h-6 w-6 text-primary" />
                </div>
                <h3 className="font-heading text-xl font-semibold mb-2">Open Access</h3>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  All catalog data is freely available under CC BY 4.0. Download records and contribute your own discoveries.
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      {!user && (
        <section className="py-16 bg-gradient-to-br from-primary/5 to-accent/5">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
            <h2 className="font-heading text-3xl md:text-4xl font-bold text-foreground mb-4">
              Help Preserve Postal History
            </h2>
            <p className="text-lg text-muted-foreground mb-8">
              Join our community of contributors and help document America's postal heritage. Your submissions help researchers and collectors worldwide.
            </p>
            <Button
              size="lg"
              className="bg-primary text-primary-foreground hover:bg-primary/90"
              onClick={handleContributeClick}
            >
              Start Contributing
            </Button>
          </div>
        </section>
      )}

      {faqs.length > 0 && (
        <section id="faq" className="py-16 md:py-24 scroll-mt-16">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12">
              <h2 className="font-heading text-3xl md:text-4xl font-semibold text-foreground mb-4">
                Frequently Asked Questions
              </h2>
              <p className="text-lg text-foreground">
                Learn more about the American Postal Markings Catalog
              </p>
            </div>

            <Accordion type="single" collapsible className="space-y-4">
              {faqs.map((faq) => (
                <AccordionItem
                  key={faq.id}
                  value={faq.id}
                  className="border border-border rounded-lg px-6 bg-card"
                >
                  <AccordionTrigger className="text-left font-semibold">
                    {faq.question}
                  </AccordionTrigger>
                  <AccordionContent className="text-foreground leading-relaxed whitespace-pre-line">
                    {faq.answer}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </div>
        </section>
      )}

      <Footer />
    </div>
  );
};

export default Index;
