// Every keyboard and mouse shortcut, for the Settings window's "Keyboard
// shortcuts" tab (ui/hud.ts). The keys themselves live with their features
// (game/input.ts, game/game.ts, the windows' own handlers, decorating,
// spells, duels, the tour); this list is what the player reads. Keep it in
// step when a key changes.

export interface Shortcut {
  /** The keys, each shown as its own key cap ("Shift+Tab" is one cap). */
  keys: readonly string[];
  what: string;
}

export interface ShortcutGroup {
  title: string;
  items: readonly Shortcut[];
}

export const SHORTCUTS: readonly ShortcutGroup[] = [
  {
    title: "Moving",
    items: [
      { keys: ["W", "A", "S", "D"], what: "Move" },
      { keys: ["Z"], what: "Walk or run" },
      { keys: ["R"], what: "Auto run (R, S or Esc stops it)" },
      { keys: ["Tap", "Hold"], what: "Seated on an open bench: tap the move key that points behind you to turn round; hold it to stand up" },
      { keys: ["←", "→", "↑", "↓"], what: "Turn and tilt the camera" },
      { keys: ["Drag"], what: "Look around" },
      { keys: ["Wheel"], what: "Zoom" },
    ],
  },
  {
    title: "On a broom",
    items: [
      { keys: ["B"], what: "Mount or land" },
      { keys: ["Space"], what: "Climb" },
      { keys: ["Shift"], what: "Dive" },
      { keys: ["Drag"], what: "Steer (the broom follows the view)" },
    ],
  },
  {
    title: "People and chat",
    items: [
      { keys: ["Click"], what: "Select someone" },
      { keys: ["Tab", "Shift+Tab"], what: "Next or previous person nearby" },
      { keys: ["E", "Double-click"], what: "Open the selected person's card" },
      { keys: ["Right-click"], what: "A player's menu: trade, duel, examine, invite to party" },
      { keys: ["Enter"], what: "Chat" },
      { keys: ["/"], what: "Chat command (/tell name …, /p party, /invite name, /leave)" },
      { keys: ["Esc"], what: "Clear the selection, then close the top window" },
    ],
  },
  {
    title: "Windows",
    items: [
      { keys: ["U"], what: "Players" },
      { keys: ["I"], what: "Wardrobe" },
      { keys: ["O"], what: "Friends" },
      { keys: ["M"], what: "Map" },
      { keys: ["N"], what: "Mail" },
      { keys: ["P"], what: "Potions" },
      { keys: ["K"], what: "Skills" },
      { keys: ["Y"], what: "Spellbook: your spells (or the level badge)" },
      { keys: ["H"], what: "House Cup" },
      { keys: ["F"], what: "Find my way (Wayfinder Charm)" },
      { keys: ["F3"], what: "Performance stats" },
      { keys: ["Drag"], what: "Drag a window's title to move it" },
      { keys: ["Double-click"], what: "A window's title: back to where it opens" },
    ],
  },
  {
    title: "Doing things",
    items: [
      { keys: ["E"], what: "Use the door, station or cauldron in reach (the Owl Order Catalogue: its stand in your common room)" },
      { keys: ["L"], what: "Wand light" },
      { keys: ["V"], what: "Levitate" },
      { keys: ["Tab", "←", "→"], what: "Aiming a spell: choose the target" },
      { keys: ["E", "V", "Enter"], what: "Aiming a spell: cast" },
      // Spells L20 (SOCIAL): the spells aimed at a friend.
      { keys: ["Tab"], what: "Select a friend, then cast the Colour Charm, Binding Curse, Summoning Charm, Unweaving or Slowing Jinx on them from the spellbook (Y), their card or /cast" },
      { keys: ["Esc"], what: "Nothing selected: the Colour Charm and the Unweaving work on you" },
      { keys: ["1–9", "0"], what: "Cast the spell in that hotbar slot (bottom of the screen). Drag slots to rearrange them, drag a spell's glyph onto one from the spellbook (Y), right-click a slot to remove it" },
      { keys: ["1–9"], what: "In a duel: cast that spell (the hotbar hides)" },
      // Spells L20 (DUELCLIENT): the duel keys.
      { keys: ["A", "D"], what: "In a duel: sidestep along the stage (not while bound or staggered)" },
      { keys: ["1–5"], what: "In a duel, bound or slowed: the Unweaving's key glows; cast it to break free" },
      { keys: ["X"], what: "First-day tour: skip a stop" },
    ],
  },
  {
    title: "Decorating your dorm",
    items: [
      { keys: ["B"], what: "Start or stop decorating" },
      { keys: ["Q", "E"], what: "Turn the piece" },
      { keys: ["G"], what: "Precise placement" },
      { keys: ["Delete"], what: "Put the piece away" },
      { keys: ["Esc"], what: "Cancel, then stop decorating" },
    ],
  },
];
