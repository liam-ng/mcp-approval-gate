import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { DataTable } from "@/components/tickets/ticket-table"
import { ticketColumns } from "@/components/tickets/ticket-columns"
import { api } from "@/lib/api"
import type { TicketStatus } from "@/lib/types"

const SUMMARY: { status: TicketStatus; label: string; accent: string }[] = [
  { status: "PENDING_APPROVAL", label: "Pending approval", accent: "text-warning" },
  { status: "APPROVED", label: "Approved", accent: "text-success" },
  { status: "EXECUTING", label: "Executing", accent: "text-info" },
  { status: "CLOSED", label: "Closed", accent: "text-primary" },
]

export default function Dashboard() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ["tickets", "all"],
    queryFn: () => api.listTickets({ limit: 100 }),
    refetchInterval: 15_000,
  })

  const counts = new Map<TicketStatus, number>()
  for (const t of data?.items ?? []) counts.set(t.status, (counts.get(t.status) ?? 0) + 1)
  const recent = (data?.items ?? []).slice(0, 8)

  return (
    <div className="space-y-6">
      <h1 className="section-title">Dashboard</h1>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {SUMMARY.map(({ status, label, accent }) => (
          <Card key={status}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <Skeleton className="h-8 w-12" />
              ) : (
                <span className={`text-3xl font-bold ${accent}`}>{counts.get(status) ?? 0}</span>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Recent change requests</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={ticketColumns}
            data={recent}
            onRowClick={(t) => navigate(`/tickets/${t.ticketId}`)}
          />
        </CardContent>
      </Card>
    </div>
  )
}
