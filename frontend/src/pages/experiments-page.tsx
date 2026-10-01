import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function ExperimentsPage() {
  return <ModulePage module={getModule('/experiments')} />
}
