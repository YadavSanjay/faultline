import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { router } from "@/app/routes";
import { LensProvider } from "@/lib/lens";
import { ThemeProvider } from "@/lib/theme";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <LensProvider>
          <RouterProvider router={router} />
        </LensProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
