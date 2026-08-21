import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { fetchBrands } from "../api/brands";

interface BrandContextType {
  selectedBrand: string;
  /** Switch brands: persist, then hard-reload so nothing fetched under the
   *  previous brand survives on screen. */
  setSelectedBrand: (brand: string) => void;
  /**
   * Align the context with a brand the app is ALREADY acting as — a
   * brand-scoped page that claimed an empty selection for itself, so that the
   * `X-Brand` header and the sidebar selector name the same brand.
   *
   * Deliberately does NOT reload, which is the whole difference from
   * `setSelectedBrand`: nothing on screen was fetched under a different brand,
   * so there is no stale state to clear — and a reload fired from a mount-time
   * adoption would bounce the page on every fresh browser.
   */
  adoptBrand: (brand: string) => void;
  availableBrands: string[];
}

const BrandContext = createContext<BrandContextType | undefined>(undefined);

const BRAND_STORAGE_KEY = "social_automation_selected_brand";

// Safety net: used until GET /brands resolves, and kept as the permanent
// fallback if that fetch errors or returns zero brands (e.g. backend not
// deployed yet) — preserves today's single-brand behavior either way.
const FALLBACK_BRAND = "persona";

export function BrandProvider({ children }: { children: React.ReactNode }) {
  const [availableBrands, setAvailableBrands] = useState<string[]>([FALLBACK_BRAND]);
  const [selectedBrand, setSelectedBrandState] = useState<string>(() => {
    return localStorage.getItem(BRAND_STORAGE_KEY) || FALLBACK_BRAND;
  });

  useEffect(() => {
    let cancelled = false;

    fetchBrands()
      .then((res) => {
        if (cancelled) return;
        const ids = res.brands.map((b) => b.id);
        if (ids.length === 0) {
          setAvailableBrands([FALLBACK_BRAND]);
          return;
        }
        setAvailableBrands(ids);
        setSelectedBrandState((current) => (ids.includes(current) ? current : ids[0]));
      })
      .catch(() => {
        if (cancelled) return;
        setAvailableBrands([FALLBACK_BRAND]);
        setSelectedBrandState((current) => current || FALLBACK_BRAND);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const setSelectedBrand = (brand: string) => {
    setSelectedBrandState(brand);
    localStorage.setItem(BRAND_STORAGE_KEY, brand);
    // When brand changes, we might want to reload the window to reset all state
    // or trigger a global refetch. For now, we just update the context and storage.
    window.location.reload(); // Hard reload to clear all react-query/state and re-init client
  };

  // Stable identity: callers adopt from an effect, and a fresh function every
  // render would re-fire that effect on every render.
  const adoptBrand = useCallback((brand: string) => {
    if (!brand) return;
    localStorage.setItem(BRAND_STORAGE_KEY, brand);
    setSelectedBrandState((current) => (current === brand ? current : brand));
  }, []);

  return (
    <BrandContext.Provider
      value={{
        selectedBrand,
        setSelectedBrand,
        adoptBrand,
        availableBrands,
      }}
    >
      {children}
    </BrandContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useBrand() {
  const context = useContext(BrandContext);
  if (context === undefined) {
    throw new Error("useBrand must be used within a BrandProvider");
  }
  return context;
}
