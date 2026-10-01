import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function KnowledgePage() {
  return <ModulePage module={getModule('/knowledge')} />
}
