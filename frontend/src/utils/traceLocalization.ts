export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  system_overview: '系统概览',
  health_check: '健康检查',
  get_disk_usage: '磁盘使用情况',
  find_large_files: '查找大文件',
  get_directory_size: '目录大小',
  get_inode_usage: 'Inode 使用情况',
  check_file_info: '文件信息',
  list_directory: '列出目录',
  read_file: '读取文件',
  find_files: '查找文件/目录',
  create_file: '创建文件',
  create_directory: '创建目录',
  write_file: '写入文件',
  delete_file: '删除文件',
  delete_directory: '删除目录',
  move_file: '移动/重命名文件',
  copy_file: '复制文件/目录',
  change_permissions: '修改文件权限',
  change_owner: '修改文件所有者',
  read_config_file: '读取配置文件',
  check_config_syntax: '检查配置语法',
  diff_config: '对比配置',
  list_services: '服务列表',
  get_service_status: '服务状态',
  get_failed_services: '失败的服务',
  restart_service: '重启服务',
  start_service: '启动服务',
  stop_service: '停止服务',
  get_service_logs: '服务日志',
  get_journal_logs: 'systemd 日志',
  get_recent_errors: '最近错误日志',
  tail_log_file: '查看日志末尾',
  search_logs: '搜索日志',
  get_boot_logs: '启动日志',
  get_listening_ports: '监听端口',
  get_connections: '网络连接',
  get_connection_count: '连接数统计',
  check_port: '端口占用查询',
  ping_host: 'Ping 主机',
  list_processes: '进程列表',
  find_zombie_processes: '查找僵尸进程',
  get_process_detail: '进程详情',
  kill_process: '终止进程',
  get_user_sessions: '登录用户会话',
  get_crontab_list: '定时任务列表',
  list_installed_packages: '已安装软件包',
  search_package: '搜索软件包',
  install_package: '安装软件包',
  remove_package: '卸载软件包',
  check_package_updates: '检查软件更新',
  list_users: '用户列表',
  list_groups: '用户组列表',
  get_user_info: '用户详情',
  create_user: '创建用户',
  delete_user: '删除用户',
  lock_user: '锁定用户',
  unlock_user: '解锁用户',
  get_firewall_status: '防火墙状态',
  list_open_ports: '已开放端口',
  allow_port: '开放端口',
  block_port: '关闭端口',
  list_cron_jobs: '定时任务详情',
  list_system_timers: 'systemd 定时器',
  add_cron_job: '添加定时任务',
  remove_cron_job: '删除定时任务',
  list_backups: '备份列表',
  rollback_backup: '恢复备份',
  get_recent_changes: '最近变更',
  prometheus_query: 'Prometheus 即时查询',
  prometheus_range_query: 'Prometheus 区间查询',
  loki_query: 'Loki 即时日志查询',
  loki_range_query: 'Loki 区间日志查询',
}

export const SOURCE_DISPLAY_NAMES: Record<string, string> = {
  agent: '智能体',
  LLM: '模型推断',
  SafetyGuardrail: '安全护栏',
  'SafetyGuardrail.check_input': '安全护栏',
  'SafetyGuardrail.check_command': '安全规则',
  'SafetyGuardrail.check_high_risk_intent': '高风险意图识别',
  knowledge_store: '知识库',
  'knowledge_store.search': '知识库检索',
  read_only_intent_guard: '只读意图保护',
  write_completion_guard: '写操作真实性保护',
  read_tool_truthfulness_guard: '只读检查真实性保护',
  structured_final_response_guard: '结构化最终回复校验',
  'context_manager.build_context_package': '上下文管理',
  approval_manager: '审批管理器',
  'BackupManager.backup_file': '备份管理器',
  runbook_executor: 'Runbook 执行器',
  assess_impact: '影响评估',
  alert_webhook: '告警自动分析',
  grafana_dashboard: 'Grafana 面板',
  prometheus: 'Prometheus',
  loki: 'Loki',
  aliyun_dashscope: '阿里云百炼',
  multimodal_input: '多模态输入',
}

const CLAIM_TRANSLATIONS: Record<string, string> = {
  'Incident problem statement came from the submitted user request': '事件问题描述来自用户输入',
  'User request is being checked by safety rules': '正在根据安全规则检查用户请求',
  'Safety guardrail allowed the request': '安全护栏允许该请求继续执行',
  'Safety guardrail blocked the request': '安全护栏已拦截该请求',
  'Knowledge search has been requested': '正在检索历史经验',
  'Knowledge retrieval failed': '知识库检索失败',
  'The agent is planning next checks or actions': '智能体正在规划下一步检查或操作',
  'The agent is planning next checks or actions.': '智能体正在规划下一步检查或操作。',
  'Final response was generated from prior evidence and messages': '已基于现有证据和上下文生成最终回复',
  'Model response claimed a write completion without executed write evidence': '模型声称写操作已完成，但没有真实执行证据',
  'Model repeated a write completion claim without executed write evidence': '模型再次声称写操作已完成，但没有真实执行证据',
  'Model response claimed a write completion after tool failure': '写操作工具失败后，模型仍声称已完成',
  'Model repeated a write completion claim after failed write tools': '写操作工具失败后，模型再次声称操作已完成',
  'High-risk intent was detected': '检测到高风险操作意图',
  'Alert webhook payload was normalized into an auto-triage request': '告警 Webhook 已转换为自动分析请求',
  'Auto-triage report was persisted as an assistant message': '告警自动分析报告已保存为智能体回复',
}

const TEXT_TRANSLATIONS: Record<string, string> = {
  'Read-only user intent cannot trigger write/destructive tools': '只读意图不能触发写操作或破坏性工具',
  'User approval was not granted': '用户未批准该操作',
  'Tool is not registered': '工具未注册',
  'ToolResult.success is false': '工具返回 success=false',
  'No write/destructive tool was called in this turn': '本轮没有调用写操作或破坏性工具',
  'No write/destructive tool was called after guard retry': '重试后仍没有调用写操作或破坏性工具',
  'Runbook did not complete': 'Runbook 未完成',
  'Review the tool error and run a safer read-only check before retrying.': '请先复核工具错误，再用只读检查确认状态后重试。',
  'Check the knowledge database/search backend before relying on history.': '请先检查知识库或搜索后端，再依赖历史经验。',
  'Re-run a read-only status/config check to isolate the mismatch.': '请重新执行只读状态或配置检查，定位执行结果不一致的原因。',
  'Inspect tool arguments and retry with a read-only check if possible.': '请检查工具参数，并优先用只读检查确认状态后重试。',
  'Ask for explicit execution again so approval and tool execution can run.': '请重新发起明确执行请求，以便系统走审批和真实工具执行。',
  'Force the agent to either execute an approved tool or retract the claim.': '请让智能体执行已审批工具，或明确撤回该完成声明。',
  'Fix the failed tool result before retrying the write operation.': '请先处理工具失败原因，再重试写操作。',
  'Validate or update the runbook before replaying it.': '请先校验或更新 Runbook，再重新执行。',
  'Inspect the runbook step arguments and retry with a read-only validation first.': '请检查 Runbook 步骤参数，并先用只读校验确认后再重试。',
  'search completed; no matching entries': '检索完成，未找到匹配经验',
  '请使用真实 MCP 工具确认图片或语音中提到的系统状态。': '请使用真实 MCP 工具确认图片或语音中提到的系统状态。',
  success: '成功',
  failure: '失败',
  failed: '失败',
  completed: '已完成',
  skipped: '已跳过',
}

export function displayTraceName(value?: string): string {
  if (!value) return ''
  return TOOL_DISPLAY_NAMES[value] || SOURCE_DISPLAY_NAMES[value] || value
}

export function translateTraceClaim(claim?: string): string {
  if (!claim) return ''
  if (CLAIM_TRANSLATIONS[claim]) return CLAIM_TRANSLATIONS[claim]

  const planning = claim.match(/^Planning to call ([\w_]+)$/)
  if (planning) return `准备调用工具：${displayTraceName(planning[1])}`

  const executed = claim.match(/^([\w_]+) executed against (.+)$/)
  if (executed) return `${displayTraceName(executed[1])} 已对 ${translateTraceText(executed[2])} 执行完成`

  const failed = claim.match(/^([\w_]+) failed against (.+)$/)
  if (failed) return `${displayTraceName(failed[1])} 对 ${translateTraceText(failed[2])} 执行失败`

  const skipped = claim.match(/^([\w_]+) was skipped before execution$/)
  if (skipped) return `${displayTraceName(skipped[1])} 已在执行前跳过`

  const missing = claim.match(/^([\w_]+) could not run because the tool is missing$/)
  if (missing) return `${displayTraceName(missing[1])} 无法执行：工具未注册`

  const blocked = claim.match(/^([\w_]+) was blocked because webhook auto-triage is read-only$/)
  if (blocked) return `${displayTraceName(blocked[1])} 已被阻断：告警自动分析只允许只读检查`

  const autoPlan = claim.match(/^Webhook auto-triage is about to run read-only tool ([\w_]+)$/)
  if (autoPlan) return `告警自动分析准备执行只读工具：${displayTraceName(autoPlan[1])}`

  const autoException = claim.match(/^([\w_]+) raised an exception during webhook auto-triage$/)
  if (autoException) return `${displayTraceName(autoException[1])} 在告警自动分析中执行异常`

  const autoResult = claim.match(/^(.+): ([\w_]+) returned (success|failure)$/)
  if (autoResult) {
    const status = autoResult[3] === 'success' ? '成功' : '失败'
    return `${translateTraceText(autoResult[1])}：${displayTraceName(autoResult[2])} 返回${status}`
  }

  return claim
}

export function localizeTraceContent(content?: string): string {
  if (!content) return ''

  const toolCall = content.match(/^调用工具:\s*([\w_]+)\((.*)\)$/s)
  if (toolCall) return `准备调用工具：${displayTraceName(toolCall[1])}\n参数：${toolCall[2]}`

  const toolCallLoose = content.match(/^调用工具:\s*([\w_]+)(.*)$/s)
  if (toolCallLoose) return `准备调用工具：${displayTraceName(toolCallLoose[1])}${toolCallLoose[2] ? `\n参数：${toolCallLoose[2].trim()}` : ''}`

  const autoPlan = content.match(/^Auto-triage read-only check:\s*([\w_]+)$/)
  if (autoPlan) return `告警自动分析准备执行只读工具：${displayTraceName(autoPlan[1])}`

  const skipped = content.match(/^Skipped ([\w_]+):\s*(.+)$/s)
  if (skipped) return `已跳过 ${displayTraceName(skipped[1])}：${translateTraceText(skipped[2])}`

  const missing = content.match(/^Tool ([\w_]+) is not registered$/)
  if (missing) return `工具未注册：${displayTraceName(missing[1])}`

  const blocked = content.match(/^Blocked non-read tool during webhook auto-triage:\s*([\w_]+)$/)
  if (blocked) return `告警自动分析已阻断非只读工具：${displayTraceName(blocked[1])}`

  const raised = content.match(/^([\w_]+) raised exception:\s*(.+)$/s)
  if (raised) return `${displayTraceName(raised[1])} 执行异常：${raised[2]}`

  const completed = content.match(/^([\w_]+) (completed|failed)$/)
  if (completed) {
    const status = completed[2] === 'completed' ? '执行完成' : '执行失败'
    return `${displayTraceName(completed[1])} ${status}`
  }

  if (content === 'Alert auto-triage report generated') return '告警自动分析报告已生成'
  if (content.startsWith('Alert webhook accepted:')) return content.replace('Alert webhook accepted:', '告警 Webhook 已接收：')

  return translateTraceText(content)
}

export function translateTraceText(value?: string): string {
  if (!value) return ''
  if (value === 'current system') return '当前系统'
  return TEXT_TRANSLATIONS[value] || translateTraceClaim(value)
}
