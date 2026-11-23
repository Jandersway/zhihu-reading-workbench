export type Mode = 'demo' | 'live'
export type Kind = 'recommend' | 'research'
export type ResearchScope = 'original' | 'library'
export type Grade = 0 | 1 | 2 | null
export type Source = 'ai_proxy' | 'human'
export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'
export interface Candidate { candidate_id: string; title: string; excerpt: string; url: string }
export interface Score { candidate_id: string; home_rank: number; focus_rank: number; score: number; matched_terms: string[] }
export interface Recommendation {
  candidate_id: string; title: string; url: string; why: string; value: string; drawback: string;
  preference_reason: string; evidence_quote: string; preference_quote: string;
}
export interface EvidenceGraph {
  nodes: { id: string; type: string; quote?: string; text?: string; url?: string; source_sha256?: string; start?: number; end?: number }[]
  edges: { source: string; target: string; relation: string }[]
}
export interface StageCost { model: { calls: number; total_tokens: number | null; elapsed_ms: number }; tools: { calls: number; elapsed_ms: number } }
export interface Result {
  run_id: string; status: string; completed: boolean; reason: string; elapsed_ms: number;
  usage_totals: { total_tokens: number }; usage_complete: boolean; skill?: { name: string; sha256: string };
  candidates: Candidate[]; candidate_ranking: { method: string; items: Score[] };
  recommendations: Recommendation[]; graph: EvidenceGraph | null;
  reviews: { approved: boolean; issues: string[]; checks: { evidence_id: string; explanation: string }[] | null }[];
  cost_breakdown: { by_stage: Record<string, StageCost> };
}
export interface TaskSummary { id: string; status: TaskStatus; focus: string; mode: Mode; kind: Kind; created_at: string }
export interface ResearchResult {
  answer: string; completed: boolean; citation_warning: boolean; skill: { name: string; sha256: string } | null;
  generation?: { mode: 'local' } | { mode: 'live'; provider: string; model: string };
  sources: { answer_id: string; title: string; citation_id: string; url: string; library_item_id?: string; quotes?: string[] }[];
  trace: { round: number; tool: string; ok: boolean; elapsed_ms: number }[];
}
export interface TaskBase extends TaskSummary {
  error: string | null;
  research_scope: ResearchScope;
  library_item_id: string | null;
  events: { seq: number; stage: string; detail: string; at: string }[];
}
export type Task = (TaskBase & { kind: 'recommend'; result: Result | null }) | (TaskBase & { kind: 'research'; result: ResearchResult | null })
export interface CaseSummary { case_id: string; focus: string; count: number; labeled: number }
export interface Form { case_id: string; items: (Candidate & { grade: Grade })[] }
export interface Comparison { candidates: Candidate[]; rankings: Record<string, string[]>; scores: Score[]; home_order_source: string }
export interface Pair { paired_cases: number; homepage_mean: number | null; challenger_mean: number | null }
export interface Metrics { cases: number; candidate_count: number; labeled: number; cutoffs: Record<string, { metrics: Record<string, Pair> }> }
export interface LibrarySummary { id: string; title: string; author: string; source: 'zhihu' | 'note' | 'demo'; url: string | null; updated_at: string }
export interface LibraryItem extends LibrarySummary { body: string }
export interface ReadingPreview extends LibraryItem { saved: boolean; preview_id: string | null }
export interface ReaderTarget { url: string; title: string }
export interface LibraryComment { key: string; author: string; content: string; likes: number; replies: LibraryComment[] }
export interface ModelStatus {
  configured: boolean
  live_allowed: boolean
  live_enabled: boolean
  provider: string | null
  model: string | null
  providers: { id: string; label: string; default_model: string; host: string }[]
}
