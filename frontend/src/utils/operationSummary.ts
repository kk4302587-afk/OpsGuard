import { displayTraceName } from './traceLocalization'

export interface ParsedOperation {
  toolName: string
  args: Record<string, unknown>
}

export function parseOperationCommand(command?: string): ParsedOperation | null {
  const text = (command || '').trim()
  const match = text.match(/^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$/s)
  if (!match) return null

  try {
    const parsed = JSON.parse(match[2])
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    return { toolName: match[1], args: parsed as Record<string, unknown> }
  } catch {
    return null
  }
}

export function operationTitle(commandOrTool?: string): string {
  const parsed = parseOperationCommand(commandOrTool)
  return displayTraceName(parsed?.toolName || commandOrTool || '') || '操作'
}

export function operationTarget(args: Record<string, unknown>): string {
  const target = (
    args.filepath
    || args.dirpath
    || args.path
    || args.source
    || args.destination
    || args.service
    || args.pid
    || args.username
    || args.backup_id
  )
  return typeof target === 'string' || typeof target === 'number' ? String(target) : ''
}

export function summarizeOperation(command?: string): { title: string; target: string; detail: string } {
  const parsed = parseOperationCommand(command)
  if (!parsed) {
    return { title: command || '待确认操作', target: '', detail: '' }
  }

  const title = operationTitle(parsed.toolName)
  const target = operationTarget(parsed.args)
  const detail = operationDetail(parsed.toolName, parsed.args)
  return { title, target, detail }
}

function operationDetail(toolName: string, args: Record<string, unknown>): string {
  if (toolName === 'write_file') {
    const append = Boolean(args.append)
    const content = typeof args.content === 'string' ? args.content : ''
    return `${append ? '追加' : '覆盖写入'}${content ? `：${compact(content, 80)}` : ''}`
  }
  if (toolName === 'create_directory') return '确保目录存在'
  if (toolName === 'delete_file' || toolName === 'delete_directory') return '删除目标'
  if (toolName === 'move_file') return typeof args.destination === 'string' ? `移动到 ${args.destination}` : '移动或重命名'
  if (toolName === 'copy_file') return typeof args.destination === 'string' ? `复制到 ${args.destination}` : '复制目标'
  if (toolName === 'change_permissions') return typeof args.mode === 'string' ? `权限改为 ${args.mode}` : '修改权限'
  if (toolName === 'restart_service') return '重启服务'
  if (toolName === 'start_service') return '启动服务'
  if (toolName === 'stop_service') return '停止服务'
  if (toolName === 'install_package') return '安装软件包'
  if (toolName === 'remove_package') return '卸载软件包'
  if (toolName === 'add_cron_job') return '添加定时任务'
  if (toolName === 'remove_cron_job') return '删除定时任务'
  return ''
}

function compact(value: string, maxLength: number): string {
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text
}
