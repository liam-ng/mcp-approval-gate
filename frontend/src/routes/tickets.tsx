import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useNavigate } from "react-router"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { DataTable } from "@/components/tickets/ticket-table"
import { ticketColumns } from "@/components/tickets/ticket-columns"
import { api } from "@/lib/api"
import { TERMINAL_STATUSES } from "@/lib/types"

type Tab = "pending" | "history"

export default function Tickets() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>("pending")
  const [tagFilter, setTagFilter] = useState("")

  const { data } = useQuery({
    queryKey: ["tickets", "all"],
    queryFn: () => api.listTickets({ limit: 100 }),
    // Poll while looking at the pending queue; history is static.
    refetchInterval: tab === "pending" ? 10_000 : false,
  })

  let items = data?.items ?? []
  items =
    tab === "pending"
      ? items.filter((t) => !TERMINAL_STATUSES.has(t.status))
      : items.filter((t) => TERMINAL_STATUSES.has(t.status))
  if (tagFilter.includes("=")) {
    const [k, v] = tagFilter.split("=", 2)
    items = items.filter((t) => t.tags[k] === v)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="section-title">Change Requests</h1>
        <Button onClick={() => navigate("/tickets/new")}>
          <Plus />
          New request
        </Button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
          <TabsList>
            <TabsTrigger value="pending">Active</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
          </TabsList>
        </Tabs>
        <Input
          placeholder="Filter by tag, e.g. owner=liam.ng"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          className="w-56"
        />
      </div>
      <DataTable
        columns={ticketColumns}
        data={items}
        onRowClick={(t) => navigate(`/tickets/${t.ticketId}`)}
      />
    </div>
  )
}
