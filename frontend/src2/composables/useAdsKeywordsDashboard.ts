import { call } from 'frappe-ui'
import { useRouter } from 'vue-router'
import { showErrorToast } from '../helpers'
import { createInfoToast } from '../helpers/toasts'

// Title of the dashboard seeded by
// `insights.ads_keywords.setup.seed_google_ads_keyword_dashboard`.
export const ADS_KEYWORDS_DASHBOARD_TITLE = 'Google Ads Keyword Analysis'

export default function useAdsKeywordsDashboard() {
	const router = useRouter()

	async function openAdsKeywordsDashboard() {
		try {
			// resolve the dashboard by title at runtime so the link keeps working
			// regardless of the generated (auto) dashboard name
			const dashboards = await call('insights.api.dashboards.get_dashboards', {
				search_term: ADS_KEYWORDS_DASHBOARD_TITLE,
				limit: 100,
			})
			const dashboard =
				dashboards.find(
					(d: { title: string }) => d.title === ADS_KEYWORDS_DASHBOARD_TITLE
				) || dashboards[0]
			if (!dashboard) {
				createInfoToast(
					'Google Ads Keyword Analysis dashboard not found. Seed it from Insights first.',
				)
				return
			}
			router.push({ name: 'Dashboard', params: { name: dashboard.name } })
		} catch (error) {
			showErrorToast(error as Error, false)
		}
	}

	return { openAdsKeywordsDashboard }
}
