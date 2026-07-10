import React, { createContext, useContext, useEffect, useState } from "react";
import { fetchBrands } from "../api/brands";

interface BrandContextType {
  selectedBrand: string;
  setSelectedBrand: (brand: string) => void;
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

  return (
    <BrandContext.Provider
      value={{
        selectedBrand,
        setSelectedBrand,
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
