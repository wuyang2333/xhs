export type PlatformId = "xhs" | "douyin" | "kuaishou" | "weibo" | "xianyu" | "taobao";

export type PlatformMeta = {
  id: PlatformId;
  name_cn: string;
  name_en: string;
  enabled: boolean;
  status: "enabled" | "coming_soon";
  accent_color: string;
  icon: string;
};

export type Paginated<T> = {
  total: number;
  page: number;
  page_size: number;
  items: T[];
};

export type DashboardOverview = {
  platform: "xhs";
  today_crawls: number;
  saved_notes: number;
  pending_publishes: number;
  healthy_accounts: number;
  at_risk_accounts: number;
  comment_count?: number;
  total_engagement?: number;
  hot_topics: Array<{ keyword: string; notes: number; engagement: number }>;
  recent_activity: Array<{ type: string; title: string; status: string }>;
};

export type PlatformUser = {
  id: number;
  username: string;
  is_admin: boolean;
};

export type AdminUser = {
  id: number;
  username: string;
  is_admin: boolean;
  created_at: string | null;
  shares_user_id?: number | null;
};

export type AuthTokens = {
  access_token: string;
  refresh_token?: string;
  token_type: "bearer";
};

export type AuthPayload = AuthTokens & {
  user: PlatformUser;
};

export type PlatformAccount = {
  id: number;
  platform: PlatformId;
  sub_type: "pc" | "creator" | "android" | null;
  external_user_id?: string;
  nickname: string;
  avatar_url?: string;
  status: "active" | "healthy" | "expired" | "risk" | "unknown" | string;
  status_message?: string;
  profile?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  action?: "created" | "updated" | string;
};

export type AndroidDevice = {
  serial: string;
  state: string;
  model?: string;
  product?: string;
  transport_id?: string;
};

export type AndroidSetupStatus = {
  adb_available: boolean;
  adb_path: string;
  script_available: boolean;
  script_path: string;
  ready_for_binding: boolean;
  ready_for_publishing: boolean;
  platform: string;
  adb_installable: boolean;
  adb_install_task_id: string;
  ime_serial: string;
  ime_installed: boolean;
  ime_checked: boolean;
};

export type AdbInstallTask = {
  id: string;
  status: "pending" | "running" | "success" | "failed" | string;
  progress: number;
  message: string;
  error: string;
  adb_path: string;
  already_installed: boolean;
  updated_at: string;
};

export type AndroidImeInstallResult = {
  serial: string;
  installed: boolean;
  message: string;
};

export type XhsQrLoginSession = {
  session_id: number;
  status: "pending" | "scanned" | "confirmed" | "expired" | string;
  qr_url: string;
  qr_image_data_url?: string;
  account?: PlatformAccount | null;
  creator_account?: PlatformAccount | null;
};

export type XhsSearchNote = {
  note_id: string;
  note_url?: string;
  title: string;
  content: string;
  author_id: string;
  author_name: string;
  author_avatar: string;
  cover_url: string;
  likes: number;
  collects: number;
  comments: number;
  shares: number;
  type: string;
  timestamp?: number | string;
  image_urls?: string[];
  video_url?: string;
  video_addr?: string;
  tags?: string[];
  raw: Record<string, unknown>;
};

export type XhsNoteSearchResponse = {
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  items: XhsSearchNote[];
  raw: Record<string, unknown>;
};

export type XhsSearchOptions = {
  account_id: number;
  keyword: string;
  page?: number;
  sort_type_choice?: number;
  note_type?: number;
  note_time?: number;
  note_range?: number;
  pos_distance?: number;
  geo?: string;
};

export type XhsDataCrawlMode = "note_urls" | "search" | "comments" | "user";

export type XhsDataCrawlPayload = {
  account_id: number;
  mode: XhsDataCrawlMode;
  urls?: string[];
  keyword?: string;
  user_url?: string;
  pages?: number;
  max_notes?: number;
  time_sleep?: number;
  fetch_comments?: boolean;
  sort_type_choice?: number;
  note_type?: number;
  note_time?: number;
  note_range?: number;
  pos_distance?: number;
  geo?: string;
};

export type XhsDataCrawlItem = {
  source: string;
  status: "success" | "failed" | string;
  error: string;
  note?: XhsSearchNote | null;
  comments: NoteComment[];
  comment_count: number;
};

export type XhsDataCrawlResponse = {
  task: TaskRecord;
  total: number;
  success_count: number;
  failed_count: number;
  items: XhsDataCrawlItem[];
};

export type SavedNote = {
  id: number;
  platform: PlatformId;
  platform_account_id: number;
  note_id: string;
  title: string;
  content: string;
  author_name: string;
  raw_json?: Record<string, unknown>;
  asset_urls?: string[];
  cover_url?: string;
  video_url?: string;
  video_addr?: string;
  created_at: string;
  tags?: Tag[];
};

export type AnalyticsTopContent = {
  id: number;
  note_id: string;
  title: string;
  author_name: string;
  created_at: string;
  likes: number;
  collects: number;
  comments: number;
  shares: number;
  engagement: number;
};

export type AnalyticsHotTopic = {
  keyword: string;
  notes: number;
  engagement: number;
};

export type AnalyticsCommentInsight = {
  total_comments: number;
  question_count: number;
  top_terms: Array<{ term: string; count: number }>;
  top_comments: Array<{
    id: number;
    note_id: number;
    user_name: string;
    content: string;
    like_count: number;
  }>;
};

export type BenchmarkTopNote = AnalyticsTopContent;

export type BenchmarkItem = {
  target_id: number;
  target_type: "account" | "brand" | string;
  name: string;
  value: string;
  status: string;
  last_refreshed_at?: string | null;
  matched_notes: number;
  total_engagement: number;
  average_engagement: number;
  top_notes: BenchmarkTopNote[];
};

export type BenchmarkOverview = {
  total_targets: number;
  matched_notes: number;
  total_engagement: number;
  average_engagement: number;
  items: BenchmarkItem[];
};

export type NoteAsset = {
  id: number;
  note_id: number;
  asset_type: "image" | "video" | string;
  url: string;
  local_path: string;
  download_url?: string;
  sort_order?: number;
};

export type NoteComment = {
  id: number;
  note_id: number;
  comment_id: string;
  user_name: string;
  user_id?: string | null;
  content: string;
  like_count: number;
  parent_comment_id?: string | null;
  created_at_remote?: string | null;
  raw_json?: Record<string, unknown>;
};

export type Tag = {
  id: number;
  name: string;
  color: string;
};

export type TagPayload = {
  name: string;
  color?: string;
};

export type BatchTagNotesPayload = {
  note_ids: number[];
  tag_ids: number[];
  mode: "replace" | "add" | "remove";
};

export type BatchTagNotesResponse = {
  updated_count: number;
  items: SavedNote[];
};

export type BatchCreateDraftsPayload = {
  note_ids: number[];
  intent?: "rewrite" | "publish" | string;
};

export type BatchCreateDraftsResponse = {
  created_count: number;
  items: Draft[];
};

export type BenchmarkCreateDraftsResponse = BatchCreateDraftsResponse;

export type NotesExportPayload = {
  note_ids: number[];
  format?: "json" | "csv";
};

export type NotesExportResponse = {
  exported_count: number;
  file_name: string;
  file_path: string;
  download_url: string;
};

export type AnalyticsReportPayload = {
  note_ids?: number[];
  format?: "json";
};

export type AnalyticsReportResponse = {
  report_type: "operations";
  generated_at: string;
  note_count: number;
  file_name: string;
  file_path: string;
  download_url: string;
  summary: {
    note_count: number;
    total_engagement: number;
    comment_count: number;
    top_topics: AnalyticsHotTopic[];
    top_notes: AnalyticsTopContent[];
    benchmark_count: number;
  };
};

export type SaveNotesResponse = {
  saved_count: number;
  items: SavedNote[];
};

export type Draft = {
  id: number;
  platform_account_id?: number | null;
  platform: PlatformId;
  title: string;
  body: string;
  tags?: { id?: string; name: string }[];
  source_note_id?: number | null;
  created_at: string;
};

export type CreateDraftPayload = {
  platform: "xhs";
  platform_account_id?: number | null;
  source_note_id?: number;
  title?: string;
  body?: string;
  intent?: "rewrite" | "publish" | string;
};

export type ModelType = "text" | "image";

export type ModelConfig = {
  id: number;
  name: string;
  model_type: ModelType;
  provider: string;
  model_name: string;
  base_url: string;
  has_api_key: boolean;
  is_default: boolean;
};

export type DescribeOptionsResponse = {
  items: ModelConfig[];
  current_id: number | null;
};

export type ModelConfigPayload = {
  name: string;
  model_type: ModelType;
  provider: string;
  model_name: string;
  base_url: string;
  api_key: string;
  is_default: boolean;
};

export type RewriteDraftPayload = {
  draft_id: number;
  instruction?: string;
  /** 从指令库选定或自填的指令，直接取代后端默认人设，不叠加 */
  system_prompt_override?: string;
};

export type GenerateNotePayload = {
  platform?: "xhs";
  platform_account_id?: number;
  topic: string;
  reference?: string;
  instruction?: string;
  generate_images?: boolean;
  image_count?: number;
  image_prompt?: string;
  existing_image_urls?: string[];
  /** 从指令库选定或自填的指令，直接取代后端默认人设，不叠加 */
  system_prompt_override?: string;
};

export type GenerateTitlePayload = {
  title?: string;
  body: string;
  count?: number;
  /** 分层组合后的完整指令 */
  instruction?: string;
  /** 从指令库选定或自填的指令，直接取代后端默认人设，不叠加 */
  system_prompt_override?: string;
};

export type GenerateTagsPayload = {
  title?: string;
  body: string;
  count?: number;
  /** 从指令库选定或自填的指令，直接取代后端默认人设，不叠加 */
  system_prompt_override?: string;
};

export type PolishTextPayload = {
  text: string;
  instruction?: string;
  /** 从指令库选定或自填的指令，直接取代后端默认人设，不叠加 */
  system_prompt_override?: string;
};

export type AssetFolder = {
  id: number;
  name: string;
  parent_id: number | null;
  cover_asset_id: number | null;
  created_at: string;
};

export type Product = {
  id: number;
  name: string;
  description: string;
  created_at: string;
};

export type AssetScene = {
  id: number;
  name: string;
  created_at: string;
};

export type AssetTag = {
  id: number;
  name: string;
  color: string;
  created_at: string;
};

export type MediaAsset = {
  id: number;
  asset_type: "image" | "video";
  url: string;
  local_path: string;
  original_name: string;
  mime_type: string;
  file_size: number;
  source: string;
  folder_id: number | null;
  product_id: number | null;
  scene_id: number | null;
  tag_ids: number[];
  tags: AssetTag[];
  sort_order: number;
  created_at: string;
  last_used_at: string | null;
};

export type DraftAsset = {
  id: number;
  draft_id: number;
  asset_type: "image" | "video" | string;
  url: string;
  local_path: string;
  media_asset_id: number | null;
  sort_order: number;
  is_cover: boolean;
};

export type GeneratedImageAsset = {
  id: number;
  draft_id?: number | null;
  prompt: string;
  model_name: string;
  params: Record<string, unknown>;
  file_path: string;
  created_at: string;
};

export type GenerateCoverPayload = {
  prompt: string;
  draft_id?: number;
  size?: string;
  style?: string;
};

export type GenerateImagePayload = {
  prompt: string;
  reference_images?: string[];
  save_to_assets?: boolean;
};

export type GenerateImageResult = {
  url: string;
  raw?: unknown;
  asset?: GeneratedImageAsset;
};

export type UserImageFile = {
  file_name: string;
  url: string;
  size: number;
};

export type DescribeImagePayload = {
  image_url: string;
  instruction?: string;
  /** 从指令库选定或自填的指令，直接取代后端默认人设，不叠加 */
  system_prompt_override?: string;
};

export type ImageUtilityFile = {
  file_name: string;
  file_path: string;
  download_url: string;
  width: number;
  height: number;
  media_type: string;
};

export type ComposeImagePayload = {
  title: string;
  body?: string;
  width?: number;
  height?: number;
  background_color?: string;
  accent_color?: string;
};

export type ResizeImagePayload = {
  source_file_name: string;
  width?: number;
  height?: number;
  mode?: "cover" | "contain";
  format?: "png" | "jpeg";
  quality?: number;
};

export type TaskRecord = {
  id: number;
  platform: PlatformId;
  task_type: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "exhausted" | string;
  progress: number;
  payload: Record<string, unknown>;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  error_type?: string | null;
  retry_count?: number;
  max_retries?: number;
  parent_task_id?: number | null;
  children?: TaskRecord[];
};

export type SchedulerStatus = {
  enabled: boolean;
  running: boolean;
  interval_seconds: number;
  jobs: Array<{
    id: string;
    next_run_time?: string | null;
  }>;
  recent_tasks: TaskRecord[];
};

export type RunDueTasksResponse = {
  executed_count: number;
  failed_count: number;
  items: PublishJob[];
};

export type MonitoringTarget = {
  id: number;
  platform: PlatformId;
  target_type: "keyword" | "account" | "brand" | "note_url" | string;
  name: string;
  value: string;
  status: "active" | "paused" | string;
  config: Record<string, unknown>;
  last_refreshed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type MonitoringTargetPayload = {
  target_type: "keyword" | "account" | "brand" | "note_url";
  name?: string;
  value: string;
  status?: "active" | "paused";
  config?: Record<string, unknown>;
};

export type MonitoringSnapshot = {
  id: number;
  target_id: number;
  payload: {
    matched_count?: number;
    total_engagement?: number;
    top_notes?: Array<{
      id: number;
      note_id: string;
      title: string;
      author_name: string;
      engagement: number;
    }>;
  };
  created_at: string;
};

export type MonitoringNote = {
  id: number;
  note_id: string;
  title: string;
  author_name: string;
  created_at: string;
  likes: number;
  collects: number;
  comments: number;
  shares: number;
  engagement: number;
};

export type MonitoringRefreshResponse = {
  target: MonitoringTarget;
  task: TaskRecord;
  snapshot: MonitoringSnapshot;
};

export type KeywordGroup = {
  id: number;
  platform: PlatformId;
  name: string;
  keywords: string[];
  created_at: string;
  updated_at: string;
};

export type KeywordGroupPayload = {
  platform?: PlatformId;
  name: string;
  keywords: string[];
};

export type KeywordGroupDetail = KeywordGroup & {
  trend: {
    total_matches: number;
    total_engagement: number;
    keywords: Array<{ keyword: string; notes: number; engagement: number }>;
    matched_notes: Array<{
      id: number;
      note_id: string;
      title: string;
      author_name: string;
      engagement: number;
      created_at: string;
    }>;
  };
};

export type PublishJob = {
  id: number;
  platform_account_id: number;
  source_draft_id?: number | null;
  platform: PlatformId;
  title: string;
  body: string;
  publish_mode: "immediate" | "scheduled" | string;
  publish_options?: PublishOptions;
  status: "pending" | "uploading" | "publishing" | "scheduled" | "published" | "failed" | "cancelled" | string;
  scheduled_at?: string | null;
  external_note_id: string;
  publish_error: string;
  published_at?: string | null;
  created_at: string;
};

export type PublishOptions = {
  topics?: string[];
  location?: string;
  privacy_type?: 0 | 1 | number;
  is_private?: boolean;
  product_keyword?: string;
  draft_tags?: Array<{ id?: string; name?: string }>;
  android_stage?: { serial?: string; image_count?: number; remote_paths?: string[] };
  android_review_image?: string;
};

export type SendDraftToPublishPayload = {
  platform_account_id?: number | null;
  publish_mode?: "immediate" | "scheduled";
  scheduled_at?: string | null;
  topics?: string[];
  location?: string | null;
  privacy_type?: 0 | 1 | null;
  is_private?: boolean | null;
};

export type PublishJobUpdatePayload = {
  title?: string;
  body?: string;
  platform_account_id?: number | null;
  publish_mode?: "immediate" | "scheduled";
  scheduled_at?: string | null;
  topics?: string[];
  location?: string | null;
  privacy_type?: 0 | 1 | null;
  is_private?: boolean | null;
  product_keyword?: string | null;
};

export type PublishAsset = {
  id: number;
  publish_job_id: number;
  asset_type: "image" | "video" | string;
  file_path: string;
  upload_status: "pending" | "uploading" | "uploaded" | "failed" | string;
  creator_media_id: string;
  upload_error: string;
  creator_upload_info: Record<string, unknown>;
};

export type PublishAssetPayload = {
  asset_type: "image" | "video";
  file_path: string;
};

export type AutoTask = {
  id: number;
  user_id: number;
  name: string;
  keywords: string[];
  /** 采集账号可选，不填表示跳过抓取、纯 AI 生成 */
  pc_account_id: number | null;
  creator_account_id: number;
  ai_instruction: string;
  status: "active" | "paused" | "completed" | string;
  last_run_at?: string | null;
  next_run_at?: string | null;
  total_published: number;
  created_at: string;
  schedule_type: "manual" | "daily" | "weekly" | "interval";
  schedule_time: string;
  schedule_days: string;
  schedule_interval_hours: number;
};

export type AutoTaskCreatePayload = {
  name: string;
  keywords: string[];
  /** 采集账号可选，不填表示跳过抓取、纯 AI 生成 */
  pc_account_id?: number | null;
  creator_account_id: number;
  ai_instruction?: string;
  schedule_type?: "manual" | "daily" | "weekly" | "interval";
  schedule_time?: string;
  schedule_days?: string;
  schedule_interval_hours?: number;
};

export type AutoTaskUpdatePayload = {
  name?: string;
  keywords?: string[];
  ai_instruction?: string;
  status?: "active" | "paused" | "completed";
  schedule_type?: "manual" | "daily" | "weekly" | "interval";
  schedule_time?: string;
  schedule_days?: string;
  schedule_interval_hours?: number;
};

/** AI 匹配到的账号，供用户复核。发布账号发错不可撤回，所以名称必须回传给前端展示。 */
export type AutoTaskMatchedAccount = {
  id: number;
  nickname: string;
  sub_type: string;
  role: "pc_account_id" | "creator_account_id";
  role_label: string;
};

/**
 * 一句话解析出的任务配置**建议值**。
 * 校验不通过的字段一律留空（字符串为 ""、账号为 null），对应说明在 `warnings` 里。
 */
export type AutoTaskProposal = {
  name: string;
  keywords: string[];
  pc_account_id: number | null;
  creator_account_id: number | null;
  ai_instruction: string;
  schedule_type: "" | "manual" | "daily" | "weekly" | "interval";
  schedule_time: string;
  schedule_days: string;
  schedule_interval_hours: number | null;
};

export type AutoTaskInstructionCandidate = {
  id: number;
  name: string;
  content: string;
};

export type AutoTaskParseResult = {
  description: string;
  proposal: AutoTaskProposal;
  matched_accounts: AutoTaskMatchedAccount[];
  warnings: string[];
  instruction_candidates: AutoTaskInstructionCandidate[];
  /** 后端恒为 true：提案从不落库，必须由用户确认后走创建接口。 */
  requires_confirmation: boolean;
};

export type AutoTaskRunResult = {
  auto_task: AutoTask;
  keyword: string;
  source_note: {
    note_id: string;
    title: string;
    likes: number;
    collects: number;
    comments: number;
  };
  draft: {
    id: number;
    title: string;
    body: string;
    created_at: string;
  };
  publish_job: {
    id: number;
    status: string;
    platform_account_id: number;
  } | null;
};

export type AppNotification = {
  id: number;
  title: string;
  body: string;
  level: "info" | "warning" | "error" | string;
  source_task_id?: number | null;
  source_type?: string | null;
  source_id?: number | null;
  read: boolean;
  created_at: string;
};

export type OperationProfile = {
  id: number; platform_account_id: number; homepage_url: string; positioning: string; target_audience: string;
  content_directions: string[]; daily_post_count: number; daily_generate_count: number; daily_publish_count: number; preferred_times: string[];
  goals: string[]; tone: string; forbidden_topics: string[]; review_required: boolean;
  status: "active" | "paused"; created_at: string; updated_at: string;
};

export type AccountContentItem = {
  id: number; note_id: string; title: string; content: string; views: number; likes: number;
  collects: number; comments: number; shares: number; source: string; created_at: string;
};

export type OperationPlan = {
  id: number; platform_account_id: number; plan_date: string; content_direction: string;
  topic: string; planned_at?: string | null; status: string; draft_id?: number | null;
  publish_job_id?: number | null; rationale: string; created_at: string; updated_at: string;
};

export type LearningInsight = {
  id: number; platform_account_id?: number | null; insight_type: string; title: string;
  conclusion: string; evidence: Record<string, unknown>; sample_size: number;
  confidence: number; status: string; created_at: string;
};

export type LearningOverview = {
  profile_count: number; active_insight_count: number; today_plan_count: number; published_count: number; synced_note_count: number;
};

export type ContentPerformanceItem = {
  id: number; platform_account_id: number; title: string; views: number; likes: number; collects: number;
  comments: number; shares: number; engagement: number; source: string; created_at: string;
};

export type StrategyProposal = {
  id: number; platform_account_id: number;
  status: "pending" | "approved" | "rejected" | "superseded" | string;
  source_note_count: number;
  strategy: {
    summary?: string; diagnosis?: string[]; positioning?: string; target_audience?: string;
    content_directions?: string[]; daily_post_count?: number; daily_generate_count?: number; daily_publish_count?: number; preferred_times?: string[];
    goals?: string[]; tone?: string; forbidden_topics?: string[];
    content_ratio?: Record<string, number>; title_rules?: string[];
    learning_insights?: Array<{ type: string; title: string; conclusion: string; confidence: number }>;
    seven_day_topics?: Array<{ direction: string; topic: string }>;
  };
  analysis_error: string; created_at: string; confirmed_at?: string | null;
};
