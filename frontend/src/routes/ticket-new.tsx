// Open a ticket from the portal, without an IDE agent in the loop.
//
// The parameter fields are driven by GET /api/aws/ec2/operations/{op}, which is
// botocore's own model plus the gate's conditional overlay. Nothing about AWS's
// parameter rules is hardcoded here on purpose: the server enforces them at
// creation, so a copy in this file could only ever disagree with the thing that
// actually decides.
//
// Two inputs, and free-form wins. The generated fields cover what the operation
// requires; the JSON box covers everything else (nested structures, options the
// form doesn't render) and is merged over the top, so a key present in both
// takes the JSON value. That ordering is deliberate — the escape hatch has to
// be able to override the convenience layer, or it isn't an escape hatch.
import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Plus, TriangleAlert } from "lucide-react"
import { useNavigate } from "react-router"
import { toast } from "sonner"
import { ResourcePicker } from "@/components/tickets/resource-picker"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError } from "@/lib/api"
import type { OperationSchema } from "@/lib/types"

/** botocore type name -> how the value must be sent on the wire. */
function coerce(raw: string, type: string | undefined): unknown {
  const value = raw.trim()
  if (value === "") return undefined
  switch (type) {
    case "integer":
    case "long":
      // NaN would serialize as null and produce a confusing server-side type
      // error, so a non-numeric entry is passed through as the string the user
      // typed and left for botocore to name precisely.
      return Number.isNaN(Number(value)) ? value : Number(value)
    case "float":
    case "double":
      return Number.isNaN(Number(value)) ? value : Number(value)
    case "boolean":
      return value === "true"
    case "list":
    case "structure":
    case "map":
      // Structured members are typed as JSON. Invalid JSON stays a string so
      // the server's validator reports it rather than the form guessing.
      try {
        return JSON.parse(value)
      } catch {
        return value
      }
    default:
      return value
  }
}

/** The names the form renders inputs for: required plus every conditional option. */
function fieldNames(schema: OperationSchema): string[] {
  const names = [...schema.required]
  for (const rule of schema.conditional) {
    for (const name of rule.oneOf) if (!names.includes(name)) names.push(name)
  }
  return names
}

export default function TicketNew() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [subject, setSubject] = useState("")
  const [plannedDate, setPlannedDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [plannedAction, setPlannedAction] = useState("")
  const [region, setRegion] = useState("ca-central-1")
  const [operation, setOperation] = useState("")
  const [reason, setReason] = useState("")
  const [fields, setFields] = useState<Record<string, string>>({})
  const [extraJson, setExtraJson] = useState("{}")

  const operations = useQuery({
    queryKey: ["ec2-operations"],
    queryFn: api.listOperations,
    staleTime: Infinity, // a static list baked into the server build
  })

  const schema = useQuery({
    queryKey: ["ec2-operation", operation],
    queryFn: () => api.getOperationSchema(operation),
    enabled: operation !== "",
    staleTime: Infinity,
    retry: false, // a typed-in unknown operation is a 422, not worth retrying
  })

  const extra = useMemo(() => {
    try {
      const parsed = JSON.parse(extraJson || "{}")
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null
      return parsed as Record<string, unknown>
    } catch {
      return null
    }
  }, [extraJson])

  const parameters = useMemo(() => {
    const structured: Record<string, unknown> = {}
    if (schema.data) {
      for (const name of fieldNames(schema.data)) {
        const value = coerce(fields[name] ?? "", schema.data.accepted[name])
        if (value !== undefined) structured[name] = value
      }
    }
    return { ...structured, ...(extra ?? {}) }
  }, [schema.data, fields, extra])

  const mutation = useMutation({
    mutationFn: () =>
      api.createTicket({
        subject,
        plannedDate,
        plannedAction,
        actionDetails: {
          service: "ec2",
          operation,
          region,
          parameters,
          resourceArns: [],
          reason: reason || null,
        },
        tags: {},
      }),
    onSuccess: (ticket) => {
      toast.success("Ticket created — an approver other than you must approve it")
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      navigate(`/tickets/${ticket.ticketId}`)
    },
    // The server's 422 message names the exact parameter, so show it verbatim
    // rather than a generic failure.
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Request failed"),
  })

  const canSubmit =
    subject.trim().length >= 3 &&
    plannedAction.trim().length >= 3 &&
    operation !== "" &&
    region.trim() !== "" &&
    extra !== null &&
    !mutation.isPending

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-primary">New change request</h1>
        <p className="text-sm text-muted-foreground">
          Nothing runs until someone other than you approves it.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Change</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="subject">Subject</Label>
            <Input
              id="subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Launch a build box"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="plannedDate">Planned date</Label>
              <Input
                id="plannedDate"
                type="date"
                value={plannedDate}
                onChange={(e) => setPlannedDate(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="region">Region</Label>
              <Input id="region" value={region} onChange={(e) => setRegion(e.target.value)} />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="plannedAction">What will happen</Label>
            <Textarea
              id="plannedAction"
              value={plannedAction}
              onChange={(e) => setPlannedAction(e.target.value)}
              placeholder="Launch one t3.micro in the build subnet"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="reason">Reason (optional)</Label>
            <Input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">AWS call</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Operation</Label>
            <Select
              value={operation}
              onValueChange={(value) => {
                setOperation(value)
                setFields({}) // a different operation has different parameters
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Choose an EC2 operation" />
              </SelectTrigger>
              <SelectContent>
                {(operations.data?.operations ?? []).map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {schema.isLoading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading parameters…
            </p>
          )}

          {schema.isError && (
            <p className="text-sm text-destructive">
              {schema.error instanceof ApiError ? schema.error.message : "Unknown operation"}
            </p>
          )}

          {schema.data && (
            <>
              {fieldNames(schema.data).map((name) => {
                const rule = schema.data.conditional.find((r) => r.oneOf.includes(name))
                const isRequired = schema.data.required.includes(name)
                return (
                  <div key={name} className="space-y-2">
                    <Label htmlFor={`param-${name}`}>
                      {name}{" "}
                      <span className="font-normal text-muted-foreground">
                        ({schema.data.accepted[name]})
                        {isRequired && " · required"}
                        {rule && ` · one of ${rule.oneOf.join(" or ")}`}
                      </span>
                    </Label>
                    <ResourcePicker
                      id={`param-${name}`}
                      parameter={name}
                      region={region}
                      value={fields[name] ?? ""}
                      onChange={(value) => setFields((f) => ({ ...f, [name]: value }))}
                    />
                    {rule && <p className="text-xs text-muted-foreground">{rule.because}</p>}
                  </div>
                )
              })}

              {schema.data.gateTags.length > 0 && (
                <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
                  The gate adds its own <code>TagSpecifications</code> for{" "}
                  {schema.data.gateTags.join(", ")}, carrying this ticket's id — IAM requires the
                  tag, and only the gate knows the id. You do not need to add them.
                </p>
              )}

              <div className="space-y-2">
                <Label htmlFor="extraJson">Other parameters (JSON)</Label>
                <Textarea
                  id="extraJson"
                  className="min-h-24 font-mono text-xs"
                  value={extraJson}
                  onChange={(e) => setExtraJson(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Merged over the fields above — a key set in both takes the value here.
                </p>
                {extra === null && (
                  <p className="flex items-center gap-1 text-xs text-destructive">
                    <TriangleAlert className="h-3 w-3" /> must be a JSON object
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label>What will be sent</Label>
                <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">
                  {JSON.stringify(parameters, null, 2)}
                </pre>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => navigate("/tickets")}>
          Cancel
        </Button>
        <Button disabled={!canSubmit} onClick={() => mutation.mutate()}>
          {mutation.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
          Create ticket
        </Button>
      </div>
    </div>
  )
}
