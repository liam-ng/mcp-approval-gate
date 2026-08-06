// Trimmed from the portal's sidebar: two sections, no admin gating needed yet.
import { NavLink } from "react-router"
import { LayoutDashboard, TicketCheck, ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"

const items = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/tickets", label: "Change Requests", icon: TicketCheck, end: false },
]

export function Sidebar() {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r bg-card">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <ShieldCheck className="h-6 w-6 text-secondary" />
        <span className="font-semibold text-primary">Approval Gate</span>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {items.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t p-3 text-xs text-muted-foreground">
        AWS MCP change control
      </div>
    </aside>
  )
}
