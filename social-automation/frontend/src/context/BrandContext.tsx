import React, { createContext, useContext, useState } from "react";

interface BrandContextType {
  selectedBrand: string;
  setSelectedBrand: (brand: string) => void;
  availableBrands: string[];
}

const BrandContext = createContext<BrandContextType | undefined>(undefined);

const BRAND_STORAGE_KEY = "social_automation_selected_brand";

// Hardcoded for now. In the future, this could be fetched from an API.
const DEFAULT_BRANDS = ["dogfoodandfun"];

export function BrandProvider({ children }: { children: React.ReactNode }) {
  const [selectedBrand, setSelectedBrandState] = useState<string>(() => {
    const stored = localStorage.getItem(BRAND_STORAGE_KEY);
    return stored && DEFAULT_BRANDS.includes(stored) ? stored : DEFAULT_BRANDS[0];
  });

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
        availableBrands: DEFAULT_BRANDS,
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
