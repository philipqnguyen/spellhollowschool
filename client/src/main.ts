// Entry point: the screen flow from sign-in to the game (docs/architecture.md
// "Client").
//
//   no session ──► sign in ◄──► register ──► check your email ──► (?verify=…) ──► confirmed, signed in ──► lookup
//   sign in ──► forgot password ──► check your email ──► (?reset=…) ──► new password ──► sign in
//   session ──► GET /api/characters ──► none: create character ──► picker
//                                   └─► 1-5: picker ──► (click) select + fullscreen + game
//   picker ──► new character (while under 5) ──► creator ──► picker
//   picker ──► delete ──► type the name ──► picker (the creator when none is left)
//   game ──► Settings > Sign out ──► picker (windowed; the account stays signed in)
//
// A saved session never opens the world by itself: the picker's "Enter the
// castle" click does, and that click is the gesture fullscreen needs. The
// picker's own "Sign out of this account" ends the session.
//
// The game (Babylon.js, the sim-wasm module, the network client) is a separate
// chunk loaded on first entry, so these screens stay small and fast.

import "./style.css";
import * as api from "./api";
import { bootStep, characterStep } from "./boot";
import { browserAllowed, CHROME_DOWNLOAD_URL, chromeRoute } from "./browser";
import { fullscreen } from "./fullscreen";
import type { RunningGame } from "./game/game";
import { EMAIL_TAKEN_NOTICE, PASSWORD_CHANGED_NOTICE } from "./ui/auth-flow";
import {
  renderCheckEmail,
  renderCreateCharacter,
  renderDeleteCharacter,
  renderForgotPassword,
  renderLogin,
  renderMessage,
  renderPicker,
  renderRegister,
  renderResetPassword,
  renderResetSent,
  renderVerify,
} from "./ui/screens";

/** The character played last (its id), preselected on the picker. */
const LAST_CHARACTER_KEY = "shs.lastCharacter";

const screen = document.getElementById("screen") as HTMLElement;
let game: RunningGame | null = null;

function showScreen(): void {
  screen.hidden = false;
}

function stopGame(): void {
  game?.dispose();
  game = null;
}

/** Out of the world and back to the screens: windowed, the Esc lock released. */
function leaveGame(): void {
  stopGame();
  void fullscreen().exit();
}

function toLogin(notice?: string, email?: string): void {
  leaveGame();
  showScreen();
  renderLogin(screen, {
    notice,
    email,
    onLoggedIn: () => void enter(),
    onRegister: toRegister,
    onForgot: (email) => toForgot(email),
    onUnverified: (email, devVerifyUrl, limited) =>
      renderCheckEmail(screen, { email, devVerifyUrl, resent: true, limited, onVerify: verify, onLogin: () => toLogin(undefined, email) }),
  });
}

function toRegister(): void {
  renderRegister(screen, {
    onRegistered: (email, devVerifyUrl) =>
      renderCheckEmail(screen, { email, devVerifyUrl, onVerify: verify, onLogin: () => toLogin(undefined, email) }),
    onEmailTaken: (email) => toLogin(EMAIL_TAKEN_NOTICE, email),
    onLogin: () => toLogin(),
  });
}

function toForgot(email?: string): void {
  renderForgotPassword(screen, {
    email,
    onSent: (email, devResetUrl) =>
      renderResetSent(screen, { email, devResetUrl, onReset: reset, onLogin: () => toLogin(undefined, email) }),
    onLogin: (email) => toLogin(undefined, email || undefined),
    onRegister: toRegister,
  });
}

function reset(token: string): void {
  // Drop ?reset= from the address bar: the link works once.
  history.replaceState(null, "", location.pathname);
  leaveGame();
  showScreen();
  renderResetPassword(screen, {
    token,
    onDone: () => {
      // The server ended every session, this browser's too.
      api.clearSession();
      toLogin(PASSWORD_CHANGED_NOTICE);
    },
    onForgot: () => toForgot(),
  });
}

function verify(token: string): void {
  // Drop ?verify= from the address bar so a reload doesn't reuse the spent token.
  history.replaceState(null, "", location.pathname);
  leaveGame();
  showScreen();
  // Confirming signs in, exactly as the sign-in form does; then on to the character.
  renderVerify(screen, { token, signedIn: api.loadSession() !== null, onContinue: () => void enter(), onLogin: () => toLogin() });
}

/** With a session: list the characters, then the picker (or the creator when there is none). */
async function enter(notice?: string): Promise<void> {
  leaveGame();
  showScreen();
  renderMessage(screen, { heading: "Opening the great doors", body: "Finding your trunk…", busy: true });
  let roster: api.Roster;
  try {
    roster = await api.listCharacters();
  } catch (e) {
    if (e instanceof api.ApiError && e.status === 401) return; // onUnauthorized already moved to sign-in
    renderMessage(screen, {
      heading: "The castle is out of reach",
      body: e instanceof Error ? e.message : String(e),
      action: { label: "Try again", run: () => void enter() },
      secondary: { label: "Sign out", run: signOut },
    });
    return;
  }
  const step = characterStep(roster.characters, rememberedCharacter(), roster.max);
  if (step.kind === "create") {
    toCreator(false);
    return;
  }
  const fs = fullscreen();
  renderPicker(screen, {
    characters: step.characters,
    selected: step.selected,
    canCreate: step.canCreate,
    max: roster.max,
    notice,
    fullscreenHint: fs.supported ? `The castle opens in fullscreen. ${fs.canLockEsc ? "Hold Esc" : "Press Esc"} to leave it.` : null,
    onPlay: (c) => {
      // Inside the click: the browser allows fullscreen only from a gesture. A refusal plays windowed.
      void fs.enter();
      remember(c);
      void play(c);
    },
    onCreate: () => toCreator(true),
    onDelete: (c) =>
      renderDeleteCharacter(screen, {
        character: c,
        onDeleted: (gone) => void enter(`${gone.name} has left the school.`),
        onCancel: () => void enter(),
      }),
    onSignOut: signOut,
  });
}

/** The creator; with `canGoBack` (the account has characters) it offers the way back to the picker. */
function toCreator(canGoBack: boolean): void {
  renderCreateCharacter(screen, {
    // Creating also picked it: preselect it on the picker.
    onCreated: (c) => {
      remember(c);
      void enter();
    },
    onSignOut: signOut,
    onBack: canGoBack ? () => void enter() : undefined,
  });
}

/** The session plays `c` from now on, then into the world. */
async function play(c: api.Character): Promise<void> {
  renderMessage(screen, { heading: "Lighting the candles", body: "Loading the castle…", busy: true });
  let character: api.Character;
  try {
    character = await api.selectCharacter(c.id);
  } catch (e) {
    if (e instanceof api.ApiError && e.status === 401) return;
    leaveGame();
    renderMessage(screen, {
      heading: "The castle is out of reach",
      body: e instanceof Error ? e.message : String(e),
      action: { label: "Back to your characters", run: () => void enter() },
    });
    return;
  }
  await startGame(character);
}

function rememberedCharacter(): string | null {
  try {
    return localStorage.getItem(LAST_CHARACTER_KEY);
  } catch {
    return null;
  }
}

function remember(c: api.Character): void {
  try {
    localStorage.setItem(LAST_CHARACTER_KEY, String(c.id));
  } catch {
    // Storage refused: the picker selects the first character.
  }
}

async function startGame(character: api.Character): Promise<void> {
  renderMessage(screen, { heading: "Lighting the candles", body: "Loading the castle…", busy: true });
  try {
    const { runGame } = await import("./game/game");
    stopGame();
    game = await runGame({
      character,
      onStatus: (heading, body) => renderMessage(screen, { heading, body, busy: true }),
      onReady: () => {
        screen.hidden = true;
      },
      onFatal: (heading, body) => {
        leaveGame();
        showScreen();
        renderMessage(screen, { heading, body, action: { label: "Return to the castle", run: () => void enter() }, secondary: { label: "Sign out", run: signOut } });
      },
      // Settings > Sign out: leave the world for the picker; the account stays signed in.
      onSignOut: () => void enter(),
    });
  } catch (e) {
    console.error(e);
    leaveGame();
    showScreen();
    renderMessage(screen, {
      heading: "Something went wrong",
      body: e instanceof Error ? e.message : String(e),
      action: { label: "Try again", run: () => void enter() },
    });
  }
}

/** Leaves the account: the session ends, back to sign in. */
function signOut(): void {
  leaveGame();
  api.logout().catch(() => undefined);
  toLogin("You have signed out.");
}

api.onUnauthorized(() => toLogin("Your session has ended. Please sign in again."));

/**
 * The card for browsers other than Google Chrome (browser.ts): the game only
 * supports Chrome. The address stays as it is, so a verify or reset link
 * still works once it is opened in Chrome.
 */
function askForChrome(): void {
  showScreen();
  const route = chromeRoute(navigator.userAgent, location.href);
  const getChrome = { label: "Get Google Chrome", run: () => window.open(CHROME_DOWNLOAD_URL, "_blank", "noopener") };
  if (route.kind === "android") {
    renderMessage(screen, {
      heading: "Please open SpellHollow School in Chrome",
      body: "SpellHollow School is made for Google Chrome, and other browsers draw parts of it wrong.",
      action: { label: "Open in Chrome", run: () => location.assign(route.intent) },
      secondary: getChrome,
    });
  } else if (route.kind === "ios") {
    renderMessage(screen, {
      heading: "Please play on a computer",
      body:
        "SpellHollow School is made for Google Chrome on a computer or an Android device. " +
        "On iPhone and iPad every browser, Chrome included, draws parts of it wrong.",
    });
  } else {
    const copy = (): void => {
      void navigator.clipboard?.writeText(location.href).then(
        () => renderMessage(screen, {
          heading: "Link copied",
          body: "Open Google Chrome and paste the link into its address bar.",
          action: { label: "Copy it again", run: copy },
          secondary: getChrome,
        }),
        () => undefined,
      );
    };
    renderMessage(screen, {
      heading: "Please open SpellHollow School in Chrome",
      body: "SpellHollow School is made for Google Chrome, and other browsers draw parts of it wrong. Copy this page's link and open it in Chrome.",
      action: { label: "Copy the link", run: copy },
      secondary: getChrome,
    });
  }
}

const boot = bootStep(location.search, api.loadSession() !== null);
if (!browserAllowed()) {
  askForChrome();
} else if (boot.kind === "verify") {
  verify(boot.token);
} else if (boot.kind === "reset") {
  reset(boot.token);
} else if (boot.kind === "lookup") {
  void enter();
} else {
  toLogin();
}
