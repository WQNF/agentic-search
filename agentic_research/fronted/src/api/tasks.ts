// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\fronted\src\api\tasks.ts
import request from './axios'

export interface TaskItem {
  id: string
  user_id: string
  query: string
  run_id: string
  status: string
  final_status: string | null
  entity: Record<string, any> | null
  created_at: string
  finished_at: string | null
  duration_ms: number | null
}

export interface TaskListResponse {
  total: number
  items: TaskItem[]
}

export interface ReportData {
  task_id: string
  run_id: string
  report_markdown: string
  final_status: string
}

export const tasksApi = {
  list: async (skip = 0, limit = 20): Promise<TaskListResponse> => {
    const res = await request.get('/tasks', { params: { skip, limit } }) as unknown as TaskListResponse
    return res
  },

  getById: async (taskId: string): Promise<TaskItem> => {
    const res = await request.get(`/tasks/${taskId}`) as unknown as TaskItem
    return res
  },

  create: async (query: string): Promise<TaskItem> => {
    const res = await request.post('/tasks', { query }) as unknown as TaskItem
    return res
  },

  getReport: async (taskId: string): Promise<ReportData> => {
    const res = await request.get(`/tasks/${taskId}/report`) as unknown as ReportData
    return res
  },
}