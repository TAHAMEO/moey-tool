export type Me = { id: string; email: string; display_name: string; role: string; csrf_token: string; local?: boolean };

export type JobSummary = {
  id: string;
  type: string;
  status: "queued" | "running" | "waiting" | "paused" | "failed" | "completed" | "cancelled";
  label: string;
  stage: string | null;
  progress: number;
  attempts: number;
  max_attempts: number;
  error: { message?: string; code?: string } | null;
  project_id: string | null;
  created_at: string;
  finished_at: string | null;
  waiting?: {
    type?: string; label?: string; packages?: ApprovalPackage[]; schedule?: RequestedSchedule | null;
    rights?: "third_party_download" | null; flags?: { package_id: string; reason: string }[];
  } | null;
};

/** A publish time asked for in chat ("…and post them tomorrow at 6pm"). */
export type RequestedSchedule = { at: string; every_minutes: number | null; assumed_time?: boolean; timezone?: string; phrase?: string };

export type JobDetail = JobSummary & {
  result: Record<string, unknown> | null;
  events: { at: string; level: string; stage: string | null; message: string }[];
  children: JobSummary[];
};

export type ApprovalPackage = {
  package_id: string;
  platform: string;
  rank: number;
  title: string;
  qc_passed: boolean;
  review_words: number;
};

export type Card =
  | { type: "job"; job_id: string; project_id: string | null; title?: string }
  | { type: "settings_suggestion"; section: string; patch: Record<string, unknown> }
  | { type: "memory_matches"; items: { id: string; memory: string }[] }
  | { type: "link"; href: string; label: string };

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta: { intent?: string; actions?: { step: string; detail: string }[]; cards?: Card[]; attachments?: string[] };
  created_at?: string;
};

export type Conversation = { id: string; title: string; updated_at: string };

export type ProjectSummary = {
  id: string;
  title: string;
  status: string;
  status_label: string;
  analysis_level: string | null;
  created_at: string;
  updated_at: string;
  clips: number;
  thumbnail_url: string | null;
  source: { platform: string; url: string | null; access_status: string; title?: string } | null;
  job: JobSummary | null;
};

export type Render = {
  id: string;
  aspect: string;
  status: string;
  video_url: string | null;
  srt_url: string | null;
  vtt_url: string | null;
  thumbnail_url: string | null;
  captions_burned: boolean;
  quality: { passed?: boolean; checks: { name: string; ok: boolean; detail: string; blocking: boolean }[]; loudness?: Record<string, number> };
  subtitles: { passed?: boolean; issues?: { type: string; detail: string }[]; review_words?: { word: string; reason: string; time: number }[]; max_cps?: number; misspelled?: string[]; language?: string };
  edits: { removed_silence_s?: number; crop_strategies?: string[]; punch_in_cuts?: number; notes?: string[] };
};

export type Publication = {
  id: string;
  status: string;
  url: string | null;
  external_id: string | null;
  published_at: string | null;
  scheduled_at: string | null;
  last_error: string | null;
  metrics: Record<string, number> | null;
  unavailable_metrics: Record<string, string> | null;
};

export type Package = {
  id: string;
  platform: "youtube" | "instagram";
  status: string;
  title: string;
  description: string;
  tags: string[];
  caption: string;
  hashtags: string[];
  privacy: string;
  metadata_method: string;
  thumbnail_url: string | null;
  scheduled_at: string | null;
  /** youtube: YouTube's own scheduler · held: CLIPFORGE publishes at the time · now: not scheduled */
  schedule_mode: "youtube" | "held" | "now";
  publications: Publication[];
};

export type ScheduleItem = {
  package_id: string; platform: "youtube" | "instagram"; status: "scheduled" | "missed"; title: string; clip_rank: number;
  project_id: string; project_title: string; scheduled_at: string | null; owner_time: boolean;
  mode: "youtube" | "held" | "now"; privacy: string; uploaded: boolean; remote_status: string | null; on_hold: boolean;
  last_error: string | null; thumbnail_url: string | null;
};

export type ReadyPost = {
  package_id: string; platform: "youtube" | "instagram"; status: string; title: string; clip_rank: number;
  project_id: string; project_title: string; privacy: string; duration_s: number; published_at: string | null;
  thumbnail_url: string | null;
};

export type ScheduleOverview = { timezone: string; items: ScheduleItem[]; ready: ReadyPost[]; recent: ReadyPost[] };

export type Clip = {
  id: string;
  rank: number;
  start_s: number;
  end_s: number;
  title: string;
  hook: string;
  transcript: string;
  scores: Record<string, number>;
  total_score: number;
  reasons: string[];
  status: string;
  scoring_method: string;
  renders: Render[];
  packages: Package[];
  hook_score?: number | null;
  loop_score?: number | null;
  shorts?: ClipShorts;
  transcript_url?: string | null;
};

export type ClipShorts = {
  hook?: { status: "ok" | "improved" | "held"; text_hook: string | null; changes: string[];
           before: { score: number; reasons: string[] }; after: { score: number; reasons: string[]; parts: Record<string, number> } };
  loop?: { end: number; changes: string[]; bridge_s: number; bridged?: boolean; visual_similarity?: number | null;
           assessment: { score: number; loopable: boolean; reasons: string[] } };
  publish_gate_result?: { result: "approved" | "awaiting_approval" | "held" | "draft" | "not_approved"; reasons: string[]; at: string };
  original_bounds?: [number, number];
  retention_plan?: { at: number | null; text: string | null; text_kind: "own_words" | "framing" | null; zoom: [number, number] | null;
                     changes: string[]; skipped: string | null };
  first_frame_text?: string;
  packaging_variants?: PackagingVariant[];
  seo?: { transcript_asset_id: string; keyword: string; keywords: string[]; words: number };
};

export type PackagingVariant = {
  rank: number; title: string; first_frame_text: string | null; template: string; score: number; parts: Record<string, number>;
  youtube_description: string; instagram_caption: string; hashtags: { youtube: string[]; instagram: string[] };
  reasons: string[]; burned: boolean;
};

export type AccessDetail = {
  state: string;
  platform: string;
  method: string | null;
  capabilities: { metadata: boolean; transcript: boolean; media: boolean; live: boolean };
  metadata: Record<string, unknown>;
  live_status: string | null;
  owned: boolean | null;
  reasons: string[];
  alternatives: { action: string; label: string; href: string }[];
  checks: { check: string; result: string }[];
};

export type ProjectDetail = ProjectSummary & {
  instruction: string;
  request: Record<string, unknown>;
  limitations: string[];
  errors: { code: string; message: string }[];
  access: AccessDetail | null;
  clips: Clip[];
  jobs: JobSummary[];
};

export type ProviderModel = {
  id: string;
  model_id: string;
  enabled: boolean;
  quality_tier: number;
  capabilities: Record<string, unknown>;
  context_window: number | null;
  price_in_per_mtok: number | null;
  price_out_per_mtok: number | null;
  price_source: string;
  calls: number;
  latency_ms: number | null;
  success_rate: number;
};

export type ProviderConnection = {
  id: string;
  provider_type: string;
  provider_label: string;
  name: string;
  base_url: string | null;
  enabled: boolean;
  is_local: boolean;
  status: string;
  last_error: string | null;
  last_tested_at: string | null;
  cooldown_until: string | null;
  key_hint: string | null;
  usage_30d: { calls: number; cost_usd: number; tokens: number };
  models: ProviderModel[];
};

export type ProvidersResponse = {
  connections: ProviderConnection[];
  available_types: { type: string; label: string; needs_key: boolean; needs_base_url: boolean }[];
};

export type ConnectionTest = { ok: boolean; models_discovered: number; chat_ok: boolean | null; error: string | null; probe_model?: string; discovery_error?: string };

export type UsageSummary = {
  today_usd: number;
  month_usd: number;
  budgets: { daily: number; monthly: number; per_job: number };
  cache_hits_month: number;
  failed_calls_month: number;
  calls_with_estimated_prices: number;
  by_model: { provider_type: string; model: string; calls: number; cost_usd: number; input_tokens: number; output_tokens: number }[];
  by_task: { task: string; calls: number; cost_usd: number }[];
};

export type AISettings = {
  local_first: boolean; allow_paid_apis: boolean; daily_budget_usd: number; monthly_budget_usd: number; per_job_budget_usd: number;
  consensus_max_providers: number; llm_rerank_clips: boolean;
};
export type PublishingSettings = {
  policy: "approval_required" | "auto_publish" | "draft_only"; platforms: ("youtube" | "instagram")[]; default_privacy: "private" | "unlisted" | "public";
  max_per_day_youtube: number; max_per_day_instagram: number; upload_youtube_captions: boolean; instagram_share_to_feed: boolean;
  youtube_category_id: string; youtube_playlist_id: string | null; made_for_kids: boolean;
  timezone: string; late_grace_minutes: number;
};
export type SourceSettings = { public_downloads: boolean };
export type ClipSettings = {
  default_count: number; min_seconds: number; max_seconds: number; aspects: ("9:16" | "1:1" | "16:9")[];
  caption_style: "bold_pop" | "karaoke_fill" | "clean_lower" | "boxed" | "kinetic"; burn_captions: boolean; languages: string[]; remove_silences: boolean;
  min_silence_to_cut_s: number; punch_in_zoom: boolean; loudness_lufs: number; music_volume_db: number; use_licensed_music: boolean;
};
export type ShortsSettings = {
  hook_scorer: boolean; hook_reconstruct: boolean; hook_gate: boolean; hook_threshold: number; max_first_line_s: number;
  runway_search_s: number; text_hook: boolean; text_hook_seconds: number; tension_first: boolean; loop_seam: boolean;
  loop_threshold: number; loop_search_s: number; prefer_max_s: number; max_s: number; min_s: number;
  rehook: boolean; rehook_from_s: number; rehook_to_s: number; rehook_min_clip_s: number; rehook_zoom: boolean; rehook_text: boolean;
  rehook_framing: boolean; kinetic_captions: boolean; seo_transcript: boolean; packaging_variants: boolean;
  title_min_chars: number; title_max_chars: number; first_frame_words: number;
};
export type BrandSettings = { accent_color: string; watermark_library_item_id: string | null; watermark_position: string; channel_name: string };
export type RetentionSettings = { source_media_days: number; work_files_days: number; live_buffer_minutes: number; keep_renders_days: number };
export type AllSettings = {
  ai: AISettings; publishing: PublishingSettings; sources: SourceSettings; control: { paused: boolean; emergency_stop: boolean; disabled_integrations: string[] };
  clips: ClipSettings; shorts: ShortsSettings; brand: BrandSettings; analytics: { collection_offsets_hours: number[] }; retention: RetentionSettings; ui: { show_diagnostics: boolean };
};

export type SocialAccount = {
  id: string; platform: "youtube" | "instagram" | "google_drive"; display_name: string; external_id: string; status: string;
  scopes: string[]; connected_at: string; token_expires_at: string | null; meta: Record<string, string | null>;
};
export type AppSource = "saved" | "built_in" | "env" | null;
export type GoogleAppStatus = {
  configured: boolean; source: AppSource; kind: "web" | "desktop" | null; client_id: string | null;
  redirect_uri: string | null; problem: string | null; web_redirect_uri: string;
};
export type InstagramAppStatus = {
  configured: boolean; source: AppSource; app_id: string | null; relay_url: string | null; redirect_uri: string | null;
  relay_target: string | null; problem: string | null; direct_redirect_uri: string; https: boolean;
};

export type ConnectionsResponse = {
  accounts: SocialAccount[];
  apps: { google: GoogleAppStatus; instagram: InstagramAppStatus; youtube_api_key: boolean };
  youtube_quota_remaining: number;
};

export type LiveSession = {
  id: string; status: string; protocol: string; port: number; source_id: string | null; connections: number; segments: number;
  stream_seconds: number; candidates: { start: number; end: number; score: number; text?: string }[]; retention_minutes: number;
  started_at: string | null; ended_at: string | null; created_at: string; publish_url?: string; note?: string;
};

export type LibraryItem = { id: string; kind: string; title: string; tags: string[]; license_note: string; rights_confirmed: boolean; url: string | null; created_at: string };

export type MemoryItem = { id: string; memory: string; score?: number; metadata?: Record<string, unknown> | null; created_at?: string; updated_at?: string | null };

export type KnowledgeItem = {
  id: string; topic: string | null; question: string; answer: string; providers: string[]; agreement: string | null; disagreements: string[];
  confidence: number; verification_status: string; reuse_count: number; responses: { provider?: string; model?: string; answer?: string }[] | null; created_at: string;
};

export type LearningRecord = {
  id: string; kind: string; finding: string; evidence: Record<string, unknown>; platform: string | null; confidence: number;
  limitations: string; causal_claim: boolean; strategy_id: string | null; next_experiment: string | null;
  related_publications: number; status: string; created_at: string;
};
export type Strategy = { id: string; version: number; name: string; status: string; params: Record<string, unknown>; rationale: string; parent_id: string | null; created_at: string; activated_at: string | null };
export type Experiment = {
  id: string; name: string; hypothesis: string; platform: string | null; status: string; allocation: number; metric: string; min_samples_per_arm: number;
  result: Record<string, unknown> | null; started_at: string; ended_at: string | null; control_strategy_id: string; variant_strategy_id: string;
};
export type AnalyticsSummary = {
  text: string;
  platforms: Record<string, { published_with_data: number; median_views: number; median_engagement_rate: number | null;
    top: { title: string; views: number; age_hours: number }[]; bottom: { title: string; views: number; age_hours: number }[] }>;
};
export type PublicationAnalytics = {
  id: string; platform: string; status: string; title: string; url: string | null; published_at: string | null; last_error: string | null;
  snapshots: { at: string; age_hours: number; metrics: Record<string, number>; unavailable: Record<string, string> }[];
};

export type BatchRecipe = {
  instruction: string; count: number | null; min_seconds?: number | null; max_seconds?: number | null;
  platforms: ("youtube" | "instagram")[]; aspects?: string[] | null; caption_style?: string | null; focus?: string | null;
  publish: "drafts" | "approve" | "auto"; privacy: "private" | "unlisted" | "public" | null;
  schedule: { mode: "now" | "slots"; times: string[]; start_date: string | null };
};

export type WatchFolderInfo = {
  id: string; name: string; path: string; enabled: boolean; include_subfolders: boolean; after_processing: "keep" | "move";
  last_scan_at: string | null; last_error: string | null; waiting_files: number; batch_id: string | null;
};

export type BatchSummary = {
  id: string; name: string; origin: "upload" | "folder"; status: "open" | "running" | "paused" | "completed" | "cancelled";
  recipe: BatchRecipe; recipe_summary: string; next_slot: string | null; created_at: string;
  counts: { total: number; waiting: number; processing: number; ready: number; scheduled: number; published: number; attention: number; skipped: number };
  folder: WatchFolderInfo | null;
};

export type FolderListing = {
  root: string; path: string; exists: boolean; writable: boolean; videos: number;
  folders: { name: string; path: string; videos: number }[]; watched: WatchFolderInfo[];
};
