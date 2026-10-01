import { Search } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { getModule } from '@/features/modules/catalog'
import { usesCommandKey } from '@/hooks/use-command-palette'
import { ModulePage } from '@/pages/module-page'

export default function SearchPage() {
  const shortcut = usesCommandKey() ? '⌘K' : 'Ctrl+K'

  return (
    <ModulePage module={getModule('/search')}>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="size-4 text-muted-foreground" aria-hidden="true" />
            Cross-module search
          </CardTitle>
          <CardDescription>
            Replaces this page once the retrieval index exists in Phase 4. Until then, the{' '}
            {shortcut} palette in the top bar is the working search surface: it filters every
            destination by name, group and keyword.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Input
            disabled
            placeholder="Search projects, tasks, notes and decisions…"
            aria-label="Global search (available in Phase 4)"
          />
          <p className="mt-3 text-xs text-muted-foreground">
            Press <span className="font-mono">{shortcut}</span> to open the module palette now.
          </p>
        </CardContent>
      </Card>
    </ModulePage>
  )
}