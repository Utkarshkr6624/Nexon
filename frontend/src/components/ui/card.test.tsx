import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

describe('CardTitle', () => {
  it('is a real section heading so the document outline is not empty', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Backend health</CardTitle>
          <CardDescription>Live from GET /api/v1/health</CardDescription>
        </CardHeader>
        <CardContent>content</CardContent>
      </Card>,
    )

    const heading = screen.getByRole('heading', { name: 'Backend health' })
    // Pages own the <h1>; card sections sit one level below it.
    expect(heading.tagName).toBe('H2')
    expect(heading).toHaveClass('text-base', 'font-semibold')
  })

  it('accepts an explicit level for cards nested inside another section', () => {
    render(<CardTitle level="h3">Nested</CardTitle>)
    expect(screen.getByRole('heading', { level: 3, name: 'Nested' })).toBeInTheDocument()
  })
})