import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0 cursor-pointer',
  {
    variants: {
      variant: {
        default: 'bg-accent text-accent-fg hover:bg-accent/90',
        secondary: 'bg-panel-2 text-fg border border-line hover:border-line-strong',
        ghost: 'text-fg-2 hover:bg-panel-2 hover:text-fg',
        outline: 'border border-line text-fg hover:bg-panel-2',
        danger: 'bg-remove/15 text-remove border border-remove/30 hover:bg-remove/25',
      },
      size: { sm: 'h-8 px-3 text-[13px]', md: 'h-9 px-4', lg: 'h-10 px-5', icon: 'size-8' },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
