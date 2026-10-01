import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function AnalyticsPage() {
  return <ModulePage module={getModule('/analytics')} />
}
