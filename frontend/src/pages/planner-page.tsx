import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function PlannerPage() {
  return <ModulePage module={getModule('/planner')} />
}
