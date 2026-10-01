import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function LearningPage() {
  return <ModulePage module={getModule('/learning')} />
}
