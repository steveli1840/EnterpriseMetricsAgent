export type Evidence = {
  metrics: Array<{ name: string; version: number; label: string; owner: string }>;
  schema_refs: string[];
  knowledge_refs: string[];
  filters: string[];
  time_window: { start: string; end: string };
  warehouse: string;
  row_count: number;
  elapsed_ms: number;
  schema_snapshot: string;
};

export type AnalysisResult = {
  answer: string;
  columns: string[];
  result_preview: Array<Array<string | number>>;
  sql: { dialect: string; statement: string; query_id: string };
  evidence: Evidence;
  trace_id: string;
  warnings: string[];
  chart_hint?: {
    enabled: boolean;
    type?: string | null;
    x?: string | null;
    y?: string | null;
  } | null;
};

export type MetricDefinition = {
  name: string;
  version: number;
  label: string;
  owner: string;
  status: string;
  description?: string;
  model?: string;
  expression?: string;
  aggregation?: string;
  time_dimension?: string;
  grain?: string;
  allowed_dimensions?: string[];
  filters?: string[];
};

export type SchemaCatalog = {
  snapshot: string | null;
  models: string[];
  source?: string;
};

export type KnowledgeCatalog = {
  documents: Array<{
    source_type: string;
    source_ref: string;
    content_hash: string;
    metadata: Record<string, unknown>;
  }>;
  embedding_model: string;
};

export type MemoryItem = {
  id: string;
  kind: string;
  value: Record<string, unknown>;
  status: string;
  created_at: string;
};

export type AuditItem = {
  id: string;
  user_id: string;
  conversation_id: string;
  trace_id: string;
  sql_hash: string;
  normalized_sql: string;
  evidence: Evidence;
  created_at: string;
};

export type DataSourceItem = {
  id: string;
  name: string;
  provider: string;
  status: string;
  is_active: boolean;
  config: Record<string, unknown>;
  created_by: string;
  created_at: string;
};

export type DataSourceRequest = {
  name: string;
  provider: string;
  config: Record<string, string | number>;
  is_active: boolean;
};

export type EvaluationSummary = {
  suite: string;
  cases: number;
  passed: number;
  failed: number;
  pass_rate: number;
  metric_accuracy: number;
  dimension_accuracy: number;
  refusal_accuracy: number;
};

async function getJson<T>(token: string, path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error("无法读取治理数据");
  return response.json() as Promise<T>;
}

async function postJson<T>(token: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "操作失败";
    try {
      const payload = await response.json();
      if (payload?.detail) detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function login(username: string, password: string): Promise<string> {
  const response = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw new Error("账号或密码不正确");
  const payload = await response.json();
  return payload.access_token;
}

export async function getMetrics(token: string): Promise<MetricDefinition[]> {
  return getJson<MetricDefinition[]>(token, "/api/v1/metrics");
}

export async function upsertMetric(
  token: string,
  metric: MetricDefinition,
): Promise<MetricDefinition> {
  return postJson<MetricDefinition>(token, "/api/v1/admin/metrics", metric);
}

export async function syncMetrics(token: string): Promise<{ status: string; metrics: number }> {
  return postJson(token, "/api/v1/admin/metrics/sync");
}

export async function getSchemas(token: string): Promise<SchemaCatalog> {
  return getJson<SchemaCatalog>(token, "/api/v1/schemas");
}

export async function describeSchema(
  token: string,
  model: string,
): Promise<{ model: string; snapshot: string | null; columns: Array<{ name: string; type: string }> }> {
  return getJson(token, `/api/v1/schemas/${encodeURIComponent(model)}`);
}

export async function getKnowledge(token: string): Promise<KnowledgeCatalog> {
  return getJson<KnowledgeCatalog>(token, "/api/v1/knowledge");
}

export async function getMemories(token: string): Promise<{ user_id: string; items: MemoryItem[] }> {
  return getJson<{ user_id: string; items: MemoryItem[] }>(token, "/api/v1/memories");
}

export async function getAudit(token: string): Promise<{ items: AuditItem[] }> {
  return getJson<{ items: AuditItem[] }>(token, "/api/v1/audit/queries");
}

export async function getEvaluations(token: string): Promise<{
  cases: number;
  latest: EvaluationSummary | null;
}> {
  return getJson<{ cases: number; latest: EvaluationSummary | null }>(token, "/api/v1/evaluations");
}

export async function getDataSources(token: string): Promise<{ items: DataSourceItem[] }> {
  return getJson<{ items: DataSourceItem[] }>(token, "/api/v1/data-sources");
}

export async function createDataSource(
  token: string,
  request: DataSourceRequest,
): Promise<DataSourceItem> {
  return postJson<DataSourceItem>(token, "/api/v1/admin/data-sources", request);
}

export async function activateDataSource(token: string, sourceId: string): Promise<DataSourceItem> {
  return postJson<DataSourceItem>(token, `/api/v1/admin/data-sources/${sourceId}/activate`);
}

export async function testActiveDataSource(token: string): Promise<{
  status: string;
  elapsed_ms: number;
  source: string;
  provider: string;
}> {
  return postJson(token, "/api/v1/admin/data-sources/test");
}

export async function refreshSchemas(token: string): Promise<{
  status: string;
  snapshot: string;
  source: string;
  columns: number;
}> {
  return postJson(token, "/api/v1/admin/schemas/refresh");
}

export async function reindexKnowledge(token: string): Promise<{
  status: string;
  documents: number;
  embedding_model: string;
  dimensions: number;
}> {
  return postJson(token, "/api/v1/admin/knowledge/reindex");
}

export async function uploadKnowledge(
  token: string,
  file: File,
): Promise<{ status: string; path?: string; documents?: number }> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/v1/admin/knowledge/upload?reindex=true", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body,
  });
  if (!response.ok) {
    let detail = "上传失败";
    try {
      const payload = await response.json();
      if (payload?.detail) detail = String(payload.detail);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return response.json();
}

export async function confirmMemory(token: string, memoryId: string): Promise<MemoryItem> {
  return postJson<MemoryItem>(token, `/api/v1/memories/${memoryId}/confirm`);
}

export async function deleteMemory(token: string, memoryId: string): Promise<void> {
  const response = await fetch(`/api/v1/memories/${memoryId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error("删除记忆失败");
}

export async function runEvaluations(token: string): Promise<EvaluationSummary & { items: unknown[] }> {
  return postJson(token, "/api/v1/evaluations/run");
}

export type ConversationItem = {
  id: string;
  title: string;
  user_id: string;
  state: Record<string, unknown>;
  created_at: string;
};

export async function getConversations(token: string): Promise<{ items: ConversationItem[] }> {
  return getJson<{ items: ConversationItem[] }>(token, "/api/v1/conversations");
}

export async function createConversation(token: string, title: string): Promise<ConversationItem> {
  return postJson<ConversationItem>(token, "/api/v1/conversations", { title });
}

export async function getConversation(token: string, conversationId: string): Promise<ConversationItem> {
  return getJson<ConversationItem>(token, `/api/v1/conversations/${conversationId}`);
}

export async function renameConversation(
  token: string,
  conversationId: string,
  title: string,
): Promise<ConversationItem> {
  const response = await fetch(`/api/v1/conversations/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new Error("重命名失败");
  return response.json() as Promise<ConversationItem>;
}

export async function deleteConversation(token: string, conversationId: string): Promise<void> {
  const response = await fetch(`/api/v1/conversations/${conversationId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error("删除失败");
}

export async function streamAnalysis(
  token: string,
  question: string,
  onProgress: (label: string) => void,
  conversationId = "workspace",
): Promise<AnalysisResult> {
  const response = await fetch("/api/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question, conversation_id: conversationId }),
  });
  if (!response.ok || !response.body) throw new Error("无法连接分析服务");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const type = event.match(/^event: (.+)$/m)?.[1];
      const data = event.match(/^data: (.+)$/m)?.[1];
      if (!data) continue;
      const payload = JSON.parse(data);
      if (type === "progress") onProgress(payload.label);
      if (type === "clarification") throw new Error(payload.detail);
      if (type === "error") throw new Error(payload.detail || "分析失败");
      if (type === "result") return payload;
    }
  }
  throw new Error("分析流在返回结果前结束");
}
