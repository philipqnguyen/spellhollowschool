// Each synthesized sound's gain (docs/architecture.md
// "Sound and music"), measured on the preview page (preview.html: "Measure
// all" renders every recipe and prints this table). A recipe is rendered
// once, its loudness measured (the active RMS of a one-shot, the RMS of a
// loop) and its gain set so it lands on its family's target (TARGETS in
// preview.ts): the recipes stay readable and the mix lives in one place.
// Missing ids play at gain 1.

export const SYNTH_LEVELS: Readonly<Record<string, number>> = {
  "ui.click": 0.408, // rms -19.2 dBFS, peak -4 dBFS as written
  "ui.open": 0.484, // rms -20.7 dBFS, peak -6.3 dBFS as written
  "ui.close": 0.643, // rms -23.2 dBFS, peak -10.1 dBFS as written
  "ui.tab": 0.59, // rms -22.4 dBFS, peak -9.5 dBFS as written
  "ui.toggle": 0.83, // rms -25.4 dBFS, peak -11.4 dBFS as written
  "ui.slider": 0.678, // rms -23.6 dBFS, peak -9.3 dBFS as written
  "ui.error": 0.362, // rms -18.2 dBFS, peak -6.6 dBFS as written
  "ui.notify": 0.545, // rms -21.7 dBFS, peak -8 dBFS as written
  "ui.whisper": 1.528, // rms -30.7 dBFS, peak -15.8 dBFS as written
  "ui.toast": 0.678, // rms -23.6 dBFS, peak -9.3 dBFS as written
  "ui.invite": 0.674, // rms -23.6 dBFS, peak -9.4 dBFS as written
  "ui.select": 0.643, // rms -23.2 dBFS, peak -11.2 dBFS as written
  "ui.dye": 1.675, // rms -31.5 dBFS, peak -16.3 dBFS as written
  "ui.friend_online": 0.635, // rms -23.1 dBFS, peak -10.5 dBFS as written
  "spell.lucerna": 1.388, // rms -23.8 dBFS, peak -8.2 dBFS as written
  "spell.lucerna_off": 2.765, // rms -29.8 dBFS, peak -12.8 dBFS as written
  "spell.elevara": 1.767, // rms -25.9 dBFS, peak -9.7 dBFS as written
  "spell.levitate_end": 1.437, // rms -24.1 dBFS, peak -9.8 dBFS as written
  "spell.scutara": 0.992, // rms -20.9 dBFS, peak -8.4 dBFS as written
  "spell.torpeo": 0.771, // rms -18.7 dBFS, peak -1.7 dBFS as written
  "spell.dimitto": 0.805, // rms -19.1 dBFS, peak -0.8 dBFS as written
  "spell.titillara": 2.733, // rms -29.7 dBFS, peak -10.1 dBFS as written
  "spell.repulsa": 0.716, // rms -18.1 dBFS, peak -4.8 dBFS as written
  "spell.trail": 3.025, // rms -36.6 dBFS, peak -20.7 dBFS as written
  "spell.fizzle": 3.784, // rms -32.6 dBFS, peak -16.6 dBFS as written
  "spell.monstra_viam": 1.124, // rms -25 dBFS, peak -11.4 dBFS as written
  // Spells to level 20 (measured like the rest: preview page, Measure all).
  "spell.tinctura": 1.807, // rms -26.1 dBFS, peak -11.7 dBFS as written
  "spell.rigora": 1.208, // rms -22.6 dBFS, peak -3.4 dBFS as written
  "spell.volucra": 2.017, // rms -28.4 dBFS, peak -7.1 dBFS as written
  "spell.adveni": 1.73, // rms -26.3 dBFS, peak -5.8 dBFS as written
  "spell.dissolvo": 1.507, // rms -24.6 dBFS, peak -10.4 dBFS as written
  "spell.aperio": 2.28, // rms -28.2 dBFS, peak -11.9 dBFS as written
  "spell.tardara": 1.606, // rms -25.1 dBFS, peak -9.3 dBFS as written
  "spell.custodio": 1.702, // rms -25.6 dBFS, peak -9.5 dBFS as written
  "spell.bound": 2.172, // rms -27.7 dBFS, peak -11.5 dBFS as written
  "spell.slowed": 3.542, // rms -32 dBFS, peak -14.1 dBFS as written
  "spell.pulled": 1.899, // rms -26.6 dBFS, peak -6.7 dBFS as written
  "spell.dispelled": 1.924, // rms -26.7 dBFS, peak -13.7 dBFS as written
  "spell.recoloured": 2.24, // rms -30 dBFS, peak -14.8 dBFS as written
  "spell.reveal_glint": 1.589, // rms -30 dBFS, peak -14.1 dBFS as written
  "volucra.flutter": 3.544, // rms -35 dBFS, peak -18 dBFS as written
  "volucra.chirp": 1.442, // rms -29.2 dBFS, peak -17.1 dBFS as written
  "guardian.summon": 1.679, // rms -26.5 dBFS, peak -10.9 dBFS as written
  "guardian.ambient": 0.418, // rms -23.4 dBFS, peak -6 dBFS as written
  "guardian.fade": 2.112, // rms -30.5 dBFS, peak -14.1 dBFS as written
  "duel.rooted": 1.095, // rms -22.5 dBFS, peak -1.8 dBFS as written
  "duel.slowed": 2.34, // rms -27.4 dBFS, peak -10.1 dBFS as written
  "duel.pulled": 1.336, // rms -22.5 dBFS, peak -6.3 dBFS as written
  "duel.cleansed": 1.332, // rms -22.5 dBFS, peak -9.4 dBFS as written
  "duel.ward_up": 1.662, // rms -24.4 dBFS, peak -9.2 dBFS as written
  "duel.warded": 0.952, // rms -19.6 dBFS, peak -0.8 dBFS as written
  "duel.ward_faded": 2.261, // rms -33.1 dBFS, peak -16.9 dBFS as written
  "duel.countdown": 0.666, // rms -20.5 dBFS, peak -5.1 dBFS as written
  "duel.fight": 0.876, // rms -18.8 dBFS, peak -2.2 dBFS as written
  "duel.charge": 1.421, // rms -23.1 dBFS, peak -10.3 dBFS as written
  "duel.blocked": 1.044, // rms -20.4 dBFS, peak -2.4 dBFS as written
  "duel.reflected": 1.215, // rms -21.7 dBFS, peak -7 dBFS as written
  "duel.shield_broken": 1.53, // rms -23.7 dBFS, peak -4.9 dBFS as written
  "duel.hit": 0.641, // rms -16.1 dBFS, peak -2.1 dBFS as written
  "duel.disarmed": 1.369, // rms -26.5 dBFS, peak -3.7 dBFS as written
  "duel.interrupted": 2.926, // rms -29.3 dBFS, peak -14.4 dBFS as written
  "duel.missed": 4.09, // rms -32.2 dBFS, peak -17.7 dBFS as written
  "potion.wear_off": 2.005, // rms -29 dBFS, peak -14.7 dBFS as written
  "potion.glow": 1.525, // rms -26.7 dBFS, peak -12.2 dBFS as written
  "potion.chameleon": 2.249, // rms -30 dBFS, peak -18 dBFS as written
  "potion.shrinking": 1.908, // rms -28.6 dBFS, peak -14.3 dBFS as written
  "potion.swelling": 1.044, // rms -23.4 dBFS, peak -7.6 dBFS as written
  "potion.babbling": 3.166, // rms -33 dBFS, peak -18.4 dBFS as written
  "potion.mimic_draught": 1.752, // rms -27.9 dBFS, peak -13.9 dBFS as written
  "potion.fortunes_draught": 1.599, // rms -27.1 dBFS, peak -12.4 dBFS as written
  "potion.pick_me_up_tonic": 0.509, // rms -17.1 dBFS, peak -5 dBFS as written
  "potion.invisibility": 2.258, // rms -30.1 dBFS, peak -13.3 dBFS as written
  "potion.fortune": 1.243, // rms -24.9 dBFS, peak -9.8 dBFS as written
  "secret.reveal": 1.874, // rms -27.5 dBFS, peak -7.9 dBFS as written
  "secret.pace": 0.689, // rms -22.8 dBFS, peak -9.2 dBFS as written
  "secret.door_appear": 1.678, // rms -26.5 dBFS, peak -8 dBFS as written
  "secret.door_vanish": 2.898, // rms -31.2 dBFS, peak -13 dBFS as written
  "level.up": 1.471, // rms -24.4 dBFS, peak -9.4 dBFS as written
  "class.bell": 0.916, // rms -23.2 dBFS, peak -7.6 dBFS as written
  "class.join": 0.976, // rms -23.8 dBFS, peak -10.3 dBFS as written
  "class.beat_tick": 0.488, // rms -20.8 dBFS, peak -6.8 dBFS as written
  "class.beat_open": 0.749, // rms -22.5 dBFS, peak -6.2 dBFS as written
  "class.grade_perfect": 0.965, // rms -23.7 dBFS, peak -8.8 dBFS as written
  "class.grade_great": 1.115, // rms -24.9 dBFS, peak -9.6 dBFS as written
  "class.grade_good": 1.418, // rms -27 dBFS, peak -11.9 dBFS as written
  "class.miss": 0.778, // rms -23.8 dBFS, peak -9.8 dBFS as written
  "class.reject": 0.623, // rms -22.9 dBFS, peak -11.5 dBFS as written
  "class.pick": 0.53, // rms -21.5 dBFS, peak -9.5 dBFS as written
  "class.ring_glow": 1.564, // rms -29.9 dBFS, peak -18.6 dBFS as written
  "class.ring_through": 1.507, // rms -26.6 dBFS, peak -9.3 dBFS as written
  "class.fx.charms": 2.848, // rms -33.1 dBFS, peak -21.2 dBFS as written
  "class.fx.potions": 1.766, // rms -28.9 dBFS, peak -13.2 dBFS as written
  "class.fx.transfiguration": 1.524, // rms -27.7 dBFS, peak -10.8 dBFS as written
  "class.fx.defence": 0.526, // rms -18.4 dBFS, peak -4.8 dBFS as written
  "class.fx.history": 5.51, // rms -38.8 dBFS, peak -20.3 dBFS as written
  "class.fx.divination": 1.442, // rms -27.2 dBFS, peak -13.8 dBFS as written
  "class.fx.astronomy": 1.851, // rms -29.3 dBFS, peak -15.7 dBFS as written
  "class.fx.herbology": 2.672, // rms -32.5 dBFS, peak -16.6 dBFS as written
  "class.fx.creatures": 1.199, // rms -25.6 dBFS, peak -14 dBFS as written
  "entrance.statue": 1.838, // rms -26.3 dBFS, peak -7 dBFS as written
  "entrance.wall": 1.459, // rms -24.3 dBFS, peak -6.3 dBFS as written
  "stairs.warning": 1.815, // rms -26.2 dBFS, peak -10.9 dBFS as written
  "stairs.swing": 0.663, // rms -20.4 dBFS, peak -6 dBFS as written
  "stairs.dock": 0.601, // rms -16.6 dBFS, peak -4 dBFS as written
  "stairs.gate": 1.4, // estimated (not yet measured on the preview page): a soft poof, near craft.fail
  "broom.mount": 2.407, // rms -29.6 dBFS, peak -15.5 dBFS as written
  "broom.land": 1.583, // rms -28 dBFS, peak -11.5 dBFS as written
  "broom.wind": 0.523, // rms -18.4 dBFS, peak -4.4 dBFS as written
  "broom.flyby": 2.105, // rms -28.5 dBFS, peak -13.5 dBFS as written
  "craft.add": 0.872, // rms -22.8 dBFS, peak -7.1 dBFS as written
  "craft.stir": 3.006, // rms -33.6 dBFS, peak -17.4 dBFS as written
  "craft.success": 1.139, // rms -25.1 dBFS, peak -10.1 dBFS as written
  "craft.fail": 1.687, // rms -28.5 dBFS, peak -12.4 dBFS as written
  "find.found": 0.973, // rms -23.8 dBFS, peak -8.8 dBFS as written
  "find.nothing": 1.491, // rms -27.5 dBFS, peak -15.6 dBFS as written
  "mail.arrive": 0.51, // rms -17.1 dBFS, peak -8.7 dBFS as written
  "owl.hoot": 0.452, // rms -15.1 dBFS, peak -7.3 dBFS as written
  "trade.complete": 1.284, // rms -26.2 dBFS, peak -11.6 dBFS as written
  "cup.points": 2.257, // rms -31.1 dBFS, peak -13.3 dBFS as written
  "world.clock_bell": 1.071, // rms -20.6 dBFS, peak -4.7 dBFS as written
  "amb.arena": 0.1, // rms -24 dBFS, peak -18.3 dBFS as written (a steady drone, no room tone: -44 dBFS)
  "amb.wishing_room": 0.126, // rms -24 dBFS, peak -11.8 dBFS as written (the shimmer alone, no room tone: -42 dBFS)
  "amb.sky": 0.796, // rms -24 dBFS, peak -9 dBFS as written
  "amb.under_lake": 0.18, // estimated (not yet measured on the preview page): -39 dBFS, a wash under the hearth
  "amb.tower": 0.632, // rms -24 dBFS, peak -8.5 dBFS as written
  "loop.cauldron": 0.626, // rms -19.9 dBFS, peak -5.3 dBFS as written
  "loop.owls": 0.796, // rms -24 dBFS, peak -9 dBFS as written
  "sprinkle.owl_far": 0.288, // rms -13.2 dBFS, peak -5.3 dBFS as written
  "sprinkle.drip": 0.501, // rms -18 dBFS, peak -5.8 dBFS as written
  "sprinkle.gust": 1.352, // rms -28.6 dBFS, peak -13.7 dBFS as written
  "sprinkle.frog": 2.061, // rms -31.3 dBFS, peak -13.8 dBFS as written
};
