// A ticket is exactly one EC2 API call (see ActionDetails), so unlike a real
// Terraform plan only one of add/change/destroy is ever non-zero for a given
// ticket — but the same "N to add, N to change, N to destroy" shape reads at
// a glance the way approvers already expect from IaC review tools.
const CREATE_OP = /^(Run|Create|Launch)/
const DESTROY_OP = /^(Terminate|Delete)/

export interface ResourceScopeSummary {
  toAdd: number
  toChange: number
  toDestroy: number
  resourceType: string
}

export function summarizeResourceScope(actionDetails: {
  service: string
  operation: string
  parameters: Record<string, unknown>
  resourceArns: string[]
}): ResourceScopeSummary {
  const resourceType = actionDetails.service.toUpperCase()
  const { operation, parameters, resourceArns } = actionDetails

  if (CREATE_OP.test(operation)) {
    const count = [parameters.MaxCount, parameters.MinCount].find(
      (v): v is number => typeof v === "number",
    )
    return { toAdd: count ?? 1, toChange: 0, toDestroy: 0, resourceType }
  }

  const count = resourceArns.length || 1
  if (DESTROY_OP.test(operation)) {
    return { toAdd: 0, toChange: 0, toDestroy: count, resourceType }
  }
  return { toAdd: 0, toChange: count, toDestroy: 0, resourceType }
}

export function formatResourceScopeSummary(s: ResourceScopeSummary): string {
  return (
    `${s.toAdd} to add (${s.toAdd} x ${s.resourceType}), ` +
    `${s.toChange} to change, ${s.toDestroy} to destroy`
  )
}
