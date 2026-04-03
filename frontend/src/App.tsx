import ErrorBoundary from "@/components/ErrorBoundary";
import Layout from "@/components/Layout";
import Adapters from "@/pages/Adapters";
import Audit from "@/pages/Audit";
import Configurations from "@/pages/Configurations";
import Dashboard from "@/pages/Dashboard";
import Documents from "@/pages/Documents";
import Simulations from "@/pages/Simulations";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/adapters" element={<Adapters />} />
              <Route path="/documents" element={<Documents />} />
              <Route path="/configurations" element={<Configurations />} />
              <Route path="/simulations" element={<Simulations />} />
              <Route path="/audit" element={<Audit />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
