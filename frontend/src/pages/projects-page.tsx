import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function ProjectsPage() {
  return <ModulePage module={getModule('/projects')} />
}
