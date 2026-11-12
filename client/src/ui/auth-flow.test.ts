import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { emailLimitOnSignIn, emailTaken, errorText, linkParam, resetLinkInvalid, retryAfterMinutes, unverifiedLink, verifyFailure } from "./auth-flow";

describe("per-account limits", () => {
  const emails = (secs: number) => new ApiError(429, "too_many_emails", "Too many emails sent. Try again in 15 minutes.", { error: "too_many_emails", retry_after: secs });
  const attempts = (secs: number) => new ApiError(429, "too_many_attempts", "Too many sign-in attempts. Try again in 15 minutes.", { error: "too_many_attempts", retry_after: secs });

  it("counts the minutes left from retry_after, rounded up", () => {
    expect(retryAfterMinutes(emails(900))).toBe(15);
    expect(retryAfterMinutes(emails(841))).toBe(15);
    expect(retryAfterMinutes(emails(840))).toBe(14);
    expect(retryAfterMinutes(emails(1))).toBe(1);
    expect(retryAfterMinutes(new ApiError(429, "too_many_emails", "…"))).toBeNull();
    expect(retryAfterMinutes(new ApiError(401, "invalid_credentials", "No.", { retry_after: 60 }))).toBeNull();
    expect(retryAfterMinutes(new Error("429"))).toBeNull();
  });

  it("tells the reset form and the sign-in form how long to wait", () => {
    expect(errorText(emails(600))).toBe("Too many emails sent. Try again in 10 minutes.");
    expect(errorText(emails(30))).toBe("Too many emails sent. Try again in 1 minute.");
    expect(errorText(attempts(61))).toBe("Too many sign-in attempts. Try again in 2 minutes.");
  });

  it("leaves every other error's message as it is", () => {
    expect(errorText(new ApiError(429, "rate_limited", "Too many attempts. Please wait a moment and try again.", { retry_after: 5 }))).toBe(
      "Too many attempts. Please wait a moment and try again.",
    );
    expect(errorText(new ApiError(401, "invalid_credentials", "That email and password don't match."))).toBe("That email and password don't match.");
    expect(errorText(new Error("Enter your email and password."))).toBe("Enter your email and password.");
    expect(errorText("plain")).toBe("plain");
  });

  it("sends an unconfirmed sign-in over the email limit to the check-your-email card", () => {
    expect(emailLimitOnSignIn(emails(420))).toBe("Too many emails sent. Try again in 7 minutes.");
    expect(emailLimitOnSignIn(attempts(420))).toBeUndefined();
    expect(emailLimitOnSignIn(new ApiError(403, "unverified", "Confirm…"))).toBeUndefined();
  });
});

describe("emailTaken", () => {
  it("is true only for the server's email_taken answer", () => {
    expect(emailTaken(new ApiError(409, "email_taken", "You already have an account with this email. Sign in instead."))).toBe(true);
    expect(emailTaken(new ApiError(400, "invalid_email", "Enter a valid email address."))).toBe(false);
    expect(emailTaken(new Error("email_taken"))).toBe(false);
  });
});

describe("unverifiedLink", () => {
  it("returns the dev link from an unverified answer", () => {
    const url = "http://localhost:5173/?verify=abc";
    expect(unverifiedLink(new ApiError(403, "unverified", "Confirm…", { dev_verify_url: url }))).toBe(url);
  });
  it("is null for an unverified answer without a dev link", () => {
    expect(unverifiedLink(new ApiError(403, "unverified", "Confirm…"))).toBeNull();
  });
  it("is undefined for any other error", () => {
    expect(unverifiedLink(new ApiError(401, "invalid_credentials", "No."))).toBeUndefined();
    expect(unverifiedLink(new Error("unverified"))).toBeUndefined();
  });
});

describe("linkParam", () => {
  it("reads a reset or verify token from a link", () => {
    expect(linkParam("http://localhost:5173/?reset=abc_-1", "reset")).toBe("abc_-1");
    expect(linkParam("http://localhost:5173/?verify=xyz", "verify")).toBe("xyz");
  });
  it("is null when the link has no such parameter", () => {
    expect(linkParam("http://localhost:5173/", "reset")).toBeNull();
    expect(linkParam("http://localhost:5173/?reset=", "reset")).toBeNull();
  });
});

describe("resetLinkInvalid", () => {
  it("is true only for invalid_token", () => {
    expect(resetLinkInvalid(new ApiError(400, "invalid_token", "This reset link is invalid or has expired."))).toBe(true);
    expect(resetLinkInvalid(new ApiError(400, "weak_password", "Use at least 10 characters."))).toBe(false);
    expect(resetLinkInvalid(new Error("invalid_token"))).toBe(false);
  });
});

describe("verifyFailure", () => {
  const spent = new ApiError(400, "invalid_token", "This link is invalid or has expired.");
  it("sends a spent link to sign in, which emails a new one", () => {
    const page = verifyFailure(spent, false);
    expect(page.next).toBe("login");
    expect(page.body).toMatch(/sign in/i);
    expect(page.body).toMatch(/new link/);
  });
  it("carries on with a session this browser already has (a second click)", () => {
    expect(verifyFailure(spent, true).next).toBe("continue");
  });
  it("offers to try again when the link was not used up", () => {
    const offline = new ApiError(0, "network", "Could not reach the castle.");
    expect(verifyFailure(offline, false)).toEqual({ heading: "That link didn't work", body: "Could not reach the castle.", next: "retry" });
    expect(verifyFailure(new ApiError(429, "rate_limited", "Slow down."), true).next).toBe("retry");
  });
});
