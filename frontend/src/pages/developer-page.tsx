import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function DeveloperPage() {
  return <ModulePage module={getModule('/developer')} />
}
