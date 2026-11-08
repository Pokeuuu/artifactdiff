import { FileQuestion } from 'lucide-react'
import { Link } from 'react-router-dom'
import { EmptyState } from '@/components/common'

export function NotFound() {
  return (
    <EmptyState icon={FileQuestion} title="Page not found">
      <Link to="/" className="text-accent hover:underline">
        Back to the dashboard
      </Link>
    </EmptyState>
  )
}
