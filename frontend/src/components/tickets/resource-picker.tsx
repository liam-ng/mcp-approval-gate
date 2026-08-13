// One parameter input, upgraded to a dropdown of real resources when the gate
// can see the account.
//
// The fallback is not an error state — it is the default. AWS_DISCOVERY_ENABLED
// is off unless someone deliberately gave the gate a Describe-only role, so
// most deployments render the plain text input and that is fine. The picker
// never *replaces* free entry either: an id that discovery didn't return (a
// shared AMI, a subnet in another account) must still be typeable, or the form
// becomes narrower than the API it drives.
import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api"
import type { DiscoveryKind } from "@/lib/types"

/** Which parameter names map onto which lookup. Names not here stay text inputs. */
const KIND_BY_PARAMETER: Record<string, DiscoveryKind> = {
  ImageId: "images",
  SubnetId: "subnets",
  VpcId: "vpcs",
  GroupId: "security-groups",
  KeyName: "key-pairs",
  InstanceId: "instances",
  VolumeId: "volumes",
}

export function discoverableKind(parameter: string): DiscoveryKind | null {
  return KIND_BY_PARAMETER[parameter] ?? null
}

export function ResourcePicker({
  id,
  parameter,
  region,
  value,
  onChange,
}: {
  id: string
  parameter: string
  region: string
  value: string
  onChange: (value: string) => void
}) {
  const kind = discoverableKind(parameter)

  // `resolve:ssm:/aws/service/...` is resolved by RunInstances at execution
  // time, so a ticket carrying the alias has an approved parametersHash that
  // pins the alias string and not the image — AWS can publish a new AL2023
  // between approval and execution. Offering the concrete id here closes that.
  const isAlias = parameter === "ImageId" && value.startsWith("resolve:ssm:")
  const resolved = useQuery({
    queryKey: ["resolve-ami", value, region],
    queryFn: () => api.resolveAmiAlias(value, region),
    enabled: isAlias && region !== "",
    staleTime: 60_000,
    retry: false,
  })
  const resolvedId = resolved.data?.items[0]?.id

  const query = useQuery({
    queryKey: ["discover", kind, region],
    queryFn: () => api.discover(kind as DiscoveryKind, region),
    enabled: kind !== null && region !== "",
    staleTime: 60_000,
    retry: false,
  })

  const options = query.data?.enabled ? query.data.items : []

  return (
    <div className="space-y-1">
      <div className="flex gap-2">
        <Input
          id={id}
          className="font-mono text-xs"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={kind ? "type an id, or pick one" : ""}
        />
        {options.length > 0 && (
          <Select value="" onValueChange={onChange}>
            <SelectTrigger className="w-48 shrink-0">
              <SelectValue placeholder="Pick…" />
            </SelectTrigger>
            <SelectContent>
              {options.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  <span className="font-medium">{item.label}</span>
                  {item.detail && (
                    <span className="ml-2 text-xs text-muted-foreground">{item.detail}</span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      {kind && query.isFetching && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> Loading {kind}…
        </p>
      )}
      {/* Deliberately quiet when discovery is simply off — that is the normal
          configuration, not a problem the person filling in the form caused. */}
      {kind && query.data?.enabled && query.data.error && (
        <p className="text-xs text-muted-foreground">
          Could not list {kind} ({query.data.error}) — enter the id directly.
        </p>
      )}
      {isAlias && resolvedId && (
        <p className="text-xs text-muted-foreground">
          Resolves today to <code className="font-mono">{resolvedId}</code> —{" "}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => onChange(resolvedId)}
          >
            use that instead
          </button>
          , so the approver reviews the image that will actually launch.
        </p>
      )}
    </div>
  )
}
