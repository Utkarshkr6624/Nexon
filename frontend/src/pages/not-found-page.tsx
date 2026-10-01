import { useLocation, useNavigate } from 'react-router-dom'
import { Compass } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ALL_NAV_ITEMS } from '@/features/modules/catalog'

export default function NotFoundPage() {
  const navigate = useNavigate()
  const location = useLocation()

  const suggestions = ALL_NAV_ITEMS.slice(0, 6)
  // Derived from the catalog so the copy can never drift from what is listed.
  const phases = [...new Set(suggestions.map((module) => module.phase))].sort((a, b) => a - b)
  const phaseRange =
    phases.length > 1 ? `Phase ${phases[0]} through Phase ${phases[phases.length - 1]}` : `Phase ${phases[0]}`

  return (
    <div className="app-container py-8 lg:py-12">
      <div className="mx-auto flex max-w-xl flex-col items-center py-16 text-center">
        <span className="flex size-11 items-center justify-center rounded-lg border border-border bg-muted text-muted-foreground">
          <Compass className="size-5" aria-hidden="true" />
        </span>
        <p className="mt-5 font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">
          Error 404
        </p>
        <h1 className="mt-2 text-xl font-semibold tracking-tight text-foreground">
          There is nothing at this address
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          <span className="font-mono text-foreground/80">{location.pathname}</span> does not match
          any destination in NEXUS. The link may be from an older build, or mistyped.
        </p>

        <div className="mt-6 flex items-center gap-2">
          <Button type="button" onClick={() => navigate('/dashboard')}>
            Back to dashboard
          </Button>
          <Button type="button" variant="outline" onClick={() => navigate(-1)}>
            Go back
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Try one of these</CardTitle>
          <CardDescription>A short list of destinations, from {phaseRange}.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2 sm:grid-cols-2">
            {suggestions.map((module) => {
              const Icon = module.icon
              return (
                <li key={module.to}>
                  <button
                    type="button"
                    onClick={() => navigate(module.to)}
                    className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm text-muted-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                  >
                    <Icon className="size-4 shrink-0" aria-hidden="true" />
                    <span className="truncate">{module.label}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}