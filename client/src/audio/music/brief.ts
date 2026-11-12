// The music brief (docs/architecture.md "Sound and music"):
// for each piece, its style, mood, pace and palette, and how its night
// variant differs. Every id gets one original score written to it.
//
// The mix by place: the castle and the Great Hall gentle orchestral (soft
// strings, woodwinds, soft brass); the grounds, the lake and the common rooms
// Celtic/folk (harp, fiddle, flute); classes, magical moments and night
// whimsical music box and celesta touches; duels and flying a little more
// energetic. Night variants are softer. A rest between plays so it never tires.

import type { MusicId, StingerId } from "../catalogue";
import type { Instrument, Style } from "./score";

export interface Brief {
  style: Style;
  mood: string;
  /** Suggested tempo range (bpm). */
  bpm: readonly [number, number];
  /** Length of one play of the form (s), before its rest. */
  lengthS: readonly [number, number];
  palette: readonly Instrument[];
  night: string;
}

export const MUSIC_BRIEF: Readonly<Record<MusicId, Brief>> = {
  "music.castle": { style: "orchestral", mood: "wandering the halls: curious, warm, unhurried", bpm: [72, 92], lengthS: [100, 150], palette: ["strings", "clarinet", "flute", "horn", "harp", "cello"], night: "strings and celesta only, slower, hushed" },
  "music.great_hall": { style: "orchestral", mood: "the heart of the school: welcoming, a little grand, candlelit", bpm: [76, 96], lengthS: [100, 150], palette: ["strings", "horn", "oboe", "flute", "harp", "timpani"], night: "no timpani, horn soft, a celesta descant" },
  "music.dungeons": { style: "orchestral", mood: "cold stone, secrets, a sly minor lilt", bpm: [64, 84], lengthS: [90, 140], palette: ["cello", "bassoon", "clarinet", "pizz", "celesta"], night: "same piece; dungeons have no daylight (pizz thinner)" },
  "music.grounds": { style: "folk", mood: "open air and green hills, a walking tune", bpm: [88, 112], lengthS: [90, 140], palette: ["harp", "fiddle", "flute", "whistle", "frame", "bass"], night: "harp and flute alone, slow air, no frame drum" },
  "music.lake": { style: "folk", mood: "wide still water, mist, distance", bpm: [60, 80], lengthS: [100, 150], palette: ["harp", "whistle", "strings", "cello"], night: "harp and celesta, very sparse" },
  "music.common_room": { style: "folk", mood: "fireside, friends, home", bpm: [72, 96], lengthS: [90, 140], palette: ["harp", "fiddle", "flute", "cello", "pizz"], night: "a lullaby tempo, harp and flute, quieter" },
  "music.dorm": { style: "celesta", mood: "a music box on the bedside table", bpm: [60, 76], lengthS: [70, 110], palette: ["musicbox", "celesta", "harp", "strings"], night: "music box alone over a faint string pad" },
  "music.class": { style: "celesta", mood: "lessons: playful, inquisitive, light on its feet (under the professor's words)", bpm: [96, 116], lengthS: [80, 120], palette: ["celesta", "musicbox", "pizz", "clarinet", "glock"], night: "celesta and pizz only" },
  "music.class_grounds": { style: "folk", mood: "lessons outdoors: earthy, cheerful, a sparkle of magic", bpm: [92, 112], lengthS: [80, 120], palette: ["harp", "whistle", "fiddle", "celesta", "shaker"], night: "harp and celesta, no shaker" },
  "music.astronomy": { style: "celesta", mood: "the night sky from the tower: stars, awe, slow turning", bpm: [56, 72], lengthS: [90, 140], palette: ["celesta", "glock", "strings", "choir", "harp", "flute"], night: "this piece is the night; its day variant adds a flute" },
  "music.flying": { style: "energetic", mood: "wind, speed, joy in the air", bpm: [120, 144], lengthS: [70, 110], palette: ["fiddle", "strings", "whistle", "horn", "frame", "harp"], night: "same drive, strings and harp, frame drum soft" },
  "music.duel": { style: "energetic", mood: "a contest of skill: tense, driving, never brutal", bpm: [126, 150], lengthS: [60, 100], palette: ["strings", "pizz", "horn", "timpani", "frame", "celesta"], night: "unchanged (duels are indoors)" },
  "music.wishing_room": { style: "celesta", mood: "a room that answers a wish: wonder, hush, kindness", bpm: [60, 80], lengthS: [80, 120], palette: ["celesta", "choir", "harp", "strings", "musicbox"], night: "the same, a little slower" },
};

/** Stingers: a few seconds each, our own motifs. */
export const STINGER_BRIEF: Readonly<Record<StingerId, string>> = {
  "stinger.welcome": "3-5 s: a door opening onto warm light; harp roll and strings",
  "stinger.level_up": "2-3 s: a rising celesta figure, a bright chord",
  "stinger.discovery": "3-4 s: a hushed 'oh!': celesta and choir, an unexpected chord",
  "stinger.class_result": "2-3 s: a pleased cadence, pizz and clarinet",
  "stinger.duel_won": "3-4 s: horn and strings, a short proud cadence",
  "stinger.duel_lost": "3-4 s: a gentle falling phrase, not mournful: next time",
  "stinger.cup_week": "4-6 s: a small fanfare, soft brass and timpani roll",
};
