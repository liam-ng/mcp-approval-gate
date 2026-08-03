// Posts a COMMENT_ADDED audit event (POST /api/tickets/:id/comments). Open to
// any authenticated IT-team session user, regardless of role or ticket status
// — comments are discussion only, not part of the approval decision.
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, Send } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError } from "@/lib/api"

export function CommentForm({ ticketId }: { ticketId: string }) {
  const [text, setText] = useState("")
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (text: string) => api.addComment(ticketId, text),
    onSuccess: () => {
      setText("")
      queryClient.invalidateQueries({ queryKey: ["ticket", ticketId] })
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Request failed"),
  })

  function submit() {
    const trimmed = text.trim()
    if (!trimmed) return
    mutation.mutate(trimmed)
  }

  return (
    <div className="space-y-2">
      <Textarea
        placeholder="Leave a comment for the team…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="min-h-20"
      />
      <div className="flex justify-end">
        <Button type="button" size="sm" onClick={submit} disabled={mutation.isPending || !text.trim()}>
          {mutation.isPending && <Loader2 className="animate-spin" />}
          <Send className="h-3.5 w-3.5" />
          Comment
        </Button>
      </div>
    </div>
  )
}
