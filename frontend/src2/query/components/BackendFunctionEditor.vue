<script setup lang="ts">
import { ref, onMounted, inject, watch, computed } from 'vue'
import { call } from 'frappe-ui'
import { Play } from 'lucide-vue-next'
import ContentEditable from '../../components/ContentEditable.vue'
import { Query } from '../query'

// ========== 从父组件注入 query ==========
const query = inject<Query>('query')!
const doc = query.doc as any

// ========== 默认值 ==========
const DEFAULT_FUNCTION_PATH = 'healthland_pos.pos.ping'
const DEFAULT_FUNCTION_ARGS = '{"outlet": "KD", "date": "2026-06-22"}'

// ========== State ==========
const functionPath = ref('')
const functionArgs = ref('')
const isLoading = ref(false)
const isSaving = ref(false)
const errorMessage = ref<string>("")
const tableKey = ref(0)

// 直接存储结果，不依赖 query.result
const resultRows = ref<any[]>([])
const resultColumns = ref<string[]>([])
const resultTime = ref<number>(-1)
const resultExecutedAt = ref<Date | null>(null)

// ========== 初始化 ==========
onMounted(() => {
    if (doc) {
        functionPath.value = doc.backend_function_path || DEFAULT_FUNCTION_PATH
        functionArgs.value = doc.backend_function_args || DEFAULT_FUNCTION_ARGS
    }
    
    if (!doc.is_backend_function) {
        doc.is_backend_function = 1
    }
})

// ========== 监听变化 ==========
watch([functionPath, functionArgs], () => {
    if (doc) {
        doc.backend_function_path = functionPath.value
        doc.backend_function_args = functionArgs.value
    }
})

// ========== 执行函数 ==========
async function executeFunction() {
    if (!functionPath.value) {
        showMessage('Please enter a function path', 'error')
        return
    }

    isLoading.value = true
    errorMessage.value = ""
    const startTime = performance.now()

    try {
        let args = {}
        if (functionArgs.value && functionArgs.value.trim()) {
            try {
                args = JSON.parse(functionArgs.value)
            } catch (e) {
                showMessage('Invalid JSON in arguments', 'error')
                isLoading.value = false
                return
            }
        }

        const response = await call(functionPath.value, args)

        // 处理响应数据
        let rows: any[] = []
        let columns: string[] = []
        
        if (Array.isArray(response)) {
            rows = response
            if (rows.length > 0) {
                columns = Object.keys(rows[0])
            }
        } else if (typeof response === 'object' && response !== null) {
            rows = [response]
            columns = Object.keys(response)
        } else {
            rows = [{ value: response }]
            columns = ['value']
        }

        // 存储到本地 ref
        resultRows.value = rows
        resultColumns.value = columns
        resultTime.value = (performance.now() - startTime) / 1000
        resultExecutedAt.value = new Date()
        tableKey.value++

        // 也更新 query.result 以便兼容
        query.result.rows = rows
        query.result.totalRowCount = rows.length
        query.result.columns = columns.map(key => ({ name: key, type: 'String' as const }))
        query.result.timeTaken = resultTime.value
        query.result.lastExecutedAt = resultExecutedAt.value
        query.result.executedSQL = `Backend function: ${functionPath.value}`
    } catch (e: any) {
        errorMessage.value = e.message || 'Function call failed'
        resultRows.value = []
        resultColumns.value = []
        showMessage(errorMessage.value, 'error')
    } finally {
        isLoading.value = false
    }
}

// ========== 保存配置 ==========
async function saveConfig() {
    if (!doc || isSaving.value) return
    if (!doc.name || doc.name.startsWith('new-query-')) {
        return
    }
    
    isSaving.value = true
    try {
        await call('frappe.client.set_value', {
            doctype: 'Insights Query v3',
            name: doc.name,
            fieldname: {
                backend_function_path: functionPath.value,
                backend_function_args: functionArgs.value,
                is_backend_function: 1,
            },
        })
    } catch (e) {
        showMessage('Failed to save configuration', 'error')
    } finally {
        isSaving.value = false
    }
}

// ========== 时间格式化 ==========
const timeAgo = computed(() => {
    if (!resultExecutedAt.value) return ''
    const now = new Date()
    const diff = (now.getTime() - resultExecutedAt.value.getTime()) / 1000
    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
})

// ========== 消息提示 ==========
function showMessage(message: string, type: 'success' | 'error' = 'success') {
    alert((type === 'success' ? '✅ ' : '❌ ') + message)
}

// ========== 计算属性 ==========
const hasResult = computed(() => {
    return resultRows.value && resultRows.value.length > 0
})

const hasError = computed(() => {
    return errorMessage.value !== null && errorMessage.value !== ""
})

// ========== 暴露给父组件 ==========
defineExpose({
    executeFunction,
    saveConfig
})
</script>

<template>
    <div class="flex flex-1 flex-col gap-4 overflow-hidden p-4">
        <!-- 配置区域 -->
        <div class="relative flex w-full flex-col rounded border">
            <div class="flex flex-shrink-0 items-center gap-1 border-b p-1">
                <ContentEditable
                    class="flex h-7 cursor-text items-center justify-center rounded bg-white px-2 text-base leading-7 text-gray-800 focus-visible:ring-1 focus-visible:ring-gray-600"
                    :modelValue="query.doc.title"
                    @returned="query.doc.title = $event"
                    @blur="query.doc.title = $event"
                    placeholder="Untitled Query"
                />
            </div>
            
            <div class="flex flex-col gap-4 p-4">
                <!-- Function Path -->
                <div class="flex flex-col gap-1.5">
                    <label class="text-sm font-medium text-gray-700">Function Path</label>
                    <input
                        v-model="functionPath"
                        type="text"
                        class="w-full rounded border border-gray-300 px-3 py-2 text-sm font-mono focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                        placeholder="e.g. healthland_pos.pos.ping"
                    />
                    <p class="text-xs text-gray-500">Enter the full Python path to your backend function</p>
                </div>

                <!-- Arguments -->
                <div class="flex flex-col gap-1.5">
                    <label class="text-sm font-medium text-gray-700">Arguments (JSON)</label>
                    <textarea
                        v-model="functionArgs"
                        class="w-full rounded border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                        rows="3"
                        placeholder='{"outlet": "Kota Damansara (SH001)"}'
                    />
                    <p class="text-xs text-gray-500">Pass arguments as JSON object. Leave empty if no arguments needed.</p>
                </div>
            </div>

            <!-- 工具栏 -->
            <div class="flex flex-shrink-0 gap-1 border-t p-1">
                <button
                    @click="executeFunction"
                    :disabled="isLoading || !functionPath"
                    class="inline-flex items-center gap-1.5 rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                    <Play class="h-3.5 w-3.5" stroke-width="1.5" />
                    {{ isLoading ? 'Running...' : 'Run' }}
                </button>
            </div>
        </div>

        <!-- 执行状态 -->
        <div
            v-show="query.result.executedSQL || hasResult || hasError"
            class="tnum flex flex-shrink-0 items-center gap-2 text-sm text-gray-600"
        >
            <div class="h-2 w-2 rounded-full" :class="hasError ? 'bg-red-500' : 'bg-green-500'"></div>
            <div class="flex items-center gap-1">
                <span v-if="hasError"> {{ errorMessage }} </span>
                <span v-else-if="resultTime >= 0"> 
                    Fetched in {{ resultTime.toFixed(2) }}s 
                </span>
                <span v-if="!hasError && timeAgo && resultTime >= 0"> · {{ timeAgo }} </span>
                <span v-else-if="!hasError && hasResult">
                    {{ resultRows.length }} rows
                </span>
            </div>
        </div>

        <!-- ====== 结果表格（使用简单 HTML 表格） ====== -->
        <div v-if="hasResult" class="relative flex w-full flex-1 flex-col overflow-hidden rounded border">
            <div class="flex-shrink-0 bg-gray-50 px-3 py-2 text-sm font-medium text-gray-700 border-b">
                Results
            </div>
            <div class="flex-1 overflow-auto">
                <table class="w-full text-sm">
                    <thead class="sticky top-0 bg-gray-100">
                        <tr>
                            <th class="border-b px-3 py-2 text-left font-medium text-gray-600">#</th>
                            <th
                                v-for="col in resultColumns"
                                :key="col"
                                class="border-b px-3 py-2 text-left font-medium text-gray-600"
                            >
                                {{ col }}
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="(row, index) in resultRows"
                            :key="index"
                            class="hover:bg-gray-50"
                            :class="index % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'"
                        >
                            <td class="border-b px-3 py-2 text-gray-400">{{ index + 1 }}</td>
                            <td
                                v-for="col in resultColumns"
                                :key="col"
                                class="border-b px-3 py-2"
                            >
                                {{ row[col] !== undefined && row[col] !== null ? row[col] : '-' }}
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <div class="flex-shrink-0 border-t bg-gray-50 px-3 py-1.5 text-xs text-gray-500">
                {{ resultRows.length }} rows
            </div>
        </div>

        <!-- 空状态 -->
        <div
            v-else-if="!hasResult && !hasError && !isLoading"
            class="relative flex w-full flex-1 flex-col items-center justify-center rounded border border-dashed border-gray-300 bg-gray-50"
        >
            <div class="text-center text-gray-400">
                <Play class="mx-auto h-8 w-8 text-gray-300" stroke-width="1.5" />
                <p class="mt-2 text-sm">Click "Run" to execute the function</p>
            </div>
        </div>
    </div>
</template>