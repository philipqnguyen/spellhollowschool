// HTTP API client (docs/architecture.md, "HTTP API") and session token storage.
//
// Every error the server sends is `{ error, message }` (a few add fields, kept
// in `body`); it surfaces here as an `ApiError` whose `message` is the server's
// player-facing text. A 401 on an
// authenticated request clears the stored session and calls the handler set
// with `onUnauthorized`, which returns the player to the sign-in screen.

const SESSION_KEY = "shs.session";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    /** Machine-readable code, e.g. `"invalid_credentials"`; `"network"` when the request never got an answer. */
    readonly code: string,
    message: string,
    /** The whole error body, for the few errors that carry more (e.g. `dev_verify_url`). */
    readonly body: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface Session {
  token: string;
  /** RFC 3339 UTC timestamp. */
  expiresAt: string;
}

export interface Character {
  id: number;
  name: string;
  /** As the API spells it: `"Skytalon"`. */
  house: string;
  /** Present in `GET /api/characters` (the picker). */
  level?: number;
}

/** `GET /api/characters`: the account's characters, oldest first, and how many it may have. */
export interface Roster {
  characters: Character[];
  max: number;
}

// ------------------------------------------------------------------ session storage

/** The stored session, or null when there is none, it has expired, or storage is unavailable. */
export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Session>;
    if (typeof s.token !== "string" || typeof s.expiresAt !== "string") return null;
    if (Date.parse(s.expiresAt) <= Date.now()) {
      clearSession();
      return null;
    }
    return { token: s.token, expiresAt: s.expiresAt };
  } catch {
    return null;
  }
}

export function saveSession(s: Session): void {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  } catch {
    // Private windows may refuse storage; the session then lasts for this page only.
  }
  memorySession = s;
}

export function clearSession(): void {
  memorySession = null;
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // Nothing stored.
  }
}

/** Fallback for when localStorage is unavailable. */
let memorySession: Session | null = null;

function currentToken(): string | null {
  return (loadSession() ?? memorySession)?.token ?? null;
}

let unauthorizedHandler: (() => void) | null = null;

/** Called (once per 401) after an authenticated request is rejected; the session is already cleared. */
export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler;
}

// ------------------------------------------------------------------ requests

async function request<T>(method: string, path: string, body?: unknown, auth = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = currentToken();
    if (!token) {
      unauthorizedHandler?.();
      throw new ApiError(401, "unauthorized", "Please sign in.");
    }
    headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "Could not reach the castle. Check your connection and try again.");
  }

  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (res.ok) return data as T;

  const err = (data && typeof data === "object" ? data : {}) as { error?: string; message?: string } & Record<string, unknown>;
  if (res.status === 401 && auth) {
    clearSession();
    unauthorizedHandler?.();
  }
  throw new ApiError(
    res.status,
    err.error ?? `http_${res.status}`,
    err.message ?? (res.status >= 500 ? "The castle is having trouble. Please try again shortly." : `Request failed (${res.status}).`),
    err,
  );
}

/**
 * An authenticated JSON request to `/api<path>`, for feature modules that
 * keep their own endpoint wrappers (friends, dorm inventory). Errors arrive
 * as `ApiError`, and a 401 goes back to sign-in like every other request.
 */
export function authedRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  return request<T>(method, path, body, true);
}

// ------------------------------------------------------------------ endpoints

export interface RegisterResult {
  /** Present only when the server runs with SHS_DEV=1. */
  dev_verify_url?: string;
}

/** `409 email_taken` when the email already has an account (the client then offers sign-in). */
export function register(email: string, password: string, confirmAdult: boolean): Promise<RegisterResult> {
  return request("POST", "/auth/register", { email, password, confirm_adult: confirmAdult });
}

export interface PasswordResetResult {
  /** Present only when the server runs with SHS_DEV=1. */
  dev_reset_url?: string;
}

/** Emails a reset link. `404 no_account` when no account has this email. */
export function requestPasswordReset(email: string): Promise<PasswordResetResult> {
  return request("POST", "/auth/password-reset", { email });
}

/** Sets a new password with a reset link's token; every session of the account ends. `400 invalid_token`, `400 weak_password`. */
export function confirmPasswordReset(token: string, password: string): Promise<void> {
  return request("POST", "/auth/password-reset/confirm", { token, password });
}

/** Stores the session a sign-in answered with. */
function startSession(r: { token: string; expires_at: string }): Session {
  const session = { token: r.token, expiresAt: r.expires_at };
  saveSession(session);
  return session;
}

/**
 * Confirms the email with a verification link's token and signs in (the link
 * proves the inbox). `400 invalid_token` when the link is spent, expired or unknown.
 */
export async function verifyEmail(token: string): Promise<Session> {
  return startSession(await request("POST", "/auth/verify", { token }));
}

export async function login(email: string, password: string): Promise<Session> {
  return startSession(await request("POST", "/auth/login", { email, password }));
}

export async function logout(): Promise<void> {
  try {
    await request("POST", "/auth/logout", undefined, true);
  } finally {
    clearSession();
  }
}

/** The character this session plays, or null when it has picked none (404 `no_character`). */
export async function myCharacter(): Promise<Character | null> {
  try {
    return await request<Character>("GET", "/characters/me", undefined, true);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** The account's characters for the picker (up to `max`). */
export function listCharacters(): Promise<Roster> {
  return request("GET", "/characters", undefined, true);
}

/** Makes `id` the character this session plays (`404 character_not_found` unless it is the account's). */
export function selectCharacter(id: number): Promise<Character> {
  return request("POST", `/characters/${id}/select`, undefined, true);
}

/**
 * Deletes one of the account's characters for good. `confirmName` is the name as the player typed it.
 * `400 name_mismatch`, `409 character_online` (in the world on another device).
 */
export function deleteCharacter(id: number, confirmName: string): Promise<void> {
  return request("DELETE", `/characters/${id}`, { confirm_name: confirmName }, true);
}

/** The session token for the WebSocket `Hello`. */
export function sessionToken(): string | null {
  return currentToken();
}
