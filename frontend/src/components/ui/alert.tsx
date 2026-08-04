import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

// Markdown-style callout block (the GitHub note/warning look): tinted
// background, accent left border, leading icon.
//
// Body text deliberately stays on the default `foreground` rather than each
// variant's `*-foreground` token — those are near-white, meant for solid
// badge/button fills (see badge.tsx), and would be unreadable on a 10% tint.
// Only the icon and title take the accent colour.
const alertVariants = cva(
  "flex w-full gap-3 rounded-lg border border-l-4 p-3 text-sm [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        info: "border-info/30 border-l-info bg-info/10 [&>svg]:text-info [&_[data-slot=alert-title]]:text-info",
        warning:
          "border-warning/30 border-l-warning bg-warning/10 [&>svg]:text-warning [&_[data-slot=alert-title]]:text-warning",
        success:
          "border-success/30 border-l-success bg-success/10 [&>svg]:text-success [&_[data-slot=alert-title]]:text-success",
        destructive:
          "border-destructive/30 border-l-destructive bg-destructive/10 [&>svg]:text-destructive [&_[data-slot=alert-title]]:text-destructive",
      },
    },
    defaultVariants: { variant: "info" },
  },
)

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  ({ className, variant, ...props }, ref) => (
    <div ref={ref} role="note" className={cn(alertVariants({ variant }), className)} {...props} />
  ),
)
Alert.displayName = "Alert"

const AlertTitle = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      data-slot="alert-title"
      className={cn("font-semibold leading-none tracking-tight", className)}
      {...props}
    />
  ),
)
AlertTitle.displayName = "AlertTitle"

const AlertDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("text-muted-foreground", className)} {...props} />
  ),
)
AlertDescription.displayName = "AlertDescription"

export { Alert, AlertTitle, AlertDescription, alertVariants }
