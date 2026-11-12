//! Scripted NPCs: the
//! professor of each class section. An NPC is an entity of the instance it
//! stands in (`realtime::ext::WorldCtx::spawn_npc`), drawn with the
//! ordinary character system from its data (`sim_core::NpcDef`: name, house
//! colours, an appearance of catalog items). The class handler drives it:
//! `move_npc` to walk or turn, `npc_event` for its lines (`Say`), wand
//! demonstrations (`Demonstrate`, with `Anim::Cast`) and emotes. It is a
//! priority entity for every student (`set_instance_priority`), so even a
//! view limit of 20 in a full room always shows it. It goes with its
//! instance.

use protocol::EntityId;
use sim_core::NpcDef;

use crate::realtime::entities::ZoneId;
use crate::realtime::ext::{NpcSpawn, WorldCtx};

/// Adds the NPC `def` to instance `zone` at its place.
pub fn spawn(ctx: &mut WorldCtx<'_>, zone: ZoneId, def: &NpcDef) -> Option<EntityId> {
    ctx.spawn_npc(
        zone,
        NpcSpawn {
            npc: def.id.clone(),
            name: def.name.clone(),
            house: def.house(),
            appearance: def.appearance,
            pos: def.pos,
            yaw: def.yaw,
        },
    )
}

/// Adds class `class`'s professor to section `zone` and makes it the
/// section's priority entity.
pub fn spawn_professor(ctx: &mut WorldCtx<'_>, zone: ZoneId, class: &str) -> Option<EntityId> {
    let def = &crate::catalog::shipped().class(class)?.professor;
    let id = spawn(ctx, zone, def)?;
    ctx.set_instance_priority(zone, vec![id]);
    Some(id)
}
