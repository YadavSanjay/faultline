// The Business ⇄ Technical lens (UI_DESIGN §2/§3): a persistent app-wide mode that
// changes the default landing and which views are emphasized. Persisted to localStorage.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type Lens = "business" | "technical";

const KEY = "qlp.lens";

interface LensCtx {
  lens: Lens;
  setLens: (l: Lens) => void;
  toggle: () => void;
  landing: string; // route path for the current lens's landing
}

const Ctx = createContext<LensCtx | null>(null);

export function landingFor(lens: Lens): string {
  return lens === "business" ? "/overview" : "/ops";
}

export function LensProvider({ children }: { children: ReactNode }) {
  const [lens, setLensState] = useState<Lens>(() => {
    const stored = localStorage.getItem(KEY);
    return stored === "technical" ? "technical" : "business";
  });

  const setLens = useCallback((l: Lens) => {
    localStorage.setItem(KEY, l);
    setLensState(l);
  }, []);

  const toggle = useCallback(() => {
    setLens(lens === "business" ? "technical" : "business");
  }, [lens, setLens]);

  const value = useMemo<LensCtx>(
    () => ({ lens, setLens, toggle, landing: landingFor(lens) }),
    [lens, setLens, toggle],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLens(): LensCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLens must be used within <LensProvider>");
  return ctx;
}
