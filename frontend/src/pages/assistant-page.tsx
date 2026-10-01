import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function AssistantPage() {
  return <ModulePage module={getModule('/assistant')} />
}
