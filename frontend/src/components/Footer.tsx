import { Link } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";

export const Footer = () => {
  const user = useAuth();
  return (
    <footer className="bg-card border-t border-border mt-auto">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          <div>
            <h3 className="font-heading text-lg font-semibold mb-3">About WorldCovers</h3>
            <p className="text-sm text-muted-foreground leading-relaxed">
              A nonprofit, open-access philatelic database cataloging historical American postal markings and covers for researchers and collectors worldwide.
            </p>
          </div>
          <div>
            <h3 className="font-heading text-lg font-semibold mb-3">Quick links</h3>
            <ul className="space-y-2 text-sm text-muted-foreground">
              <li>
                <Link to="/" className="hover:text-foreground transition-colors">
                  Home
                </Link>
              </li>
              <li>
                <Link to="/search" className="hover:text-foreground transition-colors">
                  Catalog
                </Link>
              </li>
              <li>
                <Link to="/acknowledgements" className="hover:text-foreground transition-colors">
                  Acknowledgements
                </Link>
              </li>
              {user && (
                <li>
                <Link to="/contribute" className="hover:text-foreground transition-colors">
                    Contribute
                  </Link>
                </li>
              )}
            </ul>
          </div>
          <div>
            <h3 className="font-heading text-lg font-semibold mb-3">Contact</h3>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Questions or feedback?<br />
              <a href="mailto:info@worldcovers.org" className="text-primary hover:underline">
                info@worldcovers.org
              </a>
            </p>
          </div>
        </div>
        <div className="mt-8 pt-6 border-t border-border text-center text-sm text-muted-foreground">
          © {new Date().getFullYear()} WorldCovers. All catalog data is freely available under CC BY 4.0.
        </div>
        <div className="pt-2 text-center text-xs text-muted-foreground/60">
          v{__APP_VERSION__}
        </div>
      </div>
    </footer>
  );
};
