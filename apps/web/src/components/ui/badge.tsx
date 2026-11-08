import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px] font-medium leading-4 whitespace-nowrap [&_svg]:size-3',
  {
    variants: {
      tone: {
        neutral: 'border-line bg-panel-2 text-fg-2',
        accent: 'border-accent/30 bg-accent-soft text-accent',
        add: 'border-add/30 bg-add/10 text-add',
        remove: 'border-remove/30 bg-remove/10 text-remove',
        modify: 'border-modify/30 bg-modify/10 text-modify',
        info: 'border-line bg-transparent text-fg-3',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}
