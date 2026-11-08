import type { InputHTMLAttributes, SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const base =
  'h-9 w-full rounded-md border border-line bg-panel-2 px-3 text-sm text-fg placeholder:text-fg-3 hover:border-line-strong focus-visible:border-accent disabled:opacity-50'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(base, className)} {...props} />
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(base, 'pr-8 cursor-pointer', className)} {...props}>
      {children}
    </select>
  )
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn('text-[12.5px] font-medium text-fg-2', className)} {...props} />
}
