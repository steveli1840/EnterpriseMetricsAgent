import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { activateDataSource, confirmMemory, createConversation, createDataSource, deleteConversation, deleteMemory, describeSchema, getAudit, getConversation, getConversations, getDataSources, getEvaluations, getKnowledge, getMemories, getMetrics, getSchemas, login, refreshSchemas, reindexKnowledge, renameConversation, runEvaluations, streamAnalysis, syncMetrics, testActiveDataSource, uploadKnowledge, upsertMetric } from "./api";
import type { AnalysisResult, AuditItem, ConversationItem, DataSourceItem, MemoryItem, MetricDefinition } from "./api";
import { inferChartSpec } from "./chart";

type View = "analysis" | "metrics" | "schema" | "sources" | "knowledge" | "audit" | "evaluation" | "memory";

const DEMO_RESULT: AnalysisResult = {
  answer: "SP 的已交付收入最高，为 125,430.22。该结果仅统计状态为 delivered 的订单。",
  columns: ["customer_state", "delivered_revenue"],
  result_preview: [["SP", 125430.22], ["RJ", 82440.1], ["MG", 71209.44]],
  sql: {
    dialect: "clickhouse",
    query_id: "olist-demo-018",
    statement: `SELECT customer_state, sum(price + freight_value) AS delivered_revenue
FROM analytics.fct_order_items
WHERE order_status = 'delivered'
  AND order_purchase_at >= {start:DateTime}
  AND order_purchase_at < {end:DateTime}
GROUP BY customer_state
ORDER BY delivered_revenue DESC
LIMIT 1000`,
  },
  evidence: {
    metrics: [{ name: "delivered_revenue", version: 1, label: "已交付收入", owner: "analytics" }],
    schema_refs: ["analytics.fct_order_items", "customer_state"],
    knowledge_refs: ["knowledge/business_glossary.md#delivered-revenue"],
    filters: ["order_status = 'delivered'"],
    time_window: { start: "2018-01-01", end: "2018-02-01" },
    warehouse: "clickhouse",
    row_count: 3,
    elapsed_ms: 18,
    schema_snapshot: "olist-v1",
  },
  trace_id: "trace-demo",
  warnings: [],
  chart_hint: { enabled: true, type: "bar", x: "customer_state", y: "delivered_revenue" },
};

const nav: Array<{ id: View; label: string; glyph: string }> = [
  { id: "metrics", label: "指标", glyph: "∑" },
  { id: "schema", label: "数据目录", glyph: "▦" },
  { id: "sources", label: "数据源", glyph: "◫" },
  { id: "knowledge", label: "知识库", glyph: "◇" },
  { id: "audit", label: "查询审计", glyph: "◎" },
  { id: "evaluation", label: "评测", glyph: "✓" },
  { id: "memory", label: "个人记忆", glyph: "◌" },
];

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const token = await login(String(form.get("username")), String(form.get("password")));
      localStorage.setItem("metriclens_token", token);
      onLogin(token);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }
  return <main className="login-shell">
    <section className="login-card" aria-labelledby="login-title">
      <div className="brand-mark">M</div>
      <p className="eyebrow">Enterprise metrics agent</p>
      <h1 id="login-title">MetricLens</h1>
      <p className="login-lede">用受治理的指标回答业务问题。</p>
      <form onSubmit={submit}>
        <label>账号<input name="username" defaultValue="analyst" autoComplete="username" /></label>
        <label>密码<input name="password" type="password" defaultValue="analyst-demo" autoComplete="current-password" /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "正在登录…" : "进入工作台"}</button>
      </form>
      <div className="dataset-status"><span className="status-dot" /> Olist 数据集 · ClickHouse</div>
    </section>
  </main>;
}

function Sidebar({
  view, setView,
  conversations, activeId,
  onNewAnalysis, onSelectConversation, onRenameConversation, onDeleteConversation, onLogout,
}: {
  view: View; setView: (view: View) => void;
  conversations: ConversationItem[];
  activeId: string;
  onNewAnalysis: () => void;
  onSelectConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void;
  onDeleteConversation: (id: string) => void;
  onLogout: () => void;
}) {
  const [menuId, setMenuId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  return <aside className="sidebar">
    <div className="brand"><span className="brand-mark small">M</span><strong>MetricLens</strong></div>
    <button className="new-analysis" onClick={onNewAnalysis}>＋ 新建分析</button>
    <nav aria-label="主导航">
      {nav.map(item => <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => setView(item.id)} aria-label={item.label}>
        <span className="nav-glyph">{item.glyph}</span>{item.label}
      </button>)}
    </nav>
    <div className="recent">
      <p className="eyebrow">分析会话</p>
      {conversations.length === 0 && <p style={{fontSize:13,color:"#818a9a",padding:"0 4px"}}>暂无会话</p>}
      {conversations.slice(0, 20).map(conv => (
        <div key={conv.id} className={`recent-item ${conv.id === activeId ? "active" : ""}`}>
          {editingId === conv.id ? (
            <input
              className="recent-rename"
              value={draftTitle}
              autoFocus
              onChange={e => setDraftTitle(e.target.value)}
              onBlur={() => {
                if (draftTitle.trim()) onRenameConversation(conv.id, draftTitle.trim());
                setEditingId(null);
              }}
              onKeyDown={e => {
                if (e.key === "Enter") {
                  if (draftTitle.trim()) onRenameConversation(conv.id, draftTitle.trim());
                  setEditingId(null);
                }
                if (e.key === "Escape") setEditingId(null);
              }}
            />
          ) : (
            <button className="recent-main" onClick={() => onSelectConversation(conv.id)}>
              {conv.title || "新分析"}
            </button>
          )}
          <button
            className="recent-more"
            aria-label="会话菜单"
            onClick={e => {
              e.stopPropagation();
              setMenuId(menuId === conv.id ? null : conv.id);
            }}
          >⋯</button>
          {menuId === conv.id && (
            <div className="recent-menu">
              <button onClick={() => {
                setEditingId(conv.id);
                setDraftTitle(conv.title || "");
                setMenuId(null);
              }}>重命名</button>
              <button onClick={() => {
                setMenuId(null);
                onDeleteConversation(conv.id);
              }}>删除</button>
            </div>
          )}
        </div>
      ))}
    </div>
    <div className="profile">
      <span>AN</span>
      <div><strong>数据分析师</strong><small>analyst@demo</small></div>
      <button className="logout" onClick={onLogout}>退出</button>
    </div>
  </aside>;
}

function hasExecutableSql(result: AnalysisResult): boolean {
  const statement = result.sql?.statement?.trim() ?? "";
  return statement.length > 0 && !statement.startsWith("--");
}

function isExploreResult(result: AnalysisResult): boolean {
  return result.evidence.metrics.length === 0 && (result.columns.length > 0 || hasExecutableSql(result));
}

function formatCell(cell: string | number | null | undefined): string {
  if (cell === null || cell === undefined || cell === "") return "—";
  if (typeof cell === "number") return cell.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(cell);
}

function EvidenceRail({ result }: { result: AnalysisResult }) {
  const hasMetric = result.evidence.metrics.length > 0;
  const explore = isExploreResult(result);
  const m = result.evidence.metrics[0];
  const items = hasMetric ? [
    ["指标口径", `${m.label} · v${m.version}`],
    ["数据范围", `${result.evidence.time_window.start} → ${result.evidence.time_window.end}`],
    ["Schema 来源", result.evidence.schema_refs[0] || "-"],
    ["SQL 校验", `read-only · ${result.sql.dialect}`],
    ["查询完成", `${result.evidence.row_count} rows · ${result.evidence.elapsed_ms} ms`],
  ] : explore ? [
    ["回答类型", "数据探查"],
    ["SQL 校验", `read-only · ${result.sql.dialect}`],
    ["查询完成", `${result.evidence.row_count} rows · ${result.evidence.elapsed_ms} ms`],
    ["知识来源", result.evidence.knowledge_refs.slice(0, 3).join(", ") || "schema / query tools"],
  ] : [
    ["回答类型", "知识问答"],
    ["知识来源", result.evidence.knowledge_refs.slice(0, 3).join(", ") || "系统上下文"],
  ];
  return <aside className="evidence-panel" aria-label="答案证据">
    <div className="panel-heading"><div><p className="eyebrow">Provenance</p><h2>证据轨</h2></div><span className="verified">{hasMetric || explore ? "已验证" : "上下文"}</span></div>
    <div className="evidence-rail">
      {items.map(([label, value]) => <button className="evidence-node" key={label}>
        <span className="node-check">✓</span><span><strong>{label}</strong><small>{value}</small></span>
      </button>)}
    </div>
    <div className="evidence-meta"><p><span>Query ID</span><code>{result.sql.query_id}</code></p><p><span>Snapshot</span><code>{result.evidence.schema_snapshot}</code></p>{hasMetric && <p><span>Owner</span><strong>{m.owner}</strong></p>}</div>
  </aside>;
}

function ResultTable({ result }: { result: AnalysisResult }) {
  if (result.columns.length === 0) return null;
  return <div className="result-card"><div className="result-toolbar"><strong>查询结果</strong><span>{result.evidence.row_count} 行</span></div>
    <div className="table-wrap"><table><thead><tr>{result.columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
    <tbody>{result.result_preview.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{formatCell(cell)}</td>)}</tr>)}</tbody></table></div>
  </div>;
}

function ResultVisual({ result }: { result: AnalysisResult }) {
  const chart = useMemo(() => inferChartSpec(result), [result]);
  const [mode, setMode] = useState<"chart" | "table">(chart ? "chart" : "table");
  if (result.columns.length === 0) return null;
  const option = chart ? {
    tooltip: { trigger: "axis" },
    grid: { left: 48, right: 16, top: 24, bottom: 48 },
    xAxis: {
      type: "category",
      data: chart.categories,
      axisLabel: { rotate: chart.categories.length > 8 ? 35 : 0, fontSize: 12 },
    },
    yAxis: { type: "value", scale: true },
    series: [{
      type: chart.type,
      data: chart.values,
      name: chart.yLabel,
      itemStyle: { color: "#3157d5" },
      areaStyle: chart.type === "line" ? { opacity: 0.08 } : undefined,
    }],
  } : null;

  return (
    <div className="result-visual">
      {chart && (
        <div className="result-tabs">
          <button className={mode === "chart" ? "active" : ""} onClick={() => setMode("chart")}>图表</button>
          <button className={mode === "table" ? "active" : ""} onClick={() => setMode("table")}>表格</button>
        </div>
      )}
      {mode === "chart" && option ? (
        <div className="result-card chart-card">
          <div className="result-toolbar"><strong>{chart?.yLabel} by {chart?.xLabel}</strong><span>{chart?.type}</span></div>
          <ReactECharts option={option} style={{ height: 280 }} opts={{ renderer: "svg" }} />
        </div>
      ) : (
        <ResultTable result={result} />
      )}
    </div>
  );
}

type Message =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; result: AnalysisResult }
  | { role: "progress"; steps: string[]; current: string };

type TurnItem = { id: string; text: string; index: number };

function turnIdForUserIndex(userIndex: number): string {
  return `turn-${userIndex}`;
}

function useStickyBottom(active: boolean, messages: Message[], progress: string) {
  const stickRef = useRef(true);

  useEffect(() => {
    const onScroll = () => {
      const root = document.documentElement;
      const distance = root.scrollHeight - window.scrollY - window.innerHeight;
      stickRef.current = distance < 140;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!active && !stickRef.current) return;
    if (typeof window.scrollTo !== "function") return;
    const preferSmooth =
      typeof window.matchMedia === "function"
        ? !window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: preferSmooth ? "smooth" : "auto",
    });
  }, [active, messages, progress]);

  return {
    pinToBottom() {
      stickRef.current = true;
    },
  };
}

function TurnNav({
  turns,
  activeId,
  onJump,
}: {
  turns: TurnItem[];
  activeId: string | null;
  onJump: (id: string) => void;
}) {
  if (turns.length < 1) return null;
  return (
    <nav className="turn-nav" aria-label="对话跳转">
      <div className="turn-nav-rail">
        {turns.map(turn => (
          <button
            key={turn.id}
            type="button"
            className={`turn-nav-item${activeId === turn.id ? " active" : ""}`}
            title={turn.text}
            aria-label={`跳转到：${turn.text}`}
            onClick={() => onJump(turn.id)}
          >
            <span className="turn-nav-bar" />
            <span className="turn-nav-label">{turn.text}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}

function ProgressBubble({ steps, current }: { steps: string[]; current: string }) {
  return (
    <article className="answer progress-answer">
      <div className="agent-avatar">M</div>
      <div className="answer-content">
        <div className="answer-label"><strong>MetricLens</strong><span>正在分析</span></div>
        <ul className="progress-steps">
          {steps.map(step => (
            <li key={step} className={step === current ? "current" : "done"}>
              <span>{step === current ? "…" : "✓"}</span>{step}
            </li>
          ))}
          {!steps.includes(current) && (
            <li className="current"><span>…</span>{current || "处理中"}</li>
          )}
        </ul>
      </div>
    </article>
  );
}

function AnswerMarkdown({ content }: { content: string }) {
  return (
    <div className="answer-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function SqlPanel({ result, defaultOpen }: { result: AnalysisResult; defaultOpen: boolean }) {
  const [sqlOpen, setSqlOpen] = useState(defaultOpen);
  if (!hasExecutableSql(result)) return null;
  return (
    <div className="sql-panel">
      <button className="sql-toggle" onClick={() => setSqlOpen(v => !v)} aria-expanded={sqlOpen}>
        {sqlOpen ? "收起 SQL" : "查看 SQL"}
        <span>{result.sql.dialect}</span>
      </button>
      {sqlOpen && <pre className="sql-view"><code>{result.sql.statement}</code></pre>}
    </div>
  );
}

function AnswerCard({ result }: { result: AnalysisResult }) {
  const hasMetric = result.evidence.metrics.length > 0;
  const explore = isExploreResult(result);
  const showQueryArtifacts = result.columns.length > 0 || hasExecutableSql(result);
  const m = result.evidence.metrics[0];
  const badge = hasMetric ? "基于受治理指标" : explore ? "数据探查" : "知识回答";
  return <article className="answer">
    <div className="agent-avatar">M</div>
    <div className="answer-content">
      <div className="answer-label"><strong>MetricLens</strong><span>{badge}</span></div>
      {hasMetric && (
        <div className="metric-context"><span>∑</span><div><small>采用指标</small><strong>{m.label}</strong><code>{m.name} · v{m.version}</code></div></div>
      )}
      {showQueryArtifacts && (
        <>
          <ResultVisual result={result} />
          <SqlPanel result={result} defaultOpen={!hasMetric} />
        </>
      )}
      <AnswerMarkdown content={result.answer} />
      {!showQueryArtifacts && <SqlPanel result={result} defaultOpen />}
    </div>
  </article>;
}

function messagesFromConversation(conv: ConversationItem): Message[] {
  const events = Array.isArray(conv.state?.events) ? (conv.state.events as Array<Record<string, unknown>>) : [];
  const messages: Message[] = [];
  for (const event of events) {
    const type = String(event.type || event.role || "");
    if (type === "user" && event.content) {
      messages.push({ role: "user", content: String(event.content) });
    }
    if (type === "assistant") {
      const result = event.result as AnalysisResult | undefined;
      if (result && result.answer) {
        messages.push({ role: "assistant", content: result.answer, result });
      } else if (event.content) {
        messages.push({
          role: "assistant",
          content: String(event.content),
          result: {
            answer: String(event.content),
            columns: [],
            result_preview: [],
            sql: { dialect: "clickhouse", statement: "-- restored", query_id: String(event.trace_id || "") },
            evidence: {
              metrics: [],
              schema_refs: [],
              knowledge_refs: [],
              filters: [],
              time_window: { start: "", end: "" },
              warehouse: "clickhouse",
              row_count: 0,
              elapsed_ms: 0,
              schema_snapshot: "",
            },
            trace_id: String(event.trace_id || ""),
            warnings: [],
          },
        });
      }
    }
  }
  return messages;
}

function Analysis({ token, conversationId, demoMode, onTitleChange }: { token: string; conversationId: string; demoMode?: boolean; onTitleChange?: (title: string) => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [progress, setProgress] = useState("");
  const [progressSteps, setProgressSteps] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadHistory() {
      if (demoMode || !conversationId) {
        setMessages([]);
        return;
      }
      try {
        const conv = await getConversation(token, conversationId);
        if (!cancelled) setMessages(messagesFromConversation(conv));
      } catch {
        if (!cancelled) setMessages([]);
      }
    }
    loadHistory();
    return () => { cancelled = true; };
  }, [token, conversationId, demoMode]);

  const turns = useMemo(() => {
    const items: TurnItem[] = [];
    let userIndex = 0;
    for (const msg of messages) {
      if (msg.role !== "user") continue;
      items.push({
        id: turnIdForUserIndex(userIndex),
        text: msg.content,
        index: userIndex,
      });
      userIndex += 1;
    }
    return items;
  }, [messages]);

  const { pinToBottom } = useStickyBottom(busy, messages, progress);

  useEffect(() => {
    if (turns.length === 0 || typeof IntersectionObserver === "undefined") {
      setActiveTurnId(null);
      return;
    }
    const observers: IntersectionObserver[] = [];
    for (const turn of turns) {
      const node = document.getElementById(turn.id);
      if (!node) continue;
      const observer = new IntersectionObserver(
        entries => {
          for (const entry of entries) {
            if (entry.isIntersecting) setActiveTurnId(turn.id);
          }
        },
        { rootMargin: "-20% 0px -55% 0px", threshold: 0.1 },
      );
      observer.observe(node);
      observers.push(observer);
    }
    return () => observers.forEach(observer => observer.disconnect());
  }, [turns, messages]);

  const lastResult = [...messages].reverse().find((m): m is { role: "assistant"; content: string; result: AnalysisResult } => m.role === "assistant");

  function jumpToTurn(id: string) {
    const node = document.getElementById(id);
    if (!node || typeof node.scrollIntoView !== "function") return;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveTurnId(id);
  }

  async function ask(event?: FormEvent) {
    if (event) event.preventDefault();
    if (!question.trim() || busy) return;
    const q = question.trim();
    setQuestion("");
    setBusy(true); setError("");
    setProgress("正在分析…");
    setProgressSteps([]);
    pinToBottom();
    setMessages(prev => [
      ...prev,
      { role: "user", content: q },
      { role: "progress", steps: [], current: "正在分析…" },
    ]);
    if (messages.filter(m => m.role === "user").length === 0 && onTitleChange) onTitleChange(q.slice(0, 40));
    requestAnimationFrame(() => {
      endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
    });
    try {
      const result = demoMode
        ? DEMO_RESULT
        : await streamAnalysis(token, q, label => {
            setProgress(label);
            setProgressSteps(prev => (prev.includes(label) ? prev : [...prev, label]));
            setMessages(prev => {
              const next = [...prev];
              let idx = -1;
              for (let i = next.length - 1; i >= 0; i -= 1) {
                if (next[i].role === "progress") { idx = i; break; }
              }
              if (idx >= 0) {
                const current = next[idx] as { role: "progress"; steps: string[]; current: string };
                const steps = current.steps.includes(label) ? current.steps : [...current.steps, label];
                next[idx] = { role: "progress", steps, current: label };
              }
              return next;
            });
          }, conversationId);
      setMessages(prev => {
        const withoutProgress = prev.filter(m => m.role !== "progress");
        return [...withoutProgress, { role: "assistant", content: result.answer, result }];
      });
    } catch (reason) {
      setMessages(prev => prev.filter(m => m.role !== "progress"));
      setError(reason instanceof Error ? reason.message : "分析失败");
    } finally { setBusy(false); setProgress(""); setProgressSteps([]); }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  }

  const isEmpty = messages.length === 0;
  let userOrdinal = 0;

  return <div className={`analysis-layout${lastResult ? " has-evidence" : ""}`}>
    <main className="workspace">
      <header className="workspace-header">
        <div><p className="eyebrow">Analysis workspace</p><h1>{isEmpty ? "新分析" : (messages.find(m => m.role === "user")?.content.slice(0, 30) || "分析")}</h1></div>
        <div className="source-pill"><span className="status-dot" /> ClickHouse · Olist</div>
      </header>
      {lastResult && lastResult.result.evidence.metrics.length > 0 && (
        <div className="condition-strip">
          <span>{lastResult.result.evidence.metrics[0].label}</span>
          <span>{lastResult.result.evidence.time_window.start} → {lastResult.result.evidence.time_window.end}</span>
          <span>按{lastResult.result.columns[0] || "维度"}</span>
        </div>
      )}
      <section className="conversation" style={isEmpty ? { flex: 1, display: "flex", alignItems: "center", justifyContent: "center" } : undefined}>
        {isEmpty ? (
          <div style={{ textAlign: "center" }}>
            <div className="brand-mark" style={{ margin: "0 auto 16px", width: 56, height: 56, fontSize: 24 }}>M</div>
            <h2 style={{ marginBottom: 8 }}>输入业务问题开始分析</h2>
            <p style={{ color: "#697386", margin: 0 }}>支持按指标、维度、时间范围查询</p>
          </div>
        ) : (
          <>
            {messages.map((msg, i) => {
              if (msg.role === "user") {
                const id = turnIdForUserIndex(userOrdinal);
                userOrdinal += 1;
                return (
                  <div key={i} id={id} className="user-message turn-anchor">
                    <span>AN</span><p>{msg.content}</p>
                  </div>
                );
              }
              if (msg.role === "progress") {
                return <ProgressBubble key={i} steps={msg.steps.length ? msg.steps : progressSteps} current={msg.current || progress} />;
              }
              return <AnswerCard key={i} result={msg.result} />;
            })}
            <div ref={endRef} className="conversation-end" aria-hidden />
          </>
        )}
      </section>
      <TurnNav turns={turns} activeId={activeTurnId} onJump={jumpToTurn} />
      <form className="composer" onSubmit={ask}>
        <textarea value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={handleKeyDown} placeholder="输入业务问题…" aria-label="业务问题" disabled={busy} />
        <div className="composer-footer"><span>{progress || "Enter 发送 · Shift + Enter 换行"}</span><button className="send" disabled={busy}>{busy ? "分析中" : "发送"}</button></div>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
    </main>
    {lastResult && <EvidenceRail result={lastResult.result} />}
  </div>;
}

type GovernanceTable = { title: string; description: string; columns: string[]; rows: string[][] };

const sectionData: Record<Exclude<View, "analysis">, GovernanceTable> = {
  metrics: { title: "指标目录", description: "查看与维护已发布的业务口径、版本和负责人。写操作需 admin 账号。", columns: ["指标", "版本", "Owner", "状态"], rows: [] },
  schema: { title: "数据目录", description: "浏览 ClickHouse 模型、字段与 Schema 快照。", columns: ["模型", "Snapshot", "来源", "状态"], rows: [] },
  sources: { title: "数据源", description: "管理可查询仓库、连接状态与当前激活的数据平面。", columns: ["名称", "Provider", "Database", "状态"], rows: [] },
  knowledge: { title: "知识库", description: "管理业务术语、治理规则与向量索引。上传需 admin。", columns: ["文档", "类型", "Embedding", "状态"], rows: [] },
  audit: { title: "查询审计", description: "检查查询用户、SQL、证据和资源消耗。点击行查看详情。", columns: ["Trace", "用户", "耗时", "结果"], rows: [] },
  evaluation: { title: "评测报告", description: "追踪语义解析、SQL 和结果正确性。", columns: ["指标", "案例", "得分", "状态"], rows: [] },
  memory: { title: "个人记忆", description: "查看、确认或删除只属于你的分析偏好。", columns: ["类型", "内容", "状态", "操作"], rows: [] },
};

function MetricEditor({
  token,
  initial,
  onSaved,
  onError,
}: {
  token: string;
  initial?: MetricDefinition | null;
  onSaved: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const dims = String(form.get("allowed_dimensions") || "")
      .split(",")
      .map(item => item.trim())
      .filter(Boolean);
    const filters = String(form.get("filters") || "")
      .split("\n")
      .map(item => item.trim())
      .filter(Boolean);
    try {
      const saved = await upsertMetric(token, {
        name: String(form.get("name") || ""),
        version: Number(form.get("version") || 1),
        label: String(form.get("label") || ""),
        description: String(form.get("description") || ""),
        model: String(form.get("model") || ""),
        expression: String(form.get("expression") || ""),
        aggregation: String(form.get("aggregation") || "sum"),
        time_dimension: String(form.get("time_dimension") || ""),
        grain: String(form.get("grain") || "order_item"),
        allowed_dimensions: dims,
        filters,
        owner: String(form.get("owner") || "analytics"),
        status: String(form.get("status") || "published"),
      });
      onSaved(`指标 ${saved.label} 已保存`);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "保存指标失败（需 admin）");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="source-form" aria-labelledby="metric-form-title">
      <div><p className="eyebrow">Metric</p><h2 id="metric-form-title">{initial ? "编辑指标" : "新建 / 更新指标"}</h2></div>
      <form onSubmit={submit}>
        <label>Name<input name="name" defaultValue={initial?.name || ""} pattern="^[a-z][a-z0-9_]*$" required /></label>
        <label>Version<input name="version" type="number" defaultValue={initial?.version || 1} min={1} required /></label>
        <label>Label<input name="label" defaultValue={initial?.label || ""} required /></label>
        <label>Owner<input name="owner" defaultValue={initial?.owner || "analytics"} required /></label>
        <label>Model<input name="model" defaultValue={initial?.model || "analytics.fct_order_items"} required /></label>
        <label>Expression<input name="expression" defaultValue={initial?.expression || "price"} required /></label>
        <label>Aggregation<select name="aggregation" defaultValue={initial?.aggregation || "sum"}>
          <option value="sum">sum</option>
          <option value="avg">avg</option>
          <option value="count">count</option>
          <option value="count_distinct">count_distinct</option>
          <option value="max">max</option>
          <option value="min">min</option>
        </select></label>
        <label>Time dimension<input name="time_dimension" defaultValue={initial?.time_dimension || "order_purchase_at"} required /></label>
        <label>Grain<input name="grain" defaultValue={initial?.grain || "order_item"} required /></label>
        <label>Status<select name="status" defaultValue={initial?.status || "published"}>
          <option value="published">published</option>
          <option value="draft">draft</option>
          <option value="deprecated">deprecated</option>
        </select></label>
        <label>Allowed dimensions（逗号分隔）<input name="allowed_dimensions" defaultValue={(initial?.allowed_dimensions || []).join(", ")} /></label>
        <label>Description<textarea name="description" defaultValue={initial?.description || ""} required style={{ minHeight: 64 }} /></label>
        <label>Filters（每行一条）<textarea name="filters" defaultValue={(initial?.filters || []).join("\n")} style={{ minHeight: 64 }} /></label>
        <button className="primary" disabled={busy}>{busy ? "保存中" : "保存指标"}</button>
      </form>
      <p className="muted">写操作需要登录 admin / admin-demo。</p>
    </section>
  );
}

function DataSourceForm({
  token,
  onCreated,
  onError,
}: {
  token: string;
  onCreated: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [provider, setProvider] = useState("clickhouse");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const config: Record<string, string | number> = provider === "clickhouse"
      ? {
          host: String(form.get("host") || ""),
          port: Number(form.get("port") || 8123),
          database: String(form.get("database") || ""),
          user: String(form.get("user") || ""),
          password: String(form.get("password") || ""),
        }
      : {
          project: String(form.get("project") || ""),
          dataset: String(form.get("dataset") || ""),
          maximum_bytes_billed: Number(form.get("maximum_bytes_billed") || 1000000000),
        };
    try {
      const source = await createDataSource(token, {
        name: String(form.get("name") || ""),
        provider,
        config,
        is_active: form.get("is_active") === "on",
      });
      event.currentTarget.reset();
      onCreated(`${source.name} 已创建`);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "创建数据源失败");
    } finally {
      setBusy(false);
    }
  }

  return <section className="source-form" aria-labelledby="source-form-title">
    <div><p className="eyebrow">Connection</p><h2 id="source-form-title">新增数据源</h2></div>
    <form onSubmit={submit}>
      <label>名称<input name="name" placeholder="Production ClickHouse" required /></label>
      <label>类型<select name="provider" value={provider} onChange={event => setProvider(event.target.value)}>
        <option value="clickhouse">ClickHouse</option>
        <option value="bigquery">BigQuery</option>
      </select></label>
      {provider === "clickhouse" ? <>
        <label>Host<input name="host" placeholder="clickhouse.internal" required /></label>
        <label>Port<input name="port" type="number" defaultValue="8123" required /></label>
        <label>Database<input name="database" defaultValue="analytics" required /></label>
        <label>User<input name="user" defaultValue="agent_readonly" required /></label>
        <label>Password<input name="password" type="password" autoComplete="new-password" /></label>
      </> : <>
        <label>Project<input name="project" placeholder="analytics-prod" required /></label>
        <label>Dataset<input name="dataset" placeholder="mart" required /></label>
        <label>Max bytes<input name="maximum_bytes_billed" type="number" defaultValue="1000000000" required /></label>
      </>}
      <label className="checkbox-line"><input name="is_active" type="checkbox" />设为当前数据源</label>
      <button className="primary" disabled={busy}>{busy ? "创建中" : "创建数据源"}</button>
    </form>
  </section>;
}

function DataSourceManager({
  token,
  refreshKey,
  onChanged,
  onError,
}: {
  token: string;
  refreshKey: number;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [items, setItems] = useState<DataSourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [activatingId, setActivatingId] = useState("");

  async function load() {
    setLoading(true);
    try {
      const payload = await getDataSources(token);
      setItems(payload.items);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "数据源加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [token, refreshKey]);

  async function activate(source: DataSourceItem) {
    setActivatingId(source.id);
    try {
      const updated = await activateDataSource(token, source.id);
      onChanged(`${updated.name} 已设为当前数据源`);
      await load();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "激活数据源失败");
    } finally {
      setActivatingId("");
    }
  }

  return <section className="source-manager" aria-labelledby="source-manager-title">
    <div><p className="eyebrow">Runtime source</p><h2 id="source-manager-title">数据源切换</h2></div>
    {loading ? <p className="muted">加载中</p> : <div className="source-list">
      {items.map(source => <article className="source-item" key={source.id}>
        <div><strong>{source.name}</strong><small>{source.provider} · {String(source.config.database ?? source.config.project ?? "-")}</small></div>
        {source.is_active ? <span className="table-status">Active</span> : <button onClick={() => activate(source)} disabled={activatingId === source.id}>{activatingId === source.id ? "切换中" : "设为当前"}</button>}
      </article>)}
    </div>}
  </section>;
}

function GovernancePage({ view, token }: { view: Exclude<View, "analysis">; token: string }) {
  const [data, setData] = useState<GovernanceTable>(sectionData[view]);
  const [metrics, setMetrics] = useState<MetricDefinition[]>([]);
  const [selectedMetric, setSelectedMetric] = useState<MetricDefinition | null>(null);
  const [schemaColumns, setSchemaColumns] = useState<Array<{ name: string; type: string }>>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [audits, setAudits] = useState<AuditItem[]>([]);
  const [selectedAudit, setSelectedAudit] = useState<AuditItem | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sourceRefreshKey, setSourceRefreshKey] = useState(0);
  const [query, setQuery] = useState("");

  async function load(cancelled: () => boolean = () => false) {
    setLoading(true);
    setError("");
    try {
      const next = await loadGovernanceTable(token, view);
      if (cancelled()) return;
      setData(next.table);
      setMetrics(next.metrics || []);
      setAudits(next.audits || []);
      setMemories(next.memories || []);
      if (view === "metrics") setSelectedMetric(prev => prev ? (next.metrics || []).find(item => item.name === prev.name) || null : null);
      if (view === "audit") setSelectedAudit(null);
    } catch (reason) {
      if (!cancelled()) {
        setData(sectionData[view]);
        setError(reason instanceof Error ? reason.message : "治理数据加载失败");
      }
    } finally {
      if (!cancelled()) setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setNotice("");
    setQuery("");
    setSelectedModel("");
    setSchemaColumns([]);
    void load(() => cancelled);
    return () => { cancelled = true; };
  }, [token, view]);

  async function runPrimaryAction() {
    setActionBusy(true);
    setError("");
    setNotice("");
    try {
      if (view === "sources") {
        const result = await testActiveDataSource(token);
        setNotice(`${result.source} 连接正常 · ${result.elapsed_ms} ms`);
      } else if (view === "schema") {
        const result = await refreshSchemas(token);
        setNotice(`Schema 已刷新 · ${result.columns} columns · ${result.snapshot.slice(0, 10)}`);
      } else if (view === "knowledge") {
        const result = await reindexKnowledge(token);
        setNotice(`知识索引已重建 · ${result.documents} documents · ${result.embedding_model}`);
      } else if (view === "evaluation") {
        const result = await runEvaluations(token);
        setNotice(`评测完成 · ${(result.pass_rate * 100).toFixed(1)}% · ${result.passed}/${result.cases}`);
      } else if (view === "metrics") {
        const result = await syncMetrics(token);
        setNotice(`已从 YAML 同步 ${result.metrics} 个指标`);
      } else {
        await load();
        setNotice("已刷新");
        return;
      }
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setActionBusy(false);
    }
  }

  async function openModel(model: string) {
    setSelectedModel(model);
    try {
      const detail = await describeSchema(token, model);
      setSchemaColumns(detail.columns);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取字段失败");
    }
  }

  async function onUploadKnowledge(file: File | null) {
    if (!file) return;
    setActionBusy(true);
    setError("");
    try {
      const result = await uploadKnowledge(token, file);
      setNotice(`已上传 ${file.name}${result.documents != null ? ` · 索引 ${result.documents} 文档` : ""}`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上传失败（需 admin）");
    } finally {
      setActionBusy(false);
    }
  }

  const actionLabel =
    view === "sources" ? "测试连接"
      : view === "schema" ? "刷新 Schema"
        : view === "knowledge" ? "重建索引"
          : view === "evaluation" ? "运行评测"
            : view === "metrics" ? "从 YAML 同步"
              : "刷新";

  const filteredRows = data.rows.filter(row =>
    !query.trim() || row.some(cell => cell.toLowerCase().includes(query.trim().toLowerCase())),
  );

  return <main className="governance"><header><div><p className="eyebrow">Governance</p><h1>{data.title}</h1><p>{data.description}</p></div><button className="primary" onClick={runPrimaryAction} disabled={actionBusy}>{actionBusy ? "处理中" : actionLabel}</button></header>
    {notice && <p className="success" role="status">{notice}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="catalog-toolbar">
      <input
        placeholder={`搜索${data.title}`}
        aria-label={`搜索${data.title}`}
        value={query}
        onChange={e => setQuery(e.target.value)}
      />
      {view === "knowledge" && (
        <label className="upload-btn">
          上传文档
          <input
            type="file"
            accept=".md,.txt,.yaml,.csv"
            hidden
            onChange={e => void onUploadKnowledge(e.target.files?.[0] || null)}
          />
        </label>
      )}
    </div>
    <div className="catalog-table"><table><thead><tr>{data.columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{loading ? <tr><td colSpan={data.columns.length}>加载中</td></tr> : filteredRows.length === 0 ? <tr><td colSpan={data.columns.length}>暂无数据</td></tr> : filteredRows.map((row, i) => {
      const clickable = view === "metrics" || view === "schema" || view === "audit";
      return (
        <tr
          key={i}
          className={clickable ? "clickable-row" : undefined}
          onClick={() => {
            if (view === "metrics") {
              const metric = metrics.find(item => item.label === row[0] || item.name === row[0]);
              setSelectedMetric(metric || null);
            } else if (view === "schema") {
              void openModel(row[0]);
            } else if (view === "audit") {
              setSelectedAudit(audits.find(item => item.trace_id === row[0]) || null);
            }
          }}
        >
          {row.map((cell, j) => (
            <td key={j}>
              {view === "memory" && j === row.length - 1 ? (
                <span className="row-actions" onClick={e => e.stopPropagation()}>
                  {memories.find(item => item.id === cell)?.status === "pending" && (
                    <button onClick={async () => {
                      try {
                        await confirmMemory(token, cell);
                        setNotice("记忆已确认");
                        await load();
                      } catch (reason) {
                        setError(reason instanceof Error ? reason.message : "确认失败");
                      }
                    }}>确认</button>
                  )}
                  <button onClick={async () => {
                    try {
                      await deleteMemory(token, cell);
                      setNotice("记忆已删除");
                      await load();
                    } catch (reason) {
                      setError(reason instanceof Error ? reason.message : "删除失败");
                    }
                  }}>删除</button>
                </span>
              ) : j === row.length - 1 && view !== "memory" ? <span className="table-status">{cell}</span> : cell}
            </td>
          ))}
        </tr>
      );
    })}</tbody></table></div>

    {view === "metrics" && selectedMetric && (
      <section className="detail-card">
        <div><p className="eyebrow">Selected</p><h2>{selectedMetric.label}</h2></div>
        <dl className="detail-grid">
          <div><dt>name</dt><dd><code>{selectedMetric.name}</code></dd></div>
          <div><dt>model</dt><dd><code>{selectedMetric.model}</code></dd></div>
          <div><dt>expression</dt><dd><code>{selectedMetric.expression}</code></dd></div>
          <div><dt>aggregation</dt><dd>{selectedMetric.aggregation}</dd></div>
          <div><dt>dimensions</dt><dd>{(selectedMetric.allowed_dimensions || []).join(", ") || "—"}</dd></div>
          <div><dt>filters</dt><dd>{(selectedMetric.filters || []).join(" · ") || "—"}</dd></div>
          <div><dt>description</dt><dd>{selectedMetric.description}</dd></div>
        </dl>
      </section>
    )}
    {view === "metrics" && (
      <MetricEditor
        token={token}
        initial={selectedMetric}
        onSaved={message => { setNotice(message); void load(); }}
        onError={setError}
      />
    )}

    {view === "schema" && selectedModel && (
      <section className="detail-card">
        <div><p className="eyebrow">Model</p><h2>{selectedModel}</h2></div>
        <div className="catalog-table"><table><thead><tr><th>字段</th><th>类型</th></tr></thead>
          <tbody>{schemaColumns.length === 0 ? <tr><td colSpan={2}>无字段或未加载</td></tr> : schemaColumns.map(col => <tr key={col.name}><td>{col.name}</td><td><code>{col.type}</code></td></tr>)}</tbody>
        </table></div>
      </section>
    )}

    {view === "audit" && selectedAudit && (
      <section className="detail-card">
        <div><p className="eyebrow">Audit</p><h2>{selectedAudit.trace_id}</h2></div>
        <p className="muted">{selectedAudit.created_at} · {selectedAudit.user_id} · {selectedAudit.evidence.row_count} rows · {selectedAudit.evidence.elapsed_ms} ms</p>
        <pre className="sql-view">{selectedAudit.normalized_sql}</pre>
        <dl className="detail-grid">
          <div><dt>metrics</dt><dd>{(selectedAudit.evidence.metrics || []).map(m => m.label || m.name).join(", ") || "—"}</dd></div>
          <div><dt>time</dt><dd>{selectedAudit.evidence.time_window?.start} → {selectedAudit.evidence.time_window?.end}</dd></div>
          <div><dt>schema refs</dt><dd>{(selectedAudit.evidence.schema_refs || []).join(", ") || "—"}</dd></div>
        </dl>
      </section>
    )}

    {view === "sources" && <>
      <DataSourceManager token={token} refreshKey={sourceRefreshKey} onChanged={message => { setNotice(message); setSourceRefreshKey(value => value + 1); void load(); }} onError={setError} />
      <DataSourceForm token={token} onCreated={message => { setNotice(message); setSourceRefreshKey(value => value + 1); void load(); }} onError={setError} />
    </>}
  </main>;
}

async function loadGovernanceTable(token: string, view: Exclude<View, "analysis">): Promise<{
  table: GovernanceTable;
  metrics?: MetricDefinition[];
  audits?: AuditItem[];
  memories?: MemoryItem[];
}> {
  if (view === "metrics") {
    const metrics = await getMetrics(token);
    return {
      table: {
        ...sectionData.metrics,
        rows: metrics.map(metric => [metric.label, `v${metric.version}`, metric.owner, metric.status]),
      },
      metrics,
    };
  }
  if (view === "schema") {
    const schema = await getSchemas(token);
    return {
      table: {
        ...sectionData.schema,
        rows: schema.models.map(model => [model, schema.snapshot?.slice(0, 10) ?? "-", schema.source ?? "-", "active"]),
      },
    };
  }
  if (view === "knowledge") {
    const knowledge = await getKnowledge(token);
    return {
      table: {
        ...sectionData.knowledge,
        rows: knowledge.documents.map(document => [
          document.source_ref,
          document.source_type,
          knowledge.embedding_model,
          String(document.metadata.schema_snapshot ?? "indexed").slice(0, 12),
        ]),
      },
    };
  }
  if (view === "sources") {
    const sources = await getDataSources(token);
    return {
      table: {
        ...sectionData.sources,
        rows: sources.items.map(item => [
          item.name,
          item.provider,
          String(item.config.database ?? item.config.project ?? "-"),
          item.is_active ? "Active" : item.status,
        ]),
      },
    };
  }
  if (view === "audit") {
    const audit = await getAudit(token);
    return {
      table: {
        ...sectionData.audit,
        rows: audit.items.map(item => [
          item.trace_id,
          item.user_id,
          `${item.evidence.elapsed_ms} ms`,
          `${item.evidence.row_count} rows`,
        ]),
      },
      audits: audit.items,
    };
  }
  if (view === "evaluation") {
    const evaluation = await getEvaluations(token);
    if (!evaluation.latest) {
      return {
        table: {
          ...sectionData.evaluation,
          rows: [["olist-core-v1", String(evaluation.cases), "-", "未运行"]],
        },
      };
    }
    return {
      table: {
        ...sectionData.evaluation,
        rows: [
          [evaluation.latest.suite, String(evaluation.latest.cases), `${(evaluation.latest.pass_rate * 100).toFixed(1)}%`, `${evaluation.latest.passed}/${evaluation.latest.cases}`],
          ["Metric", String(evaluation.latest.cases), `${(evaluation.latest.metric_accuracy * 100).toFixed(1)}%`, "Accuracy"],
          ["Dimension", String(evaluation.latest.cases), `${(evaluation.latest.dimension_accuracy * 100).toFixed(1)}%`, "Accuracy"],
          ["Refusal", String(evaluation.latest.cases), `${(evaluation.latest.refusal_accuracy * 100).toFixed(1)}%`, "Accuracy"],
        ],
      },
    };
  }
  if (view === "memory") {
    const memories = await getMemories(token);
    return {
      table: {
        ...sectionData.memory,
        rows: memories.items.map(item => [
          item.kind,
          JSON.stringify(item.value).slice(0, 80),
          item.status,
          item.id,
        ]),
      },
      memories: memories.items,
    };
  }
  return { table: sectionData[view] };
}

export default function App({ demoMode = false }: { demoMode?: boolean }) {
  const [token, setToken] = useState(() => demoMode ? "demo-token" : localStorage.getItem("metriclens_token") ?? "");
  const [view, setView] = useState<View>("analysis");
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeId, setActiveId] = useState("");

  async function loadConversations() {
    if (!token || token === "demo-token") return;
    try {
      const data = await getConversations(token);
      setConversations(data.items);
    } catch { /* governance tabs will show their own errors */ }
  }

  useEffect(() => { loadConversations(); }, [token]);

  function handleLogout() {
    localStorage.removeItem("metriclens_token");
    setToken("");
    setActiveId("");
    setConversations([]);
    setView("analysis");
  }

  async function handleNewAnalysis() {
    setView("analysis");
    setActiveId("");
    try {
      const conv = await createConversation(token, "新分析");
      setActiveId(conv.id);
      setConversations(prev => [conv, ...prev]);
    } catch {
      setActiveId("local-" + Math.random().toString(36).slice(2, 10));
    }
  }

  async function handleTitleChange(title: string) {
    setConversations(prev => prev.map(c => c.id === activeId ? { ...c, title } : c));
    if (!activeId.startsWith("local-") && token !== "demo-token") {
      try {
        await renameConversation(token, activeId, title);
      } catch { /* keep local title */ }
    }
  }

  async function handleRenameConversation(id: string, title: string) {
    setConversations(prev => prev.map(c => c.id === id ? { ...c, title } : c));
    if (!id.startsWith("local-") && token !== "demo-token") {
      try {
        await renameConversation(token, id, title);
      } catch { /* ignore */ }
    }
  }

  async function handleDeleteConversation(id: string) {
    if (!window.confirm("确定删除该会话？")) return;
    setConversations(prev => prev.filter(c => c.id !== id));
    if (activeId === id) setActiveId("");
    if (!id.startsWith("local-") && token !== "demo-token") {
      try {
        await deleteConversation(token, id);
      } catch { /* ignore */ }
    }
  }

  const content = useMemo(() => {
    if (view === "analysis") {
      if (!activeId) {
        return <div className="analysis-layout">
          <main className="workspace">
            <header className="workspace-header"><div><p className="eyebrow">Analysis workspace</p><h1>MetricLens</h1></div><div className="source-pill"><span className="status-dot" /> ClickHouse · Olist</div></header>
            <section className="conversation" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ textAlign: "center" }}>
                <div className="brand-mark" style={{ margin: "0 auto 16px", width: 56, height: 56, fontSize: 24 }}>M</div>
                <h2 style={{ marginBottom: 8 }}>点击「＋ 新建分析」开始</h2>
                <p style={{ color: "#697386", margin: 0 }}>创建一个新会话来查询业务指标</p>
              </div>
            </section>
          </main>
        </div>;
      }
      return <Analysis key={activeId} token={token} conversationId={activeId} demoMode={demoMode} onTitleChange={handleTitleChange} />;
    }
    return <GovernancePage view={view} token={token} />;
  }, [view, token, activeId, demoMode]);

  if (!token) return <Login onLogin={setToken} />;
  return <div className="app-shell">
    <Sidebar
      view={view} setView={setView}
      conversations={conversations}
      activeId={activeId}
      onNewAnalysis={handleNewAnalysis}
      onSelectConversation={id => { setActiveId(id); setView("analysis"); }}
      onRenameConversation={handleRenameConversation}
      onDeleteConversation={handleDeleteConversation}
      onLogout={handleLogout}
    />
    {content}
  </div>;
}
