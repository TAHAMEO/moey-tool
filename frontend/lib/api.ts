// Same-origin API client. Mutating requests carry the per-session CSRF token.

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let csrfToken: string | null = null;

export function setCsrf(token: string | null) {
  csrfToken = token;
}

type Options = {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
};

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["x-csrf-token"] = csrfToken;
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    signal: opts.signal,
    cache: "no-store",
  });
  if (!response.ok) {
    let code = "error";
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      code = data?.error?.code ?? code;
      message = data?.error?.message ?? message;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function uploadWithProgress<T>(path: string, form: FormData, onProgress: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    if (csrfToken) xhr.setRequestHeader("x-csrf-token", csrfToken);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let data: any = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(xhr.status, data?.error?.code ?? "error", data?.error?.message ?? "Upload failed"));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Network error during upload"));
    xhr.send(form);
  });
}

type UploadSession = { upload_id: string; chunk_size: number; received: number; size: number };

function sendPiece(path: string, piece: Blob, onProgress: (loaded: number) => void, signal?: AbortSignal): Promise<UploadSession> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api${path}`);
    if (csrfToken) xhr.setRequestHeader("x-csrf-token", csrfToken);
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.upload.onprogress = (event) => onProgress(event.loaded);
    xhr.onload = () => {
      let data: any = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as UploadSession);
      else {
        const error = new ApiError(xhr.status, data?.error?.code ?? "error", data?.error?.message ?? `Upload failed (${xhr.status})`);
        (error as ApiError & { received?: number }).received = data?.error?.received;
        reject(error);
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Network error during upload"));
    xhr.onabort = () => reject(new ApiError(0, "aborted", "Upload cancelled"));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(piece);
  });
}

/**
 * Upload a file in pieces of a few MB (`/uploads`), with progress, and resume after a dropped
 * connection. One big request doesn't survive every hop: the Windows app's web server keeps only
 * the first 10 MB of a forwarded request. Returns the upload id to hand to a `.../complete` call.
 */
export async function uploadInPieces(file: File, purpose: "media" | "library", onProgress: (fraction: number) => void,
  signal?: AbortSignal): Promise<string> {
  const session = await api<UploadSession>("/uploads", {
    body: { filename: file.name, size: file.size, mime: file.type || null, purpose }, signal,
  });
  const id = session.upload_id;
  const chunk = session.chunk_size;
  let offset = session.received;
  let failures = 0;
  onProgress(0);
  try {
    while (offset < file.size) {
      const end = Math.min(file.size, offset + chunk);
      try {
        const result = await sendPiece(`/uploads/${id}?offset=${offset}`, file.slice(offset, end),
          (loaded) => onProgress(Math.min(1, (offset + loaded) / file.size)), signal);
        offset = result.received;
        failures = 0;
      } catch (err) {
        const error = err as ApiError & { received?: number };
        if (error.code === "aborted" || signal?.aborted) throw error;
        if (error.code === "offset" && typeof error.received === "number") {
          offset = error.received; // the server has a different amount: continue from there
          continue;
        }
        if (error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) throw error;
        failures += 1;
        if (failures > 6) throw error;
        await new Promise((r) => setTimeout(r, Math.min(15000, 1000 * 2 ** failures)));
        try {
          offset = (await api<UploadSession>(`/uploads/${id}`, { signal })).received; // what arrived before the drop
        } catch {
          /* still offline: retry the same piece */
        }
      }
      onProgress(Math.min(1, offset / file.size));
    }
  } catch (err) {
    api(`/uploads/${id}`, { method: "DELETE" }).catch(() => undefined);
    throw err;
  }
  return id;
}

/** A video or audio file for the Brain, uploaded in pieces: `{asset_id, filename, size, duplicate}`. */
export async function uploadMedia(file: File, onProgress: (fraction: number) => void, signal?: AbortSignal) {
  const uploadId = await uploadInPieces(file, "media", onProgress, signal);
  return api<{ asset_id: string; filename: string; size: number; duplicate: boolean }>("/chat/upload/complete",
    { body: { upload_id: uploadId }, signal });
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === undefined || bytes === null) return "—";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(value?: string | null): string {
  if (!value) return "";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function money(value?: number | null): string {
  if (value === undefined || value === null) return "—";
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

const PLATFORM_NAMES: Record<string, string> = {
  youtube: "YouTube", instagram: "Instagram", google_drive: "Google Drive", gdrive: "Google Drive",
  direct: "Direct link", upload: "Upload", live_ingest: "Live stream",
};

/** Human spelling of a platform key ("youtube" → "YouTube"). */
export function platformName(key?: string | null): string {
  if (!key) return "";
  return PLATFORM_NAMES[key] ?? key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
