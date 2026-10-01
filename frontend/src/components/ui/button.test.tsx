import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button } from '@/components/ui/button'

describe('Button', () => {
  it('applies the variant and size utility classes', () => {
    const { rerender } = render(<Button>Save</Button>)
    const button = screen.getByRole('button', { name: 'Save' })

    expect(button).toHaveClass('bg-primary', 'h-9', 'px-4')
    // A bare <Button> must never submit a form by accident.
    expect(button).toHaveAttribute('type', 'button')

    rerender(
      <Button variant="destructive" size="sm">
        Delete
      </Button>,
    )
    const destructive = screen.getByRole('button', { name: 'Delete' })
    expect(destructive).toHaveClass('bg-destructive', 'h-8', 'px-3')
    expect(destructive).not.toHaveClass('bg-primary')
  })

  it('renders its child instead of a <button> when asChild is set', () => {
    render(
      <Button asChild variant="ghost">
        <a href="/dashboard">Dashboard</a>
      </Button>,
    )

    const link = screen.getByRole('link', { name: 'Dashboard' })
    expect(link).toHaveClass('hover:bg-accent')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})