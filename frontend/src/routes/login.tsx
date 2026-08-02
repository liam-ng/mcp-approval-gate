import { ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

export default function Login() {
  return (
    <div className="flex h-screen items-center justify-center bg-gradient-to-br from-background to-muted/50">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <ShieldCheck className="mb-2 h-10 w-10 text-secondary" />
          <CardTitle className="text-primary">MCP Approval Gate</CardTitle>
          <CardDescription>
            Review and approve change requests proposed by the AWS MCP agent.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button className="w-full" asChild>
            <a href="/api/auth/login">Sign in with SSO</a>
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
