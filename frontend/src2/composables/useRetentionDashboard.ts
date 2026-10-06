import { call } from 'frappe-ui'
import { useRouter } from 'vue-router'
import { showErrorToast } from '../helpers'
import { createInfoToast } from '../helpers/toasts'

// Title of the dashboard seeded by
// `insights.customer_retention.setup.seed_customer_retention_dashboard`.
export const RETENTION_DASHBOARD_TITLE = 'Customer Retention'

export default function useRetentionDashboard() {
	const router = useRouter()

	async function openRetentionDashboard() {
		try {
			// resolve the dashboard by title at runtime so the link keeps working
			// regardless of the generated (auto) dashboard name
			const dashboards = await call('insights.api.dashboards.get_dashboards', {
				search_term: RETENTION_DASHBOARD_TITLE,
				limit: 100,
			})
			const dashboard =
				dashboards.find((d: { title: string }) => d.title === RETENTION_DASHBOARD_TITLE) ||
				dashboards[0]
			if (!dashboard) {
				createInfoToast(
					'Customer Retention dashboard not found. Seed it from Insights first.',
				)
				return
			}
			router.push({ name: 'Dashboard', params: { name: dashboard.name } })
		} catch (error) {
			showErrorToast(error as Error, false)
		}
	}

	return { openRetentionDashboard }
}
