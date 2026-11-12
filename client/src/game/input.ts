// Keyboard state for movement and the camera. Keys are read by `event.code`
// (physical position), so WASD works on AZERTY and other layouts too. Typing
// in a text field (chat) never moves the player.
//
// The keys (docs/architecture.md "Client"):
//   W A S D     move: running by default, walking after Z (Z again runs; the
//               choice lasts the session). Shift no longer changes the speed
//               on foot. On a broom W / S set the speed, A / D turn, Space
//               climbs and Shift dives.
//   arrows      the camera: Left / Right orbit it round the character, Up /
//               Down tilt it (as a mouse drag does). They never move the
//               character. A focused control that uses arrows (the class
//               trace pad, the trade window's list, tabs, sliders, a text
//               field) keeps them, and so does spell targeting (game.ts
//               asks the camera last).
//   R           auto run: forward as if W were held, at the Z mode's speed
//               (run by default; on a broom: forward like W). The camera's
//               facing steers, A and D steer as with W. R again, S or Esc
//               stop it; game.ts also stops it on a zone change or teleport,
//               mounting or landing, a class, duel, trade or crafting station
//               starting, an emote, and when the character has been stuck (a
//               wall) for a second. Typing in chat it keeps going.
//
// `InputState` is the pure part (unit tested, input.test.ts); `Keyboard`
// feeds it the window's key events.

export interface MoveInput {
  /** +1 forward, -1 back, 0 neither. */
  forward: number;
  /** +1 right, -1 left. */
  strafe: number;
  /** On foot: run (the Z mode); on a broom: Shift, dive. */
  run: boolean;
  /** Space: on a broom, climb (Shift, `run`, dives). Optional so callers that only walk can leave it out. */
  up?: boolean;
}

/** The arrow keys held for the camera: +1 right / up, -1 left / down. */
export interface CameraKeys {
  yaw: number;
  pitch: number;
}

const FORWARD = new Set(["KeyW"]);
const BACK = new Set(["KeyS"]);
const LEFT = new Set(["KeyA"]);
const RIGHT = new Set(["KeyD"]);
const RUN = new Set(["ShiftLeft", "ShiftRight"]);
const UP = new Set(["Space"]);
const CAMERA_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);

/** How fast Left / Right orbit the camera (rad/s): the broom's A / D rate, a half turn in two seconds. */
export const CAMERA_YAW_RATE = 1.6;
/** How fast Up / Down tilt it (rad/s): the whole pitch range in about a second and a half. */
export const CAMERA_PITCH_RATE = 0.9;
/** Auto run stops when the character has gone less than this far (m) ... */
export const STALL_DISTANCE = 0.5;
/** ... in this long (s): pressed against a wall. */
export const STALL_SECONDS = 1;

/** Input types that take typed text; radios, checkboxes and buttons don't. */
const TEXT_INPUTS = new Set(["text", "email", "password", "search", "number", "date", "url", "tel"]);
/** Roles whose widgets move with the arrow keys (ARIA keyboard patterns). */
const ARROW_ROLES = new Set(["tab", "tablist", "menu", "menuitem", "menubar", "listbox", "option", "slider", "spinbutton", "radio", "radiogroup", "grid", "tree", "treeitem"]);

/** Whether keys pressed on `target` are text being typed (and so must not move the player). */
export function isTyping(target: EventTarget | null): boolean {
  if (typeof HTMLElement === "undefined" || !(target instanceof HTMLElement)) return false;
  if (target instanceof HTMLInputElement) return TEXT_INPUTS.has(target.type);
  return target.isContentEditable || target.tagName === "TEXTAREA" || target.tagName === "SELECT";
}

/** Whether the focused `target` uses the arrow keys itself (a slider, a radio, tabs, a menu or list). */
export function ownsArrows(target: EventTarget | null): boolean {
  if (typeof HTMLElement === "undefined" || !(target instanceof HTMLElement)) return false;
  if (isTyping(target) || target instanceof HTMLInputElement || target.tagName === "SELECT") return true;
  const role = target.getAttribute("role");
  if (role && ARROW_ROLES.has(role)) return true;
  return !!target.closest('[role="menu"], [role="listbox"], [role="tablist"], [role="radiogroup"], [role="grid"], [role="tree"]');
}

/** What decides whether an arrow key turns the camera. */
export interface ArrowContext {
  /** Typed into a text field (chat, search). */
  typing: boolean;
  /** Something else took it: a handler called preventDefault (the trade list, tabs) or claimed it (spell targeting). */
  claimed: boolean;
  /** The focused control uses arrows (`ownsArrows`). */
  owned: boolean;
}

/** Why auto run stopped (for tests and the debug log; the HUD only shows on / off). */
export type AutoRunStop = "toggle" | "back" | "escape" | "zone" | "mount" | "activity" | "emote" | "stuck";

/**
 * The server messages that stop auto run: arriving in a zone (a door, a
 * teleport, a respawn, a reconnect), a class or duel starting, a crafting
 * station opening, mounting or landing (the server's word). A trade opening,
 * an emote, R / S / Esc and a wall are game.ts's.
 */
export function autoRunStopFor(type: string): AutoRunStop | null {
  switch (type) {
    case "welcome":
    case "zoneChanged":
      return "zone";
    case "mounted":
      return "mount";
    case "classJoined":
    case "duelStart":
    case "stationOpened":
      return "activity";
    default:
      return null;
  }
}

/** Keys held, the camera's arrows and auto run. Pure: no DOM. */
export class InputState {
  private readonly down = new Set<string>();
  private readonly camera = new Set<string>();
  private auto = false;
  private walk = false;
  /** The last reason auto run stopped (null while it runs or never ran). */
  lastStop: AutoRunStop | null = null;

  /** A movement key pressed outside a text field. S stops auto run. */
  keyDown(code: string): void {
    this.down.add(code);
    if (BACK.has(code)) this.stopAutoRun("back");
  }

  /** Any key released: forgets it for movement and the camera alike. */
  keyUp(code: string): void {
    this.down.delete(code);
    this.camera.delete(code);
  }

  /** An arrow key that nothing else took: true if it now turns the camera (the caller prevents its default). */
  cameraKeyDown(code: string, ctx: ArrowContext): boolean {
    if (!CAMERA_KEYS.has(code) || ctx.typing || ctx.claimed || ctx.owned) return false;
    this.camera.add(code);
    return true;
  }

  /** Forget held keys (movement and camera), e.g. when the chat box takes focus mid-stride. Auto run keeps going. */
  clear(): void {
    this.down.clear();
    this.camera.clear();
  }

  get autoRun(): boolean {
    return this.auto;
  }

  /** Z's mode: walking instead of running on foot (off by default). */
  get walking(): boolean {
    return this.walk;
  }

  /** Z: walk, or run again (`to` sets it). Returns whether we now walk. */
  toggleWalk(to = !this.walk): boolean {
    this.walk = to;
    return this.walk;
  }

  /** R: auto run on, or off again. */
  toggleAutoRun(): boolean {
    if (this.auto) this.stopAutoRun("toggle");
    else {
      this.auto = true;
      this.lastStop = null;
    }
    return this.auto;
  }

  /** Stops auto run; true if it was running. */
  stopAutoRun(why: AutoRunStop): boolean {
    if (!this.auto) return false;
    this.auto = false;
    this.lastStop = why;
    return true;
  }

  /**
   * The movement keys this frame. `held` false (typing, Settings open) reads
   * no keys but auto run still runs. On foot `run` is the Z mode (Shift
   * does nothing); on a broom (`flying`) it is Shift, the dive. Auto run is
   * W held.
   */
  read(flying = false, held = true): MoveInput {
    const any = (keys: Set<string>) => {
      if (!held) return false;
      for (const k of keys) if (this.down.has(k)) return true;
      return false;
    };
    const forward = (any(FORWARD) ? 1 : 0) - (any(BACK) ? 1 : 0);
    return {
      forward: this.auto ? 1 : forward,
      strafe: (any(RIGHT) ? 1 : 0) - (any(LEFT) ? 1 : 0),
      run: flying ? any(RUN) : !this.walk,
      up: any(UP),
    };
  }

  /** The camera's arrows held: yaw +1 Right, -1 Left; pitch +1 Up (look up), -1 Down. */
  cameraKeys(): CameraKeys {
    const c = this.camera;
    return {
      yaw: (c.has("ArrowRight") ? 1 : 0) - (c.has("ArrowLeft") ? 1 : 0),
      pitch: (c.has("ArrowUp") ? 1 : 0) - (c.has("ArrowDown") ? 1 : 0),
    };
  }
}

/**
 * How far the camera's arrows turn it this frame: `dAlpha` for
 * `FollowCamera.turn` (positive turns the view right) and `dBeta` for
 * `FollowCamera.tilt` (positive looks up: the camera sinks, as dragging the
 * mouse up does). A long frame turns no more than a tenth of a second's worth.
 */
export function cameraStep(keys: CameraKeys, dt: number): { dAlpha: number; dBeta: number } {
  const t = Math.min(Math.max(dt, 0), 0.1);
  return { dAlpha: keys.yaw * CAMERA_YAW_RATE * t, dBeta: keys.pitch * CAMERA_PITCH_RATE * t };
}

/** Tells auto run it is stuck: less than STALL_DISTANCE gained in STALL_SECONDS. */
export class StallWatch {
  private x = 0;
  private y = 0;
  private z = 0;
  private t = 0;
  private armed = false;

  /** Starts again from wherever the next `step` is (auto run off, or just stopped). */
  reset(): void {
    this.armed = false;
  }

  /** One frame of auto run at (x, y, z): true once it has been stuck for STALL_SECONDS. */
  step(x: number, y: number, z: number, dt: number): boolean {
    if (!this.armed || (x - this.x) ** 2 + (y - this.y) ** 2 + (z - this.z) ** 2 >= STALL_DISTANCE * STALL_DISTANCE) {
      this.x = x;
      this.y = y;
      this.z = z;
      this.t = 0;
      this.armed = true;
      return false;
    }
    this.t += dt;
    return this.t >= STALL_SECONDS;
  }
}

/** sessionStorage key for Z's walk mode (this tab's session). */
const WALK_KEY = "shs.walk";

function readWalk(): boolean {
  try {
    return sessionStorage.getItem(WALK_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * The window's key events into an `InputState`. Arrow keys are not taken
 * here: game.ts offers them to `cameraKeyDown` after spell targeting and the
 * other key handlers have had them (it runs after this listener).
 */
export class Keyboard {
  readonly state = new InputState();
  private readonly onDown = (e: KeyboardEvent) => {
    if (isTyping(e.target) || CAMERA_KEYS.has(e.code)) return;
    this.state.keyDown(e.code);
  };
  private readonly onUp = (e: KeyboardEvent) => this.state.keyUp(e.code);
  private readonly onBlur = () => this.state.clear();

  constructor() {
    if (readWalk()) this.state.toggleWalk(true);
    window.addEventListener("keydown", this.onDown);
    window.addEventListener("keyup", this.onUp);
    window.addEventListener("blur", this.onBlur);
  }

  /** Forget held keys, e.g. when the chat box takes focus mid-stride. */
  clear(): void {
    this.state.clear();
  }

  /** An arrow key reaching the game unclaimed: true if it turns the camera (then prevent its default). */
  cameraKeyDown(e: KeyboardEvent): boolean {
    return this.state.cameraKeyDown(e.code, { typing: isTyping(e.target), claimed: e.defaultPrevented, owned: ownsArrows(e.target) });
  }

  read(flying = false, held = true): MoveInput {
    return this.state.read(flying, held);
  }

  /** Z: walk or run, remembered for the browser tab's session. Returns whether we now walk. */
  toggleWalk(): boolean {
    const walk = this.state.toggleWalk();
    try {
      if (walk) sessionStorage.setItem(WALK_KEY, "1");
      else sessionStorage.removeItem(WALK_KEY);
    } catch {
      // Storage blocked: the mode lasts until the page reloads.
    }
    return walk;
  }

  dispose(): void {
    window.removeEventListener("keydown", this.onDown);
    window.removeEventListener("keyup", this.onUp);
    window.removeEventListener("blur", this.onBlur);
  }
}
