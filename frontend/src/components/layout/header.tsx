import { useQuery } from "@tanstack/react-query"
import { LogOut, UserRound } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"

export function Header() {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 })

  return (
    <header className="flex h-14 items-center justify-between border-b bg-card px-6">
      <div className="text-sm text-muted-foreground">
        Auditability &amp; traceability for AI-agent changes on AWS
      </div>
      <div className="flex items-center gap-3">
        {me && (
          <>
            <span className="flex items-center gap-2 text-sm">
              <UserRound className="h-4 w-4 text-muted-foreground" />
              {me.name || me.email}
            </span>
            <Badge variant={me.role === "approver" ? "success" : "outline"}>{me.role}</Badge>
          </>
        )}
        <Button variant="ghost" size="icon" asChild title="Sign out">
          <a href="/api/auth/logout">
            <LogOut className="h-4 w-4" />
          </a>
        </Button>
      </div>
    </header>
  )
}
