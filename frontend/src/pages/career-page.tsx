import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function CareerPage() {
  return <ModulePage module={getModule('/career')} />
}
