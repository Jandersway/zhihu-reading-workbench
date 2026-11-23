import type { Grade } from './agent'

export interface Topic { id: string; label: string }
export interface ReadingType { id: string; label: string }
export interface CollectionStatus { count: number; collections: number; truncated: boolean; updated_at: string; semantic_ready: boolean }
export interface CollectionChange {
  candidate_id: string; title: string; before: number; after: number; favorite_title: string | null; bonus: number;
}
interface GoalSuggestion {
  source: 'query' | 'history' | 'feed' | 'none';
  text: string; signal?: 'library' | 'rating'; title?: string; count?: number;
}
export interface SearchItem {
  candidate_id: string; title: string; excerpt: string; author: string; url: string;
  topic: string | null; reading_type: string | null; format_terms: string[];
  matched_terms: string[];
  favorite_title: string | null; favorite_origin?: 'library' | 'archive' | 'note';
  suspected_promotion?: boolean;
  official_favorite_title?: string | null; official_favorite_bonus?: number;
  suggested_terms: string[];
  known_grade: Grade; my_grade: Grade; rated_title: string | null;
  dislike_title?: string | null;
  original_rank: number; rule_rank: number; model_rank: number; rank: number;
  goal_band: number; semantic_title: string | null; semantic_cosine: number | null;
  model_contributions: Record<string, number>;
}
export interface TrainingStatus {
  eligible_batches: number; eligible_pairs: number; min_batches: number; min_pairs: number;
  validation_batches: number; validation: { zhihu: number; rules: number; cold_start: number; learned: number } | null;
}
interface RankingModel extends TrainingStatus {
  stage: 'rules' | 'semantic' | 'learned';
  semantic_status: 'ready' | 'unavailable' | 'not_synced';
  experiment_available: boolean;
}
export interface SearchResult {
  batch_id: string;
  query: string; source: 'feed' | 'search'; items: SearchItem[];
  filtered_ads: number;
  goal: string; goal_origin: GoalSuggestion['source'] | 'manual'; goal_suggestion: GoalSuggestion | null;
  zhihu_collections?: CollectionStatus | null;
  ranking_model: RankingModel;
}

export interface FeedSnapshot {
  result: SearchResult
  batchRatings: Record<string, Grade>
  comparison: CollectionChange[]
  training: TrainingStatus | null
  nextLearnedAvailable: boolean
  lastHidden: { item: SearchItem; index: number; batch: string } | null
}
