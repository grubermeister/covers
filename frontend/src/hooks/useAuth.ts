import { useState, useEffect } from "react";
import { getStoredUser, setStoredUser, clearStoredUser, fetchCurrentUser, type AuthUser } from "@/lib/auth";

export function useAuth(): AuthUser | null {
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser());

  // Sync with server on load so role and assigned_collections are correct (e.g. Editor with Collections)
  useEffect(() => {
    const stored = getStoredUser();
    if (!stored) return;
    let cancelled = false;
    fetchCurrentUser().then((serverUser) => {
      if (cancelled) return;
      const current = getStoredUser();
      if (!current || current.id !== stored.id) return;
      if (serverUser) {
        setStoredUser(serverUser);
      } else {
        clearStoredUser();
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleChange = (e: CustomEvent<AuthUser | null>) => {
      setUser(e.detail);
    };
    const handleStorage = (e: StorageEvent) => {
      if (e.key === "worldcovers_user") {
        setUser(getStoredUser());
      }
    };

    window.addEventListener("auth-change", handleChange as EventListener);
    window.addEventListener("storage", handleStorage);

    return () => {
      window.removeEventListener("auth-change", handleChange as EventListener);
      window.removeEventListener("storage", handleStorage);
    };
  }, []);

  return user;
}
