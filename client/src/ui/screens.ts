// The pre-game screens: sign in, register, check your email, confirm the email,
// forgotten password (ask for a link, link sent, choose a new password),
// character creation, the character picker, and a generic message card
// (loading, queued, kicked).
// Each renders into the full-screen #screen element and reports back through
// callbacks; main.ts owns the flow between them.

import * as api from "../api";
import { houseInfo } from "../content/houses";
import { h, mount } from "./dom";
import { emailLimitOnSignIn, emailTaken, errorText, linkParam, resetLinkInvalid, unverifiedLink, verifyFailure } from "./auth-flow";
import { confirmsDeletion } from "../boot";
import { renderCreator } from "./creator";
import { passwordProblem, PASSWORD_MAX, PASSWORD_MIN } from "./validate";

const TITLE = "SpellHollow School";

/** The parchment card every screen is drawn on. */
function card(heading: string, sub: string | null, ...content: (Node | null)[]): HTMLElement {
  return h(
    "section",
    { class: "card", role: "dialog", "aria-labelledby": "card-heading" },
    h("p", { class: "card-kicker" }, TITLE),
    h("h1", { id: "card-heading" }, heading),
    sub ? h("p", { class: "card-sub" }, sub) : null,
    h("div", { class: "rule", "aria-hidden": "true" }),
    ...content,
  );
}

function field(label: string, input: HTMLElement, hint?: string): HTMLElement {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), input, hint ? h("span", { class: "field-hint" }, hint) : null);
}

function errorBox(): HTMLElement {
  return h("p", { class: "form-error", role: "alert", hidden: true });
}

function showError(box: HTMLElement, message: string | null): void {
  box.textContent = message ?? "";
  box.hidden = !message;
}

function linkButton(label: string, onclick: () => void): HTMLButtonElement {
  return h("button", { type: "button", class: "link", onclick: (e: Event) => (e.preventDefault(), onclick()) }, label);
}

/** "Credits" under a sign-in card: the credits card (ui/credits.ts, loaded on demand), then `back`. */
function creditsFoot(root: HTMLElement, back: () => void): HTMLElement {
  const open = () => void import("./credits").then((m) => m.renderCredits(root, { onBack: back }));
  return h("p", { class: "card-foot credits-foot" }, linkButton("Credits", open));
}

/**
 * Wires a form's submit to an async action, disabling the submit button while it
 * runs and showing its error (an `ApiError` message, with the minutes left
 * for a per-account limit, or a validation string).
 */
function onSubmit(form: HTMLFormElement, errors: HTMLElement, action: () => Promise<void>): void {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector<HTMLButtonElement>("button[type=submit]");
    showError(errors, null);
    if (button) button.disabled = true;
    try {
      await action();
    } catch (err) {
      showError(errors, errorText(err));
    } finally {
      if (button?.isConnected) button.disabled = false;
    }
  });
}

/** Thrown by validation inside an `onSubmit` action; shown like a server error. */
class FormProblem extends Error {}

// ------------------------------------------------------------------ sign in

export interface LoginOptions {
  email?: string;
  /** Shown above the form, e.g. "Your email is confirmed". */
  notice?: string;
  onLoggedIn: () => void;
  onRegister: () => void;
  /** "Forgot password?", with whatever is in the email field. */
  onForgot: (email: string) => void;
  /**
   * Right password, unconfirmed email: the server emailed a new link, or,
   * with `limited` (the too-many-emails message), sent none this time.
   */
  onUnverified: (email: string, devVerifyUrl: string | null, limited?: string) => void;
}

export function renderLogin(root: HTMLElement, opts: LoginOptions): void {
  const email = h("input", { type: "email", name: "email", autocomplete: "email", required: true, value: opts.email ?? "" });
  const password = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const errors = errorBox();
  const form = h(
    "form",
    { class: "form", novalidate: true },
    opts.notice ? h("p", { class: "notice" }, opts.notice) : null,
    field("Email", email),
    field("Password", password),
    h("p", { class: "forgot" }, linkButton("Forgot password?", () => opts.onForgot(email.value.trim()))),
    errors,
    h("button", { type: "submit", class: "primary" }, "Enter the castle"),
  );
  onSubmit(form, errors, async () => {
    if (!email.value.trim() || !password.value) throw new FormProblem("Enter your email and password.");
    const mail = email.value.trim();
    try {
      await api.login(mail, password.value);
    } catch (e) {
      const link = unverifiedLink(e);
      if (link !== undefined) return opts.onUnverified(mail, link);
      const limited = emailLimitOnSignIn(e);
      if (limited !== undefined) return opts.onUnverified(mail, null, limited);
      throw e;
    }
    opts.onLoggedIn();
  });
  mount(
    root,
    card(
      "Welcome back",
      "Sign in to return to your studies.",
      form,
      h("p", { class: "card-foot" }, "New here? ", linkButton("Create an account", opts.onRegister)),
      creditsFoot(root, () => renderLogin(root, { ...opts, email: email.value.trim() || opts.email })),
    ),
  );
  (email.value ? password : email).focus();
}

// ------------------------------------------------------------------ register

export interface RegisterOptions {
  onRegistered: (email: string, devVerifyUrl: string | null) => void;
  /** The email already has an account: sign in with it instead. */
  onEmailTaken: (email: string) => void;
  onLogin: () => void;
}

export function renderRegister(root: HTMLElement, opts: RegisterOptions): void {
  const email = h("input", { type: "email", name: "email", autocomplete: "email", required: true });
  const password = h("input", {
    type: "password",
    name: "new-password",
    autocomplete: "new-password",
    minlength: PASSWORD_MIN,
    maxlength: PASSWORD_MAX,
    required: true,
  });
  const adult = h("input", { type: "checkbox", name: "confirm_adult" });
  const errors = errorBox();
  const form = h(
    "form",
    { class: "form", novalidate: true },
    field("Email", email),
    field("Password", password, `At least ${PASSWORD_MIN} characters.`),
    h("label", { class: "check" }, adult, h("span", {}, "I confirm I am 18 or older")),
    errors,
    h("button", { type: "submit", class: "primary" }, "Create account"),
  );
  onSubmit(form, errors, async () => {
    const mail = email.value.trim();
    if (!mail.includes("@")) throw new FormProblem("Enter a valid email address.");
    const pw = passwordProblem(password.value, mail);
    if (pw) throw new FormProblem(pw);
    if (!adult.checked) throw new FormProblem("You must be 18 or older to play: confirm it below.");
    let result: api.RegisterResult;
    try {
      result = await api.register(mail, password.value, adult.checked);
    } catch (e) {
      if (emailTaken(e)) return opts.onEmailTaken(mail);
      throw e;
    }
    opts.onRegistered(mail, result.dev_verify_url ?? null);
  });
  mount(
    root,
    card(
      "Your letter has arrived",
      "Create an account to begin. Players must be 18 or older.",
      form,
      h("p", { class: "card-foot" }, "Already enrolled? ", linkButton("Sign in", opts.onLogin)),
      creditsFoot(root, () => renderRegister(root, opts)),
    ),
  );
  email.focus();
}

// ------------------------------------------------------------------ check your email

export interface CheckEmailOptions {
  email: string;
  devVerifyUrl: string | null;
  /** Signing in to an unconfirmed account sent a new link (not a new account). */
  resent?: boolean;
  /**
   * Signing in to an unconfirmed account sent no link: it had its allowance
   * of emails. The message, with the minutes left, shown in the error style.
   */
  limited?: string;
  onVerify: (token: string) => void;
  onLogin: () => void;
}

/**
 * Development only: the link the email would carry, followed in place.
 * `param` is the client's query parameter for it (`verify`, `reset`).
 */
function devLinkBox(url: string | null, token: string | null, param: string, label: string, testId: string, follow: (token: string) => void): HTMLElement | null {
  if (!url || !token) return null;
  return h(
    "div",
    { class: "dev-box" },
    h("p", { class: "dev-tag" }, "Development server"),
    h("p", {}, "No email is sent in development. Use this link instead:"),
    h(
      "a",
      {
        class: "dev-link",
        href: `?${param}=${encodeURIComponent(token)}`,
        "data-testid": testId,
        onclick: (e: Event) => {
          e.preventDefault();
          follow(token);
        },
      },
      label,
    ),
    h("p", { class: "dev-url" }, url),
  );
}

export function renderCheckEmail(root: HTMLElement, opts: CheckEmailOptions): void {
  const token = opts.devVerifyUrl ? linkParam(opts.devVerifyUrl, "verify") : null;
  const dev = devLinkBox(opts.devVerifyUrl, token, "verify", "Confirm my email", "dev-verify-link", opts.onVerify);
  mount(
    root,
    card(
      "Check your email",
      null,
      opts.limited
        ? h(
            "p",
            {},
            "Your email isn't confirmed yet. We already sent confirmation links to ",
            h("strong", {}, opts.email),
            ". Follow the latest one, then sign in.",
          )
        : h(
            "p",
            {},
            opts.resent ? "Your email isn't confirmed yet. We sent a new confirmation link to " : "We sent a confirmation link to ",
            h("strong", {}, opts.email),
            ". Follow it, then sign in.",
          ),
      opts.limited ? h("p", { class: "form-error", role: "alert" }, opts.limited) : null,
      h("p", { class: "muted" }, opts.limited ? "Each link is valid for 48 hours." : "The link is valid for 48 hours."),
      dev,
      h("p", { class: "card-foot" }, linkButton("Back to sign in", opts.onLogin)),
    ),
  );
}

// ------------------------------------------------------------------ forgotten password

export interface ForgotPasswordOptions {
  /** Prefilled from the sign-in form. */
  email?: string;
  onSent: (email: string, devResetUrl: string | null) => void;
  onLogin: (email: string) => void;
  onRegister: () => void;
}

/** Asks for a reset link by email. */
export function renderForgotPassword(root: HTMLElement, opts: ForgotPasswordOptions): void {
  const email = h("input", { type: "email", name: "email", autocomplete: "email", required: true, value: opts.email ?? "" });
  const errors = errorBox();
  const form = h(
    "form",
    { class: "form", novalidate: true },
    field("Email", email),
    errors,
    h("button", { type: "submit", class: "primary" }, "Send reset link"),
  );
  onSubmit(form, errors, async () => {
    const mail = email.value.trim();
    if (!mail.includes("@")) throw new FormProblem("Enter a valid email address.");
    const result = await api.requestPasswordReset(mail);
    opts.onSent(mail, result.dev_reset_url ?? null);
  });
  mount(
    root,
    card(
      "Forgotten your password?",
      "Enter your email and we will send you a link to choose a new one.",
      form,
      h(
        "p",
        { class: "card-foot" },
        linkButton("Back to sign in", () => opts.onLogin(email.value.trim())),
        " · ",
        linkButton("Create an account", opts.onRegister),
      ),
    ),
  );
  email.focus();
}

export interface ResetSentOptions {
  email: string;
  devResetUrl: string | null;
  onReset: (token: string) => void;
  onLogin: () => void;
}

export function renderResetSent(root: HTMLElement, opts: ResetSentOptions): void {
  const token = opts.devResetUrl ? linkParam(opts.devResetUrl, "reset") : null;
  mount(
    root,
    card(
      "Check your email",
      null,
      h("p", {}, "We sent a password reset link to ", h("strong", {}, opts.email), ". Follow it to choose a new password."),
      h("p", { class: "muted" }, "The link works once and is valid for one hour."),
      devLinkBox(opts.devResetUrl, token, "reset", "Choose a new password", "dev-reset-link", opts.onReset),
      h("p", { class: "card-foot" }, linkButton("Back to sign in", opts.onLogin)),
    ),
  );
}

export interface ResetPasswordOptions {
  token: string;
  /** The password is changed and every session ended. */
  onDone: () => void;
  /** Ask for a new link. */
  onForgot: () => void;
}

/** The page a reset link opens: choose the new password. */
export function renderResetPassword(root: HTMLElement, opts: ResetPasswordOptions): void {
  const password = h("input", {
    type: "password",
    name: "new-password",
    autocomplete: "new-password",
    minlength: PASSWORD_MIN,
    maxlength: PASSWORD_MAX,
    required: true,
  });
  const errors = errorBox();
  const form = h(
    "form",
    { class: "form", novalidate: true },
    field("New password", password, `At least ${PASSWORD_MIN} characters.`),
    errors,
    h("button", { type: "submit", class: "primary" }, "Set new password"),
  );
  onSubmit(form, errors, async () => {
    // The email isn't known here; the server also checks the password isn't it.
    const pw = passwordProblem(password.value, "");
    if (pw) throw new FormProblem(pw);
    try {
      await api.confirmPasswordReset(opts.token, password.value);
    } catch (e) {
      if (resetLinkInvalid(e)) throw new FormProblem("This reset link is invalid or has expired. Ask for a new one below.");
      throw e;
    }
    opts.onDone();
  });
  mount(
    root,
    card(
      "Choose a new password",
      "This signs you out everywhere; then sign in with the new password.",
      form,
      h("p", { class: "card-foot" }, "Link not working? ", linkButton("Ask for a new one", opts.onForgot)),
    ),
  );
  password.focus();
}

// ------------------------------------------------------------------ confirm email

export interface VerifyOptions {
  token: string;
  /** This browser already has a session (a spent link then carries on with it). */
  signedIn: boolean;
  /** Signed in: on to the character. */
  onContinue: () => void;
  onLogin: () => void;
}

/**
 * Follows a verification link: confirms the email and signs in, then says so.
 * A link that didn't work explains why and offers the way on (`verifyFailure`).
 */
export function renderVerify(root: HTMLElement, opts: VerifyOptions): void {
  renderMessage(root, { heading: "Confirming your email", body: "One moment…", busy: true });
  api.verifyEmail(opts.token).then(
    () =>
      renderMessage(root, {
        heading: "Your email is confirmed",
        body: "Welcome to SpellHollow School! You're signed in and ready to begin.",
        action: { label: "Continue", run: opts.onContinue },
      }),
    (e: unknown) => {
      const page = verifyFailure(e, opts.signedIn);
      const action =
        page.next === "continue"
          ? { label: "Continue", run: opts.onContinue }
          : page.next === "login"
            ? { label: "Sign in", run: opts.onLogin }
            : { label: "Try again", run: () => renderVerify(root, opts) };
      renderMessage(root, {
        heading: page.heading,
        body: page.body,
        action,
        secondary: page.next === "retry" ? { label: "Sign in", run: opts.onLogin } : undefined,
      });
    },
  );
}

// ------------------------------------------------------------------ character creation

export interface CreateCharacterOptions {
  onCreated: (c: api.Character) => void;
  onSignOut: () => void;
  /** Back to the picker (when the account already has characters). */
  onBack?: () => void;
}

export function renderCreateCharacter(root: HTMLElement, opts: CreateCharacterOptions): void {
  // The full creator (house crests, body, face, hair, outfit, name, live preview).
  renderCreator(root, opts);
}

// ------------------------------------------------------------------ character picker

export interface PickerOptions {
  characters: api.Character[];
  /** Index into `characters` selected at first. */
  selected: number;
  /** The account has room for another character: show "Make a new character". */
  canCreate: boolean;
  /** How many characters an account may have (for the line under the list). */
  max: number;
  /**
   * Enter the world as this character. Runs inside the button's click, so it
   * may ask for fullscreen (the browser wants a user gesture).
   */
  onPlay: (c: api.Character) => void;
  /** Open the creator for another character. */
  onCreate: () => void;
  /** Ask to delete this character (the confirmation card). */
  onDelete: (c: api.Character) => void;
  /** Leaves the account (the session ends): back to sign in. */
  onSignOut: () => void;
  /** A line under the list about fullscreen; null when the page cannot go fullscreen. */
  fullscreenHint: string | null;
  /** A line above the list (e.g. "Ada has been deleted."). */
  notice?: string;
}

/**
 * Who to play: the account's characters as a radio group (up to five), each
 * with a Delete link, a "Make a new character" button while there is room,
 * then "Enter the castle". A saved session lands here, and so does Settings >
 * Sign out; the world never opens without this click.
 */
export function renderPicker(root: HTMLElement, opts: PickerOptions): void {
  let chosen = Math.min(Math.max(0, opts.selected), opts.characters.length - 1);
  const tiles = opts.characters.map((c, i) => {
    const house = houseInfo(c.house);
    const input = h("input", {
      type: "radio",
      name: "character",
      value: String(c.id),
      checked: i === chosen,
      onchange: () => (chosen = i),
    }) as HTMLInputElement;
    const details = [house.name || c.house, c.level ? `Level ${c.level}` : null].filter(Boolean).join(" · ");
    return h(
      "div",
      { class: "pick-row" },
      h(
        "label",
        { class: "pick", style: `--house: ${house.colour}; --house-accent: ${house.accent}`, "data-testid": "picker-character" },
        input,
        h("span", { class: "pick-crest", "aria-hidden": "true" }),
        h("span", { class: "pick-text" }, h("span", { class: "pick-name" }, c.name), h("span", { class: "pick-house" }, details)),
      ),
      h(
        "button",
        { type: "button", class: "link pick-delete", "aria-label": `Delete ${c.name}`, "data-testid": "picker-delete", onclick: () => opts.onDelete(c) },
        "Delete",
      ),
    );
  });
  const play = h(
    "button",
    {
      type: "button",
      class: "primary",
      "data-testid": "picker-play",
      onclick: () => {
        const c = opts.characters[chosen];
        if (c) opts.onPlay(c);
      },
    },
    "Enter the castle",
  ) as HTMLButtonElement;
  const create = opts.canCreate
    ? h("button", { type: "button", class: "pick-new", "data-testid": "picker-new", onclick: () => opts.onCreate() }, "+ Make a new character")
    : h("p", { class: "muted pick-full" }, `An account holds up to ${opts.max} characters. Delete one to make room for another.`);
  mount(
    root,
    card(
      "Choose your student",
      "Who walks the castle tonight?",
      opts.notice ? h("p", { class: "notice", role: "status" }, opts.notice) : null,
      h("fieldset", { class: "picks" }, h("legend", { class: "field-label" }, "Your characters"), ...tiles),
      create,
      opts.fullscreenHint ? h("p", { class: "muted picker-hint" }, opts.fullscreenHint) : null,
      play,
      h("p", { class: "card-foot" }, "Not you? ", linkButton("Sign out of this account", opts.onSignOut)),
    ),
  );
  play.focus();
}

// ------------------------------------------------------------------ deleting a character

export interface DeleteCharacterOptions {
  character: api.Character;
  /** Deleted: back to the picker. */
  onDeleted: (c: api.Character) => void;
  /** Kept: back to the picker. */
  onCancel: () => void;
}

/**
 * "Delete Ada?": the player types the character's name to confirm (any
 * case), then `DELETE /api/characters/{id}`. Nothing is asked twice: the
 * typed name is the confirmation.
 */
export function renderDeleteCharacter(root: HTMLElement, opts: DeleteCharacterOptions): void {
  const c = opts.character;
  const errors = errorBox();
  const name = h("input", { type: "text", autocomplete: "off", spellcheck: "false", maxlength: "40", "data-testid": "delete-confirm-name" }) as HTMLInputElement;
  const submit = h("button", { type: "submit", class: "primary", disabled: true, "data-testid": "delete-confirm" }, `Delete ${c.name} for good`) as HTMLButtonElement;
  name.addEventListener("input", () => {
    submit.disabled = !confirmsDeletion(name.value, c.name);
  });
  const form = h(
    "form",
    { class: "form", novalidate: true },
    h(
      "p",
      { class: "message-body" },
      `${c.name} will leave the school for ever: their robes and belongings, Crowns, owl mail, friends and progress go with them, and their name becomes free for anyone. This cannot be undone.`,
    ),
    field(`Type ${c.name} to confirm`, name),
    errors,
    submit,
  ) as HTMLFormElement;
  onSubmit(form, errors, async () => {
    if (!confirmsDeletion(name.value, c.name)) throw new FormProblem(`Type ${c.name} to confirm.`);
    await api.deleteCharacter(c.id, name.value);
    opts.onDeleted(c);
  });
  mount(root, card(`Delete ${c.name}?`, null, form, h("p", { class: "card-foot" }, linkButton(`Keep ${c.name}`, opts.onCancel))));
  name.focus();
}

// ------------------------------------------------------------------ messages

export interface MessageOptions {
  heading: string;
  body?: string;
  busy?: boolean;
  action?: { label: string; run: () => void };
  secondary?: { label: string; run: () => void };
}

/** A simple card with a heading, a line of text, an optional spinner and buttons. */
export function renderMessage(root: HTMLElement, opts: MessageOptions): void {
  mount(
    root,
    card(
      opts.heading,
      null,
      opts.busy ? h("div", { class: "candle", "aria-hidden": "true" }, h("span", { class: "flame" })) : null,
      opts.body ? h("p", { class: "message-body", "aria-live": "polite" }, opts.body) : null,
      opts.action ? h("button", { type: "button", class: "primary", onclick: opts.action.run }, opts.action.label) : null,
      opts.secondary ? h("p", { class: "card-foot" }, linkButton(opts.secondary.label, opts.secondary.run)) : null,
    ),
  );
}
