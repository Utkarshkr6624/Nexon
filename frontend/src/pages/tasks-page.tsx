import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function TasksPage() {
  return <ModulePage module={getModule('/tasks')} />
}
