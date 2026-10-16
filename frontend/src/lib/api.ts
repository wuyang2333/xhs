import axios from "axios";
import { message } from "antd";

import { fallbackPlatforms } from "./platforms";
import type {
  AuthPayload,
  AssetFolder,
  AssetScene,
  AssetTag,
  AutoTask,
  AutoTaskCreatePayload,
  AutoTaskParseResult,
  AutoTaskRunResult,
  AutoTaskUpdatePayload,
  AnalyticsCommentInsight,
  AccountContentItem,
  AdminUser,
  AnalyticsHotTopic,
  AnalyticsReportPayload,
  AnalyticsReportResponse,
  AnalyticsTopContent,
  AdbInstallTask,
  AndroidDevice,
  AndroidImeInstallResult,
  AndroidSetupStatus,
  AppNotification,
  BenchmarkCreateDraftsResponse,
  BenchmarkOverview,
  BatchCreateDraftsPayload,
  BatchCreateDraftsResponse,
  BatchTagNotesPayload,
  BatchTagNotesResponse,
  ComposeImagePayload,
  ContentPerformanceItem,
  CreateDraftPayload,
  DashboardOverview,
  Draft,
  DraftAsset,
  DescribeImagePayload,
  DescribeOptionsResponse,
  GenerateNotePayload,
  GenerateCoverPayload,
  GenerateImagePayload,
  GenerateImageResult,
  GeneratedImageAsset,
  GenerateTagsPayload,
  GenerateTitlePayload,
  ImageUtilityFile,
  KeywordGroup,
  LearningInsight,
  LearningOverview,
  KeywordGroupDetail,
  KeywordGroupPayload,
  ModelConfig,
  ModelConfigPayload,
  ModelType,
  MediaAsset,
  MonitoringNote,
  MonitoringRefreshResponse,
  MonitoringSnapshot,
  MonitoringTarget,
  MonitoringTargetPayload,
  NoteAsset,
  NoteComment,
  NotesExportPayload,
  NotesExportResponse,
  Paginated,
  PlatformAccount,
  PlatformMeta,
  Product,
  OperationPlan,
  AccountOperationSummary,
  OperationPlanDraftResult,
  OperationProfile,
  PlatformUser,
  PublishAsset,
  PublishAssetPayload,
  PublishJob,
  PublishJobUpdatePayload,
  PolishTextPayload,
  ResizeImagePayload,
  RewriteDraftPayload,
  RunDueTasksResponse,
  SchedulerStatus,
  StrategyProposal,
  SavedNote,
  SaveNotesResponse,
  SendDraftToPublishPayload,
  Tag,
  UserImageFile,
  TagPayload,
  TaskRecord,
  XhsNoteSearchResponse,
  XhsDataCrawlItem,
  XhsDataCrawlPayload,
  XhsDataCrawlResponse,
  XhsSearchOptions,
  XhsSearchNote,
  XhsQrLoginSession
} from "../types";

const http = axios.create({
  baseURL: "/api",
  timeout: 120000,
});

const REFRESH_TOKEN_KEY = "spider_xhs_refresh_token";
let accessToken: string | null = null;

export type AuthCredentials = {
  username: string;
  password: string;
};

export function getAccessToken(): string | null {
  return accessToken;
}

export function hasRefreshToken(): boolean {
  return Boolean(window.localStorage.getItem(REFRESH_TOKEN_KEY));
}

function getRefreshToken(): string | null {
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

function setAccessToken(token: string | null): void {
  accessToken = token;
}

function persistAuthPayload(payload: AuthPayload): AuthPayload {
  setAccessToken(payload.access_token);
  if (payload.refresh_token) {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, payload.refresh_token);
  }
  return payload;
}

export function clearAuthTokens(): void {
  setAccessToken(null);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

http.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config as typeof error.config & { _authRetry?: boolean; _silent?: boolean };
    // The refresh endpoint itself must never trigger another refresh attempt.
    // Otherwise an expired LAN-browser token loops forever and the route guard
    // remains on "正在验证登录状态..." instead of falling back to /login.
    const isRefreshRequest = originalRequest?.url?.includes("/auth/refresh");
    if (error.response?.status !== 401 || originalRequest?._authRetry || isRefreshRequest || !getRefreshToken()) {
      if (!originalRequest?._silent) {
        const detail = error.response?.data?.detail;
        const msg = typeof detail === "string" ? detail : "请求失败，请稍后重试";
        message.error(msg);
      }
      return Promise.reject(error);
    }

    originalRequest._authRetry = true;
    try {
      const token = await refreshAccessToken();
      originalRequest.headers.Authorization = `Bearer ${token}`;
      return http(originalRequest);
    } catch (refreshError) {
      clearAuthTokens();
      message.error("登录已过期，请重新登录");
      return Promise.reject(refreshError);
    }
  }
);

export async function login(credentials: AuthCredentials): Promise<AuthPayload> {
  const response = await http.post<AuthPayload>("/auth/login", credentials);
  return persistAuthPayload(response.data);
}

export async function register(credentials: AuthCredentials): Promise<AuthPayload> {
  const response = await http.post<AuthPayload>("/auth/register", credentials);
  return persistAuthPayload(response.data);
}

export async function refreshAccessToken(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error("Missing refresh token");
  }

  const response = await http.post<{ access_token: string; token_type: "bearer" }>("/auth/refresh", {
    refresh_token: refreshToken
  });
  setAccessToken(response.data.access_token);
  return response.data.access_token;
}

export async function fetchMe(): Promise<PlatformUser> {
  const response = await http.get<PlatformUser>("/auth/me");
  return response.data;
}

export async function bootstrapAuth(): Promise<PlatformUser | null> {
  if (!getAccessToken() && hasRefreshToken()) {
    await refreshAccessToken();
  }
  if (!getAccessToken()) {
    return null;
  }
  return fetchMe();
}

export async function logout(): Promise<void> {
  try {
    await http.post("/auth/logout");
  } finally {
    clearAuthTokens();
  }
}

export async function fetchPlatforms(): Promise<PlatformMeta[]> {
  try {
    const response = await http.get<Paginated<PlatformMeta>>("/platforms");
    return response.data.items;
  } catch {
    return fallbackPlatforms;
  }
}

export async function fetchXhsOverview(): Promise<DashboardOverview> {
  const response = await http.get<DashboardOverview>("/xhs/analytics/overview");
  return response.data;
}

export async function fetchXhsTopContent(): Promise<{ items: AnalyticsTopContent[] }> {
  const response = await http.get<{ items: AnalyticsTopContent[] }>("/xhs/analytics/top-content");
  return response.data;
}

export async function fetchXhsHotTopics(): Promise<{ items: AnalyticsHotTopic[] }> {
  const response = await http.get<{ items: AnalyticsHotTopic[] }>("/xhs/analytics/hot-topics");
  return response.data;
}

export async function fetchXhsCommentInsights(): Promise<AnalyticsCommentInsight> {
  const response = await http.get<AnalyticsCommentInsight>("/xhs/analytics/comment-insights");
  return response.data;
}

export async function fetchXhsBenchmarks(): Promise<BenchmarkOverview> {
  const response = await http.get<BenchmarkOverview>("/xhs/analytics/benchmarks");
  return response.data;
}

export async function createBenchmarkDrafts(targetId: number, limit = 5): Promise<BenchmarkCreateDraftsResponse> {
  const response = await http.post<BenchmarkCreateDraftsResponse>(
    `/xhs/analytics/benchmarks/${targetId}/create-drafts`,
    null,
    { params: { limit } }
  );
  return response.data;
}

export async function createXhsAnalyticsReport(
  payload: AnalyticsReportPayload = { format: "json" }
): Promise<AnalyticsReportResponse> {
  const response = await http.post<AnalyticsReportResponse>("/xhs/analytics/reports", payload);
  return response.data;
}

export async function fetchAccounts(platform = "xhs"): Promise<PlatformAccount[]> {
  const response = await http.get<Paginated<PlatformAccount>>("/accounts", { params: { platform } });
  return response.data.items;
}

export type SavedNoteFilters = {
  platform?: string;
  q?: string;
  tag_id?: number;
  has_assets?: boolean;
  has_comments?: boolean;
  page_size?: number;
};

export async function fetchSavedNoteIds(platform = "xhs"): Promise<string[]> {
  const response = await http.get<{ items: string[] }>("/notes/ids", { params: { platform } });
  return response.data.items;
}

export async function fetchSavedNotes(platformOrFilters: string | SavedNoteFilters = "xhs"): Promise<Paginated<SavedNote>> {
  const params =
    typeof platformOrFilters === "string"
      ? { platform: platformOrFilters }
      : {
          platform: platformOrFilters.platform ?? "xhs",
          q: platformOrFilters.q || undefined,
          tag_id: platformOrFilters.tag_id,
          has_assets: platformOrFilters.has_assets,
          has_comments: platformOrFilters.has_comments,
          page_size: platformOrFilters.page_size,
        };
  const response = await http.get<Paginated<SavedNote>>("/notes", { params });
  return response.data;
}

export async function fetchSavedNote(noteId: number, silent = false): Promise<SavedNote> {
  const response = await http.get<SavedNote>(`/notes/${noteId}`, { _silent: silent } as never);
  return response.data;
}

export async function fetchSavedNoteAssets(noteId: number): Promise<Paginated<NoteAsset>> {
  const response = await http.get<Paginated<NoteAsset>>(`/notes/${noteId}/assets`);
  return response.data;
}

export async function addNoteAsset(noteId: number, payload: { asset_type: string; url?: string; local_path?: string }): Promise<NoteAsset> {
  const response = await http.post<NoteAsset>(`/notes/${noteId}/assets`, payload);
  return response.data;
}

export async function deleteNoteAsset(noteId: number, assetId: number): Promise<void> {
  await http.delete(`/notes/${noteId}/assets/${assetId}`);
}

export async function reorderNoteAssets(noteId: number, assetIds: number[]): Promise<void> {
  await http.put(`/notes/${noteId}/assets/reorder`, { asset_ids: assetIds });
}

export async function fetchSavedNoteComments(noteId: number, page = 1): Promise<Paginated<NoteComment>> {
  const response = await http.get<Paginated<NoteComment>>(`/notes/${noteId}/comments`, {
    params: { page, page_size: 50 }
  });
  return response.data;
}

export async function deleteSavedNote(noteId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/notes/${noteId}`);
  return response.data;
}

export async function fetchTags(): Promise<Paginated<Tag>> {
  const response = await http.get<Paginated<Tag>>("/tags");
  return response.data;
}

export async function createTag(payload: TagPayload): Promise<Tag> {
  const response = await http.post<Tag>("/tags", payload);
  return response.data;
}

export async function batchTagNotes(payload: BatchTagNotesPayload): Promise<BatchTagNotesResponse> {
  const response = await http.post<BatchTagNotesResponse>("/notes/batch-tag", payload);
  return response.data;
}

export async function batchCreateDraftsFromNotes(
  payload: BatchCreateDraftsPayload
): Promise<BatchCreateDraftsResponse> {
  const response = await http.post<BatchCreateDraftsResponse>("/notes/batch-create-drafts", payload);
  return response.data;
}

export async function exportSavedNotes(payload: NotesExportPayload): Promise<NotesExportResponse> {
  const response = await http.post<NotesExportResponse>("/notes/export", payload);
  return response.data;
}

export async function downloadExportFile(downloadUrl: string, fileName: string): Promise<void> {
  const endpoint = downloadUrl.startsWith("/api") ? downloadUrl.slice(4) : downloadUrl;
  const response = await http.get<Blob>(endpoint, { responseType: "blob" });
  const objectUrl = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export async function downloadMediaFile(downloadUrl: string, fileName: string): Promise<void> {
  return downloadExportFile(downloadUrl, fileName);
}

export type UploadedFile = {
  file_name: string;
  file_path: string;
  download_url: string;
  asset_type: "image" | "video";
  size: number;
};

export async function uploadAssetFile(file: File): Promise<UploadedFile> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await http.post<UploadedFile>("/files/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
  return response.data;
}

export async function createMediaObjectUrl(downloadUrl: string): Promise<string> {
  const endpoint = downloadUrl.startsWith("/api") ? downloadUrl.slice(4) : downloadUrl;
  const response = await http.get<Blob>(endpoint, { responseType: "blob" });
  return window.URL.createObjectURL(response.data);
}

export async function searchXhsNotes(payload: XhsSearchOptions): Promise<XhsNoteSearchResponse> {
  const response = await http.post<XhsNoteSearchResponse>("/xhs/pc/search/notes", payload);
  return response.data;
}

export async function discoverUserNotes(payload: {
  account_id: number;
  user_url: string;
  limit: number;
}): Promise<{ total: number; limit: number; items: XhsSearchNote[] }> {
  const response = await http.post("/xhs/pc/users/notes", payload);
  return response.data;
}

export async function crawlXhsDataStream(
  payload: XhsDataCrawlPayload,
  onItem: (index: number, item: XhsDataCrawlItem) => void,
  onProgress?: (message: string) => void,
  onError?: (message: string) => void,
): Promise<{ total: number; success_count: number; failed_count: number }> {
  const token = getAccessToken();
  const response = await fetch("/api/xhs/crawl/data", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail || `HTTP ${response.status}`);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response stream");
  const decoder = new TextDecoder();
  let buffer = "";
  let result = { total: 0, success_count: 0, failed_count: 0 };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6));
        if (event.type === "item") onItem(event.index, event.item);
        else if (event.type === "progress") onProgress?.(event.message);
        else if (event.type === "error") onError?.(event.message);
        else if (event.type === "done") result = { total: event.total, success_count: event.success_count, failed_count: event.failed_count };
      } catch { /* skip malformed events */ }
    }
  }
  return result;
}

export async function fetchXhsNoteDetail(payload: { account_id: number; url: string }): Promise<XhsSearchNote> {
  const response = await http.post<XhsSearchNote>("/xhs/pc/notes/detail", payload);
  return response.data;
}

export async function downloadXhsNoteOriginalImages(
  payload: { account_id: number; url: string },
  fileName: string,
): Promise<void> {
  const response = await http.post<Blob>("/xhs/pc/notes/download-images", payload, {
    responseType: "blob",
    timeout: 120000,
  });
  const objectUrl = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export async function fetchXhsNoteComments(payload: {
  account_id: number;
  note_url: string;
}): Promise<Paginated<NoteComment>> {
  const response = await http.post<{ total: number; items: NoteComment[] }>("/xhs/pc/notes/comments", payload);
  return {
    total: response.data.total,
    page: 1,
    page_size: response.data.items.length,
    items: response.data.items
  };
}

export async function saveXhsNotesToLibrary(payload: {
  account_id: number;
  notes: XhsSearchNote[];
}): Promise<SaveNotesResponse> {
  const response = await http.post<SaveNotesResponse>("/notes/batch-save", payload);
  return response.data;
}

export async function createDraftFromNote(payload: CreateDraftPayload): Promise<Draft> {
  const response = await http.post<Draft>("/drafts", payload);
  return response.data;
}

export async function fetchDrafts(platform = "xhs", accountId?: number): Promise<Paginated<Draft>> {
  const response = await http.get<Paginated<Draft>>("/drafts", { params: { platform, account_id: accountId } });
  return response.data;
}

export async function updateDraft(draftId: number, payload: { title?: string; body?: string; tags?: { id?: string; name: string }[] }): Promise<Draft> {
  const response = await http.patch<Draft>(`/drafts/${draftId}`, payload);
  return response.data;
}

export async function deleteDraft(draftId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/drafts/${draftId}`);
  return response.data;
}

export async function fetchDraftAssets(draftId: number): Promise<{ items: DraftAsset[] }> {
  const response = await http.get<{ items: DraftAsset[] }>(`/drafts/${draftId}/assets`);
  return response.data;
}

export async function addDraftAsset(
  draftId: number,
  payload: { asset_type?: string; url?: string; local_path?: string; media_asset_id?: number; is_cover?: boolean },
): Promise<DraftAsset> {
  const response = await http.post<DraftAsset>(`/drafts/${draftId}/assets`, payload);
  return response.data;
}

export async function deleteDraftAsset(draftId: number, assetId: number): Promise<void> {
  await http.delete(`/drafts/${draftId}/assets/${assetId}`);
}

export async function updateDraftAsset(draftId: number, assetId: number, payload: { url?: string; local_path?: string }): Promise<DraftAsset> {
  const response = await http.patch<DraftAsset>(`/drafts/${draftId}/assets/${assetId}`, payload);
  return response.data;
}

export async function setDraftAssetCover(draftId: number, assetId: number): Promise<DraftAsset> {
  return (await http.put<DraftAsset>(`/drafts/${draftId}/assets/${assetId}/cover`)).data;
}

export async function reorderDraftAssets(draftId: number, assetIds: number[]): Promise<void> {
  await http.put(`/drafts/${draftId}/assets/reorder`, { asset_ids: assetIds });
}

export async function sendDraftToPublish(draftId: number, payload: SendDraftToPublishPayload): Promise<PublishJob> {
  const response = await http.post<PublishJob>(`/drafts/${draftId}/send-to-publish`, payload);
  return response.data;
}

export async function rewriteDraftWithAi(payload: RewriteDraftPayload): Promise<Draft> {
  const response = await http.post<Draft>("/ai/rewrite-note", payload);
  return response.data;
}

export async function generateNoteWithAi(payload: GenerateNotePayload): Promise<Draft> {
  const response = await http.post<Draft>("/ai/generate-note", payload, { timeout: 600000 });
  return response.data;
}

export async function generateTitleOptions(payload: GenerateTitlePayload): Promise<{ items: string[] }> {
  const response = await http.post<{ items: string[] }>("/ai/generate-title", payload);
  return response.data;
}

export async function generateTagOptions(payload: GenerateTagsPayload): Promise<{ items: string[] }> {
  const response = await http.post<{ items: string[] }>("/ai/generate-tags", payload);
  return response.data;
}

export async function polishTextWithAi(payload: PolishTextPayload): Promise<{ text: string }> {
  const response = await http.post<{ text: string }>("/ai/polish-text", payload);
  return response.data;
}

export async function fetchGeneratedImageAssets(
  params?: { page?: number; page_size?: number },
): Promise<Paginated<GeneratedImageAsset>> {
  const response = await http.get<Paginated<GeneratedImageAsset>>("/ai/images/assets", { params });
  return response.data;
}

export async function deleteGeneratedImageAsset(assetId: number): Promise<void> {
  await http.delete(`/ai/images/assets/${assetId}`);
}

export async function deleteUserImage(fileName: string): Promise<void> {
  await http.delete(`/files/images/${fileName}`);
}

export async function generateCoverWithAi(payload: GenerateCoverPayload): Promise<GeneratedImageAsset> {
  const response = await http.post<GeneratedImageAsset>("/ai/images/generate-cover", payload);
  return response.data;
}

export async function generateImageWithAi(payload: GenerateImagePayload): Promise<GenerateImageResult> {
  const response = await http.post<GenerateImageResult>("/ai/images/generate", payload, { timeout: 180000 });
  return response.data;
}

export async function fetchUserImages(): Promise<{ items: UserImageFile[] }> {
  const response = await http.get<{ items: UserImageFile[] }>("/files/images");
  return response.data;
}

export async function describeImageWithAi(payload: DescribeImagePayload): Promise<{ text: string }> {
  const response = await http.post<{ text: string }>("/ai/images/describe", payload);
  return response.data;
}

export async function composeImageUtility(payload: ComposeImagePayload): Promise<ImageUtilityFile> {
  const response = await http.post<ImageUtilityFile>("/files/images/compose", payload);
  return response.data;
}

export async function resizeImageUtility(payload: ResizeImagePayload): Promise<ImageUtilityFile> {
  const response = await http.post<ImageUtilityFile>("/files/images/resize", payload);
  return response.data;
}

export async function fetchModelConfigs(modelType?: ModelType): Promise<Paginated<ModelConfig>> {
  const response = await http.get<Paginated<ModelConfig>>("/model-configs", {
    params: modelType ? { model_type: modelType } : undefined
  });
  return response.data;
}

export async function createModelConfig(payload: ModelConfigPayload): Promise<ModelConfig> {
  const response = await http.post<ModelConfig>("/model-configs", payload);
  return response.data;
}

export async function setDefaultModelConfig(configId: number): Promise<ModelConfig> {
  const response = await http.post<ModelConfig>(`/model-configs/${configId}/set-default`);
  return response.data;
}



export async function fetchAdminUsers(): Promise<{ items: AdminUser[] }> {
  const response = await http.get<{ items: AdminUser[] }>("/admin/users");
  return response.data;
}

export async function createSubAdmin(username: string, password: string): Promise<{ id: number; username: string; shares_user_id: number | null }> {
  const response = await http.post("/admin/users/sub-admin", { username, password });
  return response.data;
}

export async function resetUserPassword(userId: number, password: string): Promise<unknown> {
  const response = await http.post(`/admin/users/${userId}/reset-password`, { password });
  return response.data;
}

export async function setUserAdmin(userId: number, isAdmin: boolean): Promise<unknown> {
  const response = await http.post(`/admin/users/${userId}/admin`, { is_admin: isAdmin });
  return response.data;
}

export async function fetchDescribeOptions(): Promise<DescribeOptionsResponse> {
  const response = await http.get<DescribeOptionsResponse>("/model-configs/describe-options");
  return response.data;
}

export async function setDescribeDefault(modelConfigId: number): Promise<unknown> {
  const response = await http.post("/model-configs/describe-default", { model_config_id: modelConfigId });
  return response.data;
}

export async function updateModelConfig(configId: number, payload: Partial<ModelConfigPayload>): Promise<ModelConfig> {
  const response = await http.patch<ModelConfig>(`/model-configs/${configId}`, payload);
  return response.data;
}

export async function testModelConfig(configId: number): Promise<{ id: number; status: string; message: string }> {
  const response = await http.post<{ id: number; status: string; message: string }>(`/model-configs/${configId}/test`);
  return response.data;
}

export async function deleteModelConfig(configId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/model-configs/${configId}`);
  return response.data;
}

export async function fetchTasks(platform?: string): Promise<Paginated<TaskRecord>> {
  const response = await http.get<Paginated<TaskRecord>>("/tasks", {
    params: platform ? { platform } : undefined
  });
  return response.data;
}

export async function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  const response = await http.get<SchedulerStatus>("/tasks/scheduler/status");
  return response.data;
}

export async function cancelTask(taskId: number): Promise<TaskRecord> {
  const response = await http.post<TaskRecord>(`/tasks/${taskId}/cancel`);
  return response.data;
}

export async function retryTask(taskId: number): Promise<TaskRecord> {
  const response = await http.post<TaskRecord>(`/tasks/${taskId}/retry`);
  return response.data;
}

export async function runDueTasks(platform = "xhs"): Promise<RunDueTasksResponse> {
  const response = await http.post<RunDueTasksResponse>("/tasks/run-due", null, { params: { platform } });
  return response.data;
}

export async function fetchMonitoringTargets(): Promise<Paginated<MonitoringTarget>> {
  const response = await http.get<Paginated<MonitoringTarget>>("/xhs/monitoring/targets");
  return response.data;
}

export async function createMonitoringTarget(payload: MonitoringTargetPayload): Promise<MonitoringTarget> {
  const response = await http.post<MonitoringTarget>("/xhs/monitoring/targets", payload);
  return response.data;
}

export async function updateMonitoringTarget(
  targetId: number,
  payload: Partial<MonitoringTargetPayload>
): Promise<MonitoringTarget> {
  const response = await http.patch<MonitoringTarget>(`/xhs/monitoring/targets/${targetId}`, payload);
  return response.data;
}

export async function deleteMonitoringTarget(targetId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/xhs/monitoring/targets/${targetId}`);
  return response.data;
}

export async function refreshMonitoringTarget(targetId: number): Promise<MonitoringRefreshResponse> {
  const response = await http.post<MonitoringRefreshResponse>(`/xhs/monitoring/targets/${targetId}/refresh`);
  return response.data;
}

export async function fetchMonitoringSnapshots(targetId: number): Promise<{ target_id: number; items: MonitoringSnapshot[] }> {
  const response = await http.get<{ target_id: number; items: MonitoringSnapshot[] }>(
    `/xhs/monitoring/targets/${targetId}/snapshots`
  );
  return response.data;
}

export async function fetchMonitoringTargetNotes(targetId: number): Promise<{ target_id: number; items: MonitoringNote[] }> {
  const response = await http.get<{ target_id: number; items: MonitoringNote[] }>(
    `/xhs/monitoring/targets/${targetId}/notes`
  );
  return response.data;
}

export async function fetchKeywordGroups(platform = "xhs"): Promise<Paginated<KeywordGroup>> {
  const response = await http.get<Paginated<KeywordGroup>>("/keyword-groups", { params: { platform } });
  return response.data;
}

export async function createKeywordGroup(payload: KeywordGroupPayload): Promise<KeywordGroup> {
  const response = await http.post<KeywordGroup>("/keyword-groups", payload);
  return response.data;
}

export async function fetchKeywordGroup(groupId: number): Promise<KeywordGroupDetail> {
  const response = await http.get<KeywordGroupDetail>(`/keyword-groups/${groupId}`);
  return response.data;
}

export async function updateKeywordGroup(
  groupId: number,
  payload: Partial<KeywordGroupPayload>
): Promise<KeywordGroup> {
  const response = await http.patch<KeywordGroup>(`/keyword-groups/${groupId}`, payload);
  return response.data;
}

export async function deleteKeywordGroup(groupId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/keyword-groups/${groupId}`);
  return response.data;
}

export async function fetchPublishJobs(platform = "xhs", accountId?: number): Promise<Paginated<PublishJob>> {
  const response = await http.get<Paginated<PublishJob>>("/publish/jobs", { params: { platform, account_id: accountId } });
  return response.data;
}

export async function fetchPublishJob(jobId: number): Promise<PublishJob> {
  const response = await http.get<PublishJob>(`/publish/jobs/${jobId}`);
  return response.data;
}

export async function updatePublishJob(jobId: number, payload: PublishJobUpdatePayload): Promise<PublishJob> {
  const response = await http.patch<PublishJob>(`/publish/jobs/${jobId}`, payload);
  return response.data;
}

export async function publishJobToCreator(jobId: number): Promise<PublishJob> {
  const response = await http.post<PublishJob>(`/publish/jobs/${jobId}/publish`);
  return response.data;
}

export async function retryPublishJob(jobId: number): Promise<PublishJob> {
  const response = await http.post<PublishJob>(`/publish/jobs/${jobId}/retry`);
  return response.data;
}

export async function cancelPublishJob(jobId: number): Promise<PublishJob> {
  const response = await http.post<PublishJob>(`/publish/jobs/${jobId}/cancel`);
  return response.data;
}

export async function deletePublishJob(jobId: number): Promise<void> {
  await http.delete(`/publish/jobs/${jobId}`);
}

export async function fetchPublishAssets(jobId: number): Promise<Paginated<PublishAsset>> {
  const response = await http.get<Paginated<PublishAsset>>(`/publish/jobs/${jobId}/assets`);
  return response.data;
}

export async function addPublishAsset(jobId: number, payload: PublishAssetPayload): Promise<PublishAsset> {
  const response = await http.post<PublishAsset>(`/publish/jobs/${jobId}/assets`, payload);
  return response.data;
}

export async function deletePublishAsset(assetId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/publish/assets/${assetId}`);
  return response.data;
}

export async function uploadPublishAsset(assetId: number): Promise<PublishAsset> {
  const response = await http.post<PublishAsset>(`/publish/assets/${assetId}/upload`);
  return response.data;
}

export async function importXhsCookieAccount(payload: {
  sub_type: "pc" | "creator";
  cookie_string: string;
  sync_creator?: boolean;
}): Promise<PlatformAccount> {
  const response = await http.post<PlatformAccount>("/accounts/import-cookie", {
    platform: "xhs",
    ...payload
  });
  return response.data;
}

export async function checkAccount(accountId: number): Promise<PlatformAccount> {
  const response = await http.post<PlatformAccount>(`/accounts/${accountId}/check`);
  return response.data;
}

export async function fetchAndroidDevices(): Promise<AndroidDevice[]> {
  const response = await http.get<{ items: AndroidDevice[] }>("/accounts/android/devices");
  return response.data.items;
}

export async function fetchAndroidUsers(serial: string): Promise<import("../types").AndroidUserSpace[]> {
  const response = await http.get<{ items: import("../types").AndroidUserSpace[] }>("/accounts/android/users", { params: { serial } });
  return response.data.items;
}

export async function fetchAndroidSetupStatus(serial?: string): Promise<AndroidSetupStatus> {
  const response = await http.get<AndroidSetupStatus>("/accounts/android/setup-status", {
    params: serial ? { serial } : undefined,
  });
  return response.data;
}

export async function startAndroidAdbInstall(): Promise<AdbInstallTask> {
  const response = await http.post<AdbInstallTask>("/accounts/android/setup-adb");
  return response.data;
}

export async function fetchAndroidAdbInstallStatus(taskId: string): Promise<AdbInstallTask> {
  const response = await http.get<AdbInstallTask>(`/accounts/android/setup-adb/${taskId}`);
  return response.data;
}

export async function installAndroidIme(serial: string): Promise<AndroidImeInstallResult> {
  const response = await http.post<AndroidImeInstallResult>("/accounts/android/install-ime", { serial }, { timeout: 180000 });
  return response.data;
}

export async function bindAndroidAccount(payload: { serial: string; nickname?: string; android_user?: string }): Promise<PlatformAccount> {
  const response = await http.post<PlatformAccount>("/accounts/android/bind", payload);
  return response.data;
}

export async function prepareAndroidPublish(jobId: number): Promise<{ job: PublishJob; message: string }> {
  const response = await http.post<{ job: PublishJob; message: string }>(`/publish/jobs/${jobId}/android/prepare`);
  return response.data;
}

export async function fillAndroidPublish(jobId: number): Promise<{ job: PublishJob; review_image_url: string; message: string }> {
  const response = await http.post<{ job: PublishJob; review_image_url: string; message: string }>(`/publish/jobs/${jobId}/android/fill`);
  return response.data;
}

export async function confirmAndroidPublish(jobId: number): Promise<PublishJob> {
  const response = await http.post<PublishJob>(`/publish/jobs/${jobId}/android/confirm`);
  return response.data;
}

export async function publishAndroidDirect(jobId: number): Promise<PublishJob> {
  // A request may wait behind another note using the same physical phone.
  const response = await http.post<PublishJob>(`/publish/jobs/${jobId}/android/publish`, undefined, { timeout: 900000 });
  return response.data;
}

export async function deleteAccount(accountId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/accounts/${accountId}`);
  return response.data;
}

export async function createXhsPcQrLoginSession(payload?: {
  sync_creator?: boolean;
}): Promise<XhsQrLoginSession> {
  const response = await http.post<XhsQrLoginSession>("/xhs/login-sessions/pc/qrcode", payload ?? {});
  return response.data;
}

export async function createXhsCreatorQrLoginSession(): Promise<XhsQrLoginSession> {
  const response = await http.post<XhsQrLoginSession>("/xhs/login-sessions/creator/qrcode");
  return response.data;
}

export async function pollXhsLoginSession(sessionId: number): Promise<XhsQrLoginSession> {
  const response = await http.get<XhsQrLoginSession>(`/xhs/login-sessions/${sessionId}`);
  return response.data;
}

export async function sendXhsPhoneCode(payload: {
  sub_type: "pc" | "creator";
  phone: string;
  sync_creator?: boolean;
}): Promise<{ session_id: number; status: string; message: string }> {
  const response = await http.post<{ session_id: number; status: string; message: string }>(
    `/xhs/login-sessions/${payload.sub_type}/phone/send-code`,
    { phone: payload.phone, sync_creator: payload.sync_creator }
  );
  return response.data;
}

export async function confirmXhsPhoneLogin(payload: {
  sub_type: "pc" | "creator";
  session_id: number;
  phone: string;
  code: string;
  sync_creator?: boolean;
}): Promise<XhsQrLoginSession> {
  const response = await http.post<XhsQrLoginSession>(`/xhs/login-sessions/${payload.sub_type}/phone/confirm`, {
    session_id: payload.session_id,
    phone: payload.phone,
    code: payload.code,
    sync_creator: payload.sync_creator
  });
  return response.data;
}

export async function fetchNotifications(params?: { unread?: boolean; page?: number; page_size?: number }): Promise<Paginated<AppNotification>> {
  const response = await http.get<Paginated<AppNotification>>("/notifications", { params });
  return response.data;
}

export async function markNotificationRead(id: number): Promise<AppNotification> {
  const response = await http.post<AppNotification>(`/notifications/${id}/read`);
  return response.data;
}

export async function markAllNotificationsRead(): Promise<{ marked: number }> {
  const response = await http.post<{ marked: number }>("/notifications/read-all");
  return response.data;
}

// Auto Tasks (Auto Operations)

export async function fetchAutoTasks(accountId?: number): Promise<Paginated<AutoTask>> {
  const response = await http.get<Paginated<AutoTask>>("/auto-tasks", { params: { account_id: accountId } });
  return response.data;
}

export type PromptTemplateItem = {
  id: number; name: string; prompt_type: string; platform_account_id?: number | null;
  /** 与图片素材共用的产品 / 场景实体，用于指令分层组合 */
  product_id?: number | null; scene_id?: number | null;
  /** 官方模板随程序更新；改过正文的自动变为 false，升级不覆盖 */
  is_official?: boolean;
  scenes: string[]; tags: string[]; content: string; status: string; priority: number;
  current_version: number; source_type: string; source_ref: string; updated_at: string;
  /** 推荐接口返回的命中依据与分数；普通列表接口可能不包含。 */
  match_reasons?: string[]; match_score?: number;
};
export type BackupItem = {
  id: string; created_at: string; reason: string; valid: boolean;
  files: Array<{ name: string; size: number; sha256: string }>;
};
export type ObsidianSyncConfig = {
  configured: boolean; vault_path: string; last_scanned_at?: string | null; last_synced_at?: string | null;
};
export type ObsidianScanItem = {
  relative_path: string; name: string; prompt_type: string; platform_account_id?: number | null;
  scenes: string[]; tags: string[]; priority: number; source_hash: string; modified_at: number;
  action: "new" | "update" | "unchanged"; existing_prompt_id?: number | null; current_version: number;
  content_preview: string; content_length: number;
};
export type ObsidianScanResult = {
  config: ObsidianSyncConfig; items: ObsidianScanItem[];
  errors: Array<{ relative_path: string; message: string }>; total: number;
};
export type ObsidianImportResult = {
  created: number; updated: number; unchanged: number; backup_id?: string | null;
};
export async function fetchPromptTemplates(params?: {
  prompt_type?: string; account_id?: number; status?: string;
  product_id?: number; scene_id?: number; is_official?: boolean;
}): Promise<{ items: PromptTemplateItem[] }> {
  return (await http.get("/prompt-library", { params })).data;
}

export async function fetchPromptRecommendations(params: {
  prompt_type: string; account_id?: number; product_id?: number; scene_id?: number; scene?: string;
}): Promise<{ item: PromptTemplateItem | null; candidates: PromptTemplateItem[] }> {
  return (await http.get("/prompt-library/recommend/match", { params })).data;
}

export async function updatePromptTemplate(
  promptId: number,
  payload: Omit<PromptTemplateItem, "id" | "current_version" | "source_type" | "source_ref" | "updated_at"> & { change_note?: string },
): Promise<PromptTemplateItem> {
  return (await http.put(`/prompt-library/${promptId}`, payload)).data;
}

export type PromptVersionItem = {
  id?: number; version: number; content: string; change_note: string; created_by: string;
  approved?: boolean; is_current?: boolean; created_at: string;
};

export async function fetchPromptVersions(promptId: number): Promise<{ current_version: number; items: PromptVersionItem[] }> {
  return (await http.get(`/prompt-library/${promptId}/versions`)).data;
}

export async function archivePromptTemplate(promptId: number): Promise<PromptTemplateItem> {
  return (await http.post(`/prompt-library/${promptId}/archive`)).data;
}

export async function activatePromptTemplate(promptId: number): Promise<PromptTemplateItem> {
  return (await http.post(`/prompt-library/${promptId}/activate`)).data;
}

export async function deletePromptTemplate(promptId: number): Promise<void> {
  await http.delete(`/prompt-library/${promptId}`, { _silent: true } as never);
}

export async function restorePromptVersion(promptId: number, version: number): Promise<PromptTemplateItem> {
  return (await http.post(`/prompt-library/${promptId}/versions/${version}/restore`)).data;
}

export async function recordPromptUsage(payload: {
  prompt_template_id: number; platform_account_id?: number | null;
  module: "image_studio" | "draft_studio" | "auto_ops";
  scene?: string; target_type?: string; target_id?: number | null; outcome?: Record<string, unknown>;
}): Promise<{ id: number }> {
  return (await http.post("/prompt-library/usages", payload)).data;
}
export async function createPromptTemplate(payload: Omit<PromptTemplateItem, "id" | "current_version" | "source_type" | "source_ref" | "updated_at">): Promise<PromptTemplateItem> {
  return (await http.post("/prompt-library", payload)).data;
}
export async function fetchObsidianSyncConfig(): Promise<ObsidianSyncConfig> {
  return (await http.get("/prompt-library/sync/obsidian/config")).data;
}
export async function saveObsidianSyncConfig(vaultPath: string): Promise<ObsidianSyncConfig> {
  return (await http.put("/prompt-library/sync/obsidian/config", { vault_path: vaultPath })).data;
}
export async function scanObsidianVault(): Promise<ObsidianScanResult> {
  return (await http.post("/prompt-library/sync/obsidian/scan")).data;
}
export async function importObsidianPrompts(relativePaths: string[]): Promise<ObsidianImportResult> {
  return (await http.post("/prompt-library/sync/obsidian/import", { relative_paths: relativePaths })).data;
}
export async function fetchBackups(): Promise<{ items: BackupItem[] }> {
  return (await http.get("/backups")).data;
}
export async function createBackup(): Promise<BackupItem> {
  return (await http.post("/backups")).data;
}

export async function createAutoTask(payload: AutoTaskCreatePayload): Promise<AutoTask> {
  const response = await http.post<AutoTask>("/auto-tasks", payload);
  return response.data;
}

export async function updateAutoTask(taskId: number, payload: AutoTaskUpdatePayload): Promise<AutoTask> {
  const response = await http.patch<AutoTask>(`/auto-tasks/${taskId}`, payload);
  return response.data;
}

export async function deleteAutoTask(taskId: number): Promise<{ id: number; status: string }> {
  const response = await http.delete<{ id: number; status: string }>(`/auto-tasks/${taskId}`);
  return response.data;
}

/**
 * 一句话解析成任务配置提案。**只读接口，不创建任务**——落库仍走 createAutoTask，
 * 中间必须经用户逐项复核（自动运营会自动发布到真实账号，发出去撤不回来）。
 */
export async function parseAutoTaskDescription(payload: {
  description: string;
  account_id?: number;
}): Promise<AutoTaskParseResult> {
  const response = await http.post<AutoTaskParseResult>("/auto-tasks/parse-description", payload);
  return response.data;
}

export async function runAutoTask(taskId: number): Promise<AutoTaskRunResult> {
  const response = await http.post<AutoTaskRunResult>(`/auto-tasks/${taskId}/run`);
  return response.data;
}

export async function fetchOperationProfiles(): Promise<OperationProfile[]> {
  return (await http.get<{ items: OperationProfile[] }>("/operations/profiles")).data.items;
}

export async function saveOperationProfile(accountId: number, payload: Omit<OperationProfile, "id" | "platform_account_id" | "created_at" | "updated_at">): Promise<OperationProfile> {
  return (await http.put<OperationProfile>(`/operations/profiles/${accountId}`, payload)).data;
}

export async function fetchOperationPlans(planDate: string, planDateTo?: string | number, accountId?: number): Promise<OperationPlan[]> {
  const rangeEnd = typeof planDateTo === "string" ? planDateTo : undefined;
  const legacyAccountId = typeof planDateTo === "number" ? planDateTo : accountId;
  return (await http.get<{ items: OperationPlan[] }>("/operations/plans", {
    params: { plan_date: planDate, plan_date_to: rangeEnd, account_id: legacyAccountId },
  })).data.items;
}

export async function generateOperationPlans(planDate: string, planDateTo?: string | number[], accountIds: number[] = []): Promise<{ created: number; existing: number; days_requested: number; days_covered: number; daily: Array<{ plan_date: string; created: number; existing: number }>; items: OperationPlan[] }> {
  const rangeEnd = typeof planDateTo === "string" ? planDateTo : undefined;
  const selectedAccounts = Array.isArray(planDateTo) ? planDateTo : accountIds;
  return (await http.post<{ created: number; existing: number; days_requested: number; days_covered: number; daily: Array<{ plan_date: string; created: number; existing: number }>; items: OperationPlan[] }>("/operations/plans/generate", {
    plan_date: planDate,
    ...(rangeEnd ? { plan_date_to: rangeEnd } : {}),
    account_ids: selectedAccounts,
  })).data;
}

export async function fetchAccountOperationSummary(accountId: number): Promise<AccountOperationSummary> {
  return (await http.get<AccountOperationSummary>(`/operations/accounts/${accountId}/summary`)).data;
}

export async function updateOperationPlan(planId: number, payload: Partial<Pick<OperationPlan, "content_direction" | "topic" | "planned_at" | "status">>): Promise<OperationPlan> {
  return (await http.patch<OperationPlan>(`/operations/plans/${planId}`, payload)).data;
}

export async function generateOperationPlanDraft(planId: number): Promise<OperationPlanDraftResult> {
  return (await http.post<OperationPlanDraftResult>(`/operations/plans/${planId}/generate-draft`, { create_publish_job: false })).data;
}

export async function fetchLearningOverview(): Promise<LearningOverview> {
  return (await http.get<LearningOverview>("/operations/learning/overview")).data;
}

export async function fetchLearningInsights(accountId?: number): Promise<LearningInsight[]> {
  return (await http.get<{ items: LearningInsight[] }>("/operations/learning/insights", { params: { account_id: accountId } })).data.items;
}

export async function createLearningInsight(payload: Omit<LearningInsight, "id" | "status" | "created_at">): Promise<LearningInsight> {
  return (await http.post<LearningInsight>("/operations/learning/insights", payload)).data;
}

export async function syncAccountContent(accountId: number, options: { mode?: "quick" | "full" | "rebuild"; analyze_after_sync?: boolean } = {}): Promise<TaskRecord> {
  return (await http.post<TaskRecord>(`/operations/accounts/${accountId}/sync-content`, {
    mode: options.mode || "full",
    analyze_after_sync: Boolean(options.analyze_after_sync),
  })).data;
}

export async function fetchAccountOperationTask(accountId: number): Promise<TaskRecord | null> {
  return (await http.get<{ task: TaskRecord | null }>(`/operations/accounts/${accountId}/operation-task`)).data.task;
}

export async function clearAccountContent(accountId: number): Promise<{ account_id: number; deleted: number }> {
  return (await http.delete(`/operations/accounts/${accountId}/content`)).data;
}

export async function fetchAccountContent(accountId: number): Promise<AccountContentItem[]> {
  return (await http.get<{ items: AccountContentItem[] }>(`/operations/accounts/${accountId}/content`)).data.items;
}

export async function fetchContentPerformance(): Promise<ContentPerformanceItem[]> {
  return (await http.get<{ items: ContentPerformanceItem[] }>("/operations/learning/content-performance")).data.items;
}

export async function fetchStrategyProposals(params?: { account_id?: number; status?: string }): Promise<StrategyProposal[]> {
  return (await http.get<{ items: StrategyProposal[] }>("/operations/strategy-proposals", { params })).data.items;
}

export async function analyzeAccountStrategy(accountId: number): Promise<TaskRecord> {
  return (await http.post<TaskRecord>(`/operations/accounts/${accountId}/analyze-strategy`)).data;
}

export async function approveStrategyProposal(
  proposalId: number,
  strategy?: StrategyProposal["strategy"],
  options: { create_auto_tasks: boolean } = { create_auto_tasks: false },
): Promise<{ proposal: StrategyProposal; profile: OperationProfile; insights_created: number; plans_created: number; auto_tasks_created: number; auto_tasks_updated: number; auto_tasks_enabled: boolean }> {
  return (await http.post(`/operations/strategy-proposals/${proposalId}/approve`, {
    strategy, create_auto_tasks: options.create_auto_tasks, enable_auto_tasks: false,
  })).data;
}

export async function rejectStrategyProposal(proposalId: number): Promise<StrategyProposal> {
  return (await http.post<StrategyProposal>(`/operations/strategy-proposals/${proposalId}/reject`)).data;
}

// ---------------------------------------------------------------------------
// 素材域（图片工坊）：文件夹 / 产品 / 场景 / 标签 / 素材
// ---------------------------------------------------------------------------

export async function fetchAssetFolders(): Promise<AssetFolder[]> {
  return (await http.get<{ items: AssetFolder[] }>("/assets/folders")).data.items;
}

/** silent=true 时跳过全局错误提示，由调用方自行给出中文文案（同名 409 等）。 */
export async function createAssetFolder(
  payload: { name: string; parent_id?: number | null },
  silent = false,
): Promise<AssetFolder> {
  return (await http.post<AssetFolder>("/assets/folders", payload, { _silent: silent } as never)).data;
}

/** silent=true 时跳过全局错误提示（同名 409、成环 400、封面不属于该文件夹 400）。 */
export async function updateAssetFolder(
  folderId: number,
  payload: { name?: string; parent_id?: number | null; cover_asset_id?: number | null },
  silent = false,
): Promise<AssetFolder> {
  return (
    await http.patch<AssetFolder>(`/assets/folders/${folderId}`, payload, { _silent: silent } as never)
  ).data;
}

/** silent=true 时跳过全局错误提示（非空文件夹 409）。 */
export async function deleteAssetFolder(folderId: number, silent = false): Promise<void> {
  await http.delete(`/assets/folders/${folderId}`, { _silent: silent } as never);
}

export async function fetchProducts(): Promise<Product[]> {
  return (await http.get<{ items: Product[] }>("/assets/products")).data.items;
}

export async function createProduct(payload: { name: string; description?: string }): Promise<Product> {
  return (await http.post<Product>("/assets/products", payload)).data;
}

export async function updateProduct(productId: number, payload: { name?: string; description?: string }): Promise<Product> {
  return (await http.patch<Product>(`/assets/products/${productId}`, payload)).data;
}

export async function deleteProduct(productId: number): Promise<void> {
  await http.delete(`/assets/products/${productId}`);
}

export async function fetchAssetScenes(): Promise<AssetScene[]> {
  return (await http.get<{ items: AssetScene[] }>("/assets/scenes")).data.items;
}

export async function createAssetScene(payload: { name: string }): Promise<AssetScene> {
  return (await http.post<AssetScene>("/assets/scenes", payload)).data;
}

export async function updateAssetScene(sceneId: number, payload: { name?: string }): Promise<AssetScene> {
  return (await http.patch<AssetScene>(`/assets/scenes/${sceneId}`, payload)).data;
}

export async function deleteAssetScene(sceneId: number): Promise<void> {
  await http.delete(`/assets/scenes/${sceneId}`);
}

export async function fetchAssetTags(): Promise<AssetTag[]> {
  return (await http.get<{ items: AssetTag[] }>("/assets/tags")).data.items;
}

export async function createAssetTag(payload: { name: string; color?: string }): Promise<AssetTag> {
  return (await http.post<AssetTag>("/assets/tags", payload)).data;
}

export async function updateAssetTag(tagId: number, payload: { name?: string; color?: string }): Promise<AssetTag> {
  return (await http.patch<AssetTag>(`/assets/tags/${tagId}`, payload)).data;
}

export async function deleteAssetTag(tagId: number): Promise<void> {
  await http.delete(`/assets/tags/${tagId}`);
}

export type MediaAssetFilters = {
  folder_id?: number;
  product_id?: number;
  scene_id?: number;
  tag_id?: number;
  search?: string;
  recent?: boolean;
  page?: number;
  page_size?: number;
};

export async function fetchMediaAssets(filters: MediaAssetFilters = {}): Promise<Paginated<MediaAsset>> {
  const params = {
    folder_id: filters.folder_id,
    product_id: filters.product_id,
    scene_id: filters.scene_id,
    tag_id: filters.tag_id,
    search: filters.search?.trim() || undefined,
    recent: filters.recent || undefined,
    page: filters.page,
    page_size: filters.page_size,
  };
  return (await http.get<Paginated<MediaAsset>>("/assets", { params })).data;
}

export async function createMediaAsset(payload: {
  asset_type: "image" | "video";
  url?: string;
  local_path?: string;
  original_name?: string;
  mime_type?: string;
  file_size?: number;
  source?: string;
  /** "坐浴盆/卫生间场景" —— 缺失层级自动创建。folder_id 优先。 */
  folder_path?: string;
  folder_id?: number | null;
  product_id?: number | null;
  scene_id?: number | null;
  tag_ids?: number[];
}): Promise<MediaAsset> {
  return (await http.post<MediaAsset>("/assets", payload)).data;
}

/**
 * 只有显式出现在 payload 里的字段会被后端写入，省略的字段保持不变。
 * 传 null 表示清空该关联。后端返回改动条数，不返回素材列表。
 */
export async function batchUpdateMediaAssets(payload: {
  asset_ids: number[];
  folder_id?: number | null;
  product_id?: number | null;
  scene_id?: number | null;
  tag_ids?: number[] | null;
}): Promise<{ updated_count: number }> {
  return (await http.patch<{ updated_count: number }>("/assets/batch", payload)).data;
}

/** 后端要求 assetIds 是该用户全部素材的完整集合，缺一个就 400。 */
export async function reorderMediaAssets(assetIds: number[], silent = false): Promise<void> {
  await http.put("/assets/reorder", { asset_ids: assetIds }, { _silent: silent } as never);
}

/** silent=true 时跳过全局错误提示（素材仍被草稿引用时 409）。 */
export async function deleteMediaAsset(assetId: number, silent = false): Promise<void> {
  await http.delete(`/assets/${assetId}`, { _silent: silent } as never);
}
