import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter, Outlet, Route, Routes } from "react-router"
import { Toaster } from "sonner"
import { Header } from "@/components/layout/header"
import { Sidebar } from "@/components/layout/sidebar"
import ApproveByLink from "@/routes/approve-by-link"
import Dashboard from "@/routes/dashboard"
import Login from "@/routes/login"
import TicketDetail from "@/routes/ticket-detail"
import Tickets from "@/routes/tickets"

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

// Route protection is enforced by the API (401 -> /login redirect in lib/api);
// this shell just provides the chrome.
function Shell() {
  return (
    <div className="flex h-screen bg-gradient-to-br from-background to-muted/50">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/act" element={<ApproveByLink />} />
          <Route element={<Shell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tickets" element={<Tickets />} />
            <Route path="/tickets/:id" element={<TicketDetail />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  )
}
