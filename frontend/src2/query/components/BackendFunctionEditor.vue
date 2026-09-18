<script setup lang="ts">
import { useTimeAgo } from '@vueuse/core'
import { Play } from 'lucide-vue-next'
import { computed, inject } from 'vue'
import Code from '../../components/Code.vue'
import ContentEditable from '../../components/ContentEditable.vue'
import InlineFormControlLabel from '../../components/InlineFormControlLabel.vue'
import { __ } from '../../translation'
import { Query } from '../query'
import QueryDataTable from './QueryDataTable.vue'

const query = inject<Query>('query')!

const DEFAULT_FUNCTION_PATH = 'healthland_pos.pos.ping'
const DEFAULT_FUNCTION_ARGS = '{"from_date": "2026-05-01", "to_date": "2026-06-30", "outlet": "KD"}'

// the flag is set by the query type selector, keep it on the doc so it is saved with the query
query.doc.is_backend_function = true

// fill the sample defaults into a brand new query
if (!query.doc.backend_function_path) {
	query.doc.backend_function_path = DEFAULT_FUNCTION_PATH
}
if (!query.doc.backend_function_args) {
	query.doc.backend_function_args = DEFAULT_FUNCTION_ARGS
}

// NOTE: always read/write through query.doc. The doc object is replaced with a new
// object every time the query is saved, so capturing `query.doc` into a variable
// silently redirects later edits into a stale object and they never get saved.

const argsError = computed(() => {
	const args = query.doc.backend_function_args
	if (!args?.trim()) return ''
	try {
		const parsed = JSON.parse(args)
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
			return __('Arguments must be a JSON object')
		}
		return ''
	} catch (e: any) {
		return e.message || __('Invalid JSON')
	}
})

function run() {
	if (!query.doc.backend_function_path || argsError.value) return
	query.execute(true)
}

query.autoExecute = false
query.execute()
</script>

<template>
	<div class="flex flex-1 flex-col gap-4 overflow-hidden p-4">
		<div class="relative flex w-full flex-shrink-0 flex-col rounded border">
			<div class="flex flex-shrink-0 items-center gap-1 border-b p-1">
				<ContentEditable
					class="flex h-7 cursor-text items-center justify-center rounded bg-white px-2 text-base leading-7 text-gray-800 focus-visible:ring-1 focus-visible:ring-gray-600"
					:modelValue="query.doc.title"
					@returned="query.doc.title = $event"
					@blur="query.doc.title = $event"
					placeholder="Untitled Query"
				/>
			</div>

			<div class="flex flex-col gap-3 p-4">
				<InlineFormControlLabel :label="__('Function Path')">
					<FormControl
						v-model="query.doc.backend_function_path"
						type="text"
						placeholder="e.g. healthland_pos.pos.get_sales_summary"
					/>
				</InlineFormControlLabel>
				<p class="-mt-2 text-xs text-gray-500">
					{{
						__(
							'Full python path of a whitelisted backend function. It should return a list of dicts (one per row).',
						)
					}}
				</p>

				<div class="flex flex-col gap-1.5">
					<div class="text-xs text-gray-600">{{ __('Arguments (JSON)') }}</div>
					<div class="h-20 overflow-hidden rounded border">
						<Code v-model="query.doc.backend_function_args" language="javascript" />
					</div>
					<p v-if="argsError" class="text-xs text-red-600">{{ argsError }}</p>
					<p v-else class="text-xs text-gray-500">
						{{ __('Passed as keyword arguments. Leave empty if not needed.') }}
					</p>
				</div>
			</div>

			<div class="flex flex-shrink-0 gap-1 border-t p-1">
				<Button
					@click="run"
					:disabled="!query.doc.backend_function_path || Boolean(argsError)"
					:loading="query.executing"
				>
					<template #prefix>
						<Play class="h-3.5 w-3.5 text-gray-700" stroke-width="1.5" />
					</template>
					{{ __('Run') }}
				</Button>
			</div>
		</div>

		<div
			v-show="query.result.executedSQL"
			class="tnum flex flex-shrink-0 items-center gap-2 text-sm text-gray-600"
		>
			<div class="h-2 w-2 rounded-full bg-green-500"></div>
			<div class="flex items-center gap-1">
				<span v-if="query.result.timeTaken == -1"> {{ __('Fetched from cache') }} </span>
				<span v-else> {{ __('Fetched in {0}s', String(query.result.timeTaken)) }} </span>
				<span> {{ useTimeAgo(query.result.lastExecutedAt).value }} </span>
			</div>
		</div>

		<div class="relative flex w-full flex-1 flex-col overflow-hidden rounded border">
			<QueryDataTable :query="query" />
		</div>
	</div>
</template>
