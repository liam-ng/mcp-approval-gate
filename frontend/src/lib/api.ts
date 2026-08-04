import type { ApprovalLinkPreview, Me, Ticket, TicketDetail, TicketList, TicketStatus } from "./types"

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  })
  if (res.status === 401) {
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login"
    }
    throw new ApiError(401, "UNAUTHENTICATED", "sign in required")
  }
  if (!res.ok) {
    let code = "ERROR"
    let message = res.statusText
    try {
      const body = await res.json()
      // Backend errors come as {error:{code,message}} or FastAPI {detail:...}
      const err = body.error ?? body.detail
      if (typeof err === "object" && err) {
        code = err.code ?? code
        message = err.message ?? message
      } else if (typeof err === "string") {
        message = err
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message)
  }
  return res.json() as Promise<T>
}

export const api = {
  me: () => request<Me>("/api/me"),

  listTickets: (params: { status?: TicketStatus; tag?: string; cursor?: string; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set("status", params.status)
    if (params.tag) qs.set("tag", params.tag)
    if (params.cursor) qs.set("cursor", params.cursor)
    if (params.limit) qs.set("limit", String(params.limit))
    const suffix = qs.size ? `?${qs}` : ""
    return request<TicketList>(`/api/tickets${suffix}`)
  },

  getTicket: (id: string) => request<TicketDetail>(`/api/tickets/${id}`),

  approve: (id: string) => request<Ticket>(`/api/tickets/${id}/approve`, { method: "POST" }),

  reject: (id: string, reason: string) =>
    request<Ticket>(`/api/tickets/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  supersede: (id: string, payload: unknown) =>
    request<Ticket>(`/api/tickets/${id}/supersede`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateTags: (id: string, tags: Record<string, string>) =>
    request<Ticket>(`/api/tickets/${id}/tags`, {
      method: "POST",
      body: JSON.stringify({ tags }),
    }),

  addComment: (id: string, text: string) =>
    request<Ticket>(`/api/tickets/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  close: (id: string, reason?: string) =>
    request<Ticket>(`/api/tickets/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ reason: reason || null }),
    }),

  // Signed email-link approve/reject — no session, token stands in for one.
  previewByLink: (token: string) =>
    request<ApprovalLinkPreview>(`/api/tickets/by-link/${token}`),

  actByLink: (token: string, reason?: string) =>
    request<ApprovalLinkPreview>(`/api/tickets/by-link/${token}`, {
      method: "POST",
      body: JSON.stringify({ reason: reason || null }),
    }),
}
