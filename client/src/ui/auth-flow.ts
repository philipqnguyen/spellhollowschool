// Where an auth error sends the player next. Kept apart from the screens (which
// need the DOM) so the decisions are unit-tested.

import { ApiError } from "../api";

/** Shown above the sign-in form after registering with an email that has an account. */
export const EMAIL_TAKEN_NOTICE = "You already have an account with this email. Sign in instead.";

/** Shown above the sign-in form after a password reset. */
export const PASSWORD_CHANGED_NOTICE = "Your password is changed and you are signed out everywhere. Sign in with your new password.";

/** A query parameter of a link (`verify` of a verification link, `reset` of a reset link), or null. */
export function linkParam(url: string, name: string): string | null {
  try {
    return new URL(url, "http://localhost/").searchParams.get(name) || null;
  } catch {
    return null;
  }
}

/** A reset link that is spent, expired or unknown (`400 invalid_token`): ask for a new one. */
export function resetLinkInvalid(e: unknown): boolean {
  return e instanceof ApiError && e.code === "invalid_token";
}

/** Registering hit an existing account (`409 email_taken`): sign in with this email instead. */
export function emailTaken(e: unknown): boolean {
  return e instanceof ApiError && e.code === "email_taken";
}

/**
 * Signing in with the right password to an unconfirmed account (`403
 * unverified`): the server has emailed a new link. Returns the dev link (or
 * null outside development), or undefined when `e` is any other error.
 */
export function unverifiedLink(e: unknown): string | null | undefined {
  if (!(e instanceof ApiError) || e.code !== "unverified") return undefined;
  const url = e.body.dev_verify_url;
  return typeof url === "string" ? url : null;
}

/** The page after following a verification link that did not sign in: what it says and offers. */
export interface VerifyFailure {
  heading: string;
  body: string;
  /** `continue` to the character (already signed in), `login`, or `retry` the same link. */
  next: "continue" | "login" | "retry";
}

/**
 * Following a verification link failed. A spent, expired or unknown link
 * (`400 invalid_token`, e.g. the second click on it) sends the player on: to
 * their character when this browser is already signed in, else to sign in,
 * which emails a fresh link to an unconfirmed account. Anything else (no
 * connection, rate limited) has not used the link up, so it can be tried again.
 */
export function verifyFailure(e: unknown, signedIn: boolean): VerifyFailure {
  if (e instanceof ApiError && e.code === "invalid_token") {
    return signedIn
      ? { heading: "You're already signed in", body: "This confirmation link has already been used or has expired, but you're signed in and can carry on.", next: "continue" }
      : {
          heading: "This link has already been used",
          body: "Confirmation links work only once and only for a limited time. If you already confirmed your email, just sign in. If not, sign in anyway and we'll email you a new link.",
          next: "login",
        };
  }
  return { heading: "That link didn't work", body: e instanceof Error ? e.message : String(e), next: "retry" };
}

/** Whole minutes until a `429`'s `retry_after` (seconds) is over, rounded up; null without one. */
export function retryAfterMinutes(e: unknown): number | null {
  if (!(e instanceof ApiError) || e.status !== 429) return null;
  const secs = e.body.retry_after;
  if (typeof secs !== "number" || !Number.isFinite(secs)) return null;
  return Math.max(1, Math.ceil(secs / 60));
}

/** "Try again in 7 minutes." */
export function tryAgainIn(minutes: number): string {
  return `Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
}

/**
 * What a form shows for an error: for the per-account limits (`429
 * too_many_emails`, `429 too_many_attempts`) the minutes left, counted from
 * `retry_after`; otherwise the error's own message.
 */
export function errorText(e: unknown): string {
  const minutes = retryAfterMinutes(e);
  if (minutes !== null && e instanceof ApiError) {
    if (e.code === "too_many_emails") return `Too many emails sent. ${tryAgainIn(minutes)}`;
    if (e.code === "too_many_attempts") return `Too many sign-in attempts. ${tryAgainIn(minutes)}`;
  }
  return e instanceof Error ? e.message : String(e);
}

/**
 * Signing in with the right password to an unconfirmed account that has
 * been sent its allowance of emails (`429 too_many_emails`): no new link went
 * out, so the "check your email" card shows this instead. Undefined for any
 * other error.
 */
export function emailLimitOnSignIn(e: unknown): string | undefined {
  return e instanceof ApiError && e.code === "too_many_emails" ? errorText(e) : undefined;
}
