import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, useLocation, Navigate } from "react-router-dom";
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";
import { Spinner } from "@/components/ui/spinner";
import Index from "./pages/Index";
import Search from "./pages/Search";
import ContributionDetail from "./pages/ContributionDetail";
import Contribute from "./pages/Contribute";
import Auth from "./pages/Auth";
import ResetPassword from "./pages/ResetPassword";
import Help from "./pages/Help";
import Collections from "./pages/Collections";
import NotFound from "./pages/NotFound";

// Lazy-loaded routes: heavier pages that are not on the common
// landing path. EntryDetail is reused across /record/:id, /covers/:coverId,
// and /record/:id/cover/:coverId -- React.lazy caches the chunk after the
// first load so the second hit is free.
const CoverEdit = lazy(() => import("./pages/CoverEdit"));
const EntryDetail = lazy(() => import("./pages/EntryDetail"));
const Dashboard = lazy(() => import("./pages/Dashboard"));

const queryClient = new QueryClient();
// Scroll to top on every route change so pages open at the top.
const ScrollToTop = ({ children }: { children: ReactNode }) => {
  const location = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [location.pathname, location.search]);
  return <>{children}</>;
};
// Protect routes that require an authenticated user.
const RequireAuth = ({ children }: { children: ReactNode }) => {
  const user = useAuth();
  const location = useLocation();
  if (!user) {
    return <Navigate to="/auth" replace state={{ from: location }} />;
  }
  return <>{children}</>;
};
// Protect routes that require a superuser (e.g. catalog edit/delete).
const RequireSuperuser = ({ children }: { children: ReactNode }) => {
  const user = useAuth();
  const location = useLocation();
  if (!user) {
    return <Navigate to="/auth" replace state={{ from: location }} />;
  }
  if (!user.is_superuser) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <ScrollToTop>
          <Suspense fallback={<Spinner fullScreen />}>
          <Routes>
            <Route path="/" element={<Index />} />
            <Route path="/search" element={<Search />} />
            <Route path="/record/:id" element={<EntryDetail />} />
            <Route path="/covers/:coverId" element={<EntryDetail />} />
            <Route
              path="/record/:id/cover/new"
              element={(
                <RequireAuth>
                  <CoverEdit />
                </RequireAuth>
              )}
            />
            <Route path="/record/:id/cover/:coverId" element={<EntryDetail />} />
            <Route
              path="/record/:id/cover/:coverId/edit"
              element={(
                <RequireAuth>
                  <CoverEdit />
                </RequireAuth>
              )}
            />
            <Route
              path="/contribution/:id"
              element={(
                <RequireAuth>
                  <ContributionDetail />
                </RequireAuth>
              )}
            />
            <Route
              path="/contribute"
              element={(
                <RequireAuth>
                  <Contribute />
                </RequireAuth>
              )}
            />
            <Route
              path="/edit/:id"
              element={(
                <RequireAuth>
                  <Contribute />
                </RequireAuth>
              )}
            />
            <Route
              path="/dashboard"
              element={(
                <RequireAuth>
                  <Dashboard />
                </RequireAuth>
              )}
            />
            <Route path="/auth" element={<Auth />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/help" element={<Help />} />
            <Route path="/help/:docSlug" element={<Help />} />
            <Route path="/acknowledgements" element={<Help singleDocSlug="acknowledgements" />} />
            <Route
              path="/admin/collections"
              element={(
                <RequireSuperuser>
                  <Collections />
                </RequireSuperuser>
              )}
            />
            {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
            <Route path="*" element={<NotFound />} />
          </Routes>
          </Suspense>
        </ScrollToTop>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
