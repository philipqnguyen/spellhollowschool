//! The Wishing Room in the world (docs/architecture.md
//! "Secrets and the map"; `data/world/secrets.json` `wishing_room`).
//!
//! - **The door.** Pacing the seventh-floor east corridor opens it for one
//!   character (`explore`); `WishingRoomDoor { open_until_ms }` tells them.
//!   Using it (`Interact`) is answered with nothing: the client offers the
//!   two rooms. [`WorldSim::on_enter_wishing_room`] checks the door is open
//!   for them, they stand in its reach and are not riding, then takes them
//!   in (`WishingRoomDoor { 0 }`: the door is used up).
//! - **The rooms** run on the dorm instance code (`instances`): one
//!   `DormInstance` with `wishing_room` set per opener while anyone is in it
//!   (`Instances::of_wishing_room`); entering again while it is occupied
//!   joins it, whatever room was asked for. At most `capacity` people.
//! - **Guests** come the dorm way: the opener's `DormInvite` from inside
//!   ([`WorldSim::own_wishing_room`]: `DormInviteReceived.wishing_room`),
//!   `DormSetOpen` opens it to friends and `DormVisit` by the opener's name
//!   joins it while open, `DormKick` sends a guest out (barred for a while,
//!   as from a dorm), and closing it again locks it.
//! - **Leaving** (the room's exit, `LeaveInstance`, a kick, the opener going
//!   offline) puts everyone in front of the door on the corridor: that is
//!   also where a character in a room is saved. An empty room closes.

use std::time::Instant;

use protocol::{DormErrorCode, ServerMsg, WishingRoomKind, yaw_to_u8};

use super::entities::{CASTLE, ReturnPoint};
use super::world::WorldSim;

impl WorldSim {
    /// The room of the Wishing Room character `i` opened and stands
    /// in, if it does.
    pub(super) fn own_wishing_room(&self, i: usize) -> Option<WishingRoomKind> {
        let d = self.instances.get(self.ents.zone[i])?;
        (d.owner == self.ents.info[i].character_id)
            .then_some(d.wishing_room)
            .flatten()
    }

    /// Where everyone comes out: in front of the door on the corridor,
    /// facing its way.
    pub(super) fn wishing_room_door_spot(&self) -> Option<([f32; 3], u8)> {
        let rules = self.explore_index.wishing_room()?;
        let door = self.castle.interactable(&rules.door)?;
        let g = self
            .castle
            .ground_at(door.pos[0], door.pos[2], door.pos[1])?;
        Some(([door.pos[0], g.y, door.pos[2]], yaw_to_u8(door.yaw)))
    }

    /// `ClientMsg::EnterWishingRoom`: through the door the pacing opened.
    pub(super) fn on_enter_wishing_room(&mut self, i: usize, at: Instant, room: WishingRoomKind) {
        if !self.take_action(i, at, None) {
            return;
        }
        let Some(rules) = self.explore_index.wishing_room() else {
            return self.dorm_error(i, DormErrorCode::Closed, None);
        };
        let (door, capacity) = (rules.door.clone(), usize::from(rules.capacity));
        let layout = rules.room(room).layout.clone();
        let cid = self.ents.info[i].character_id;
        let now_ms = u64::try_from(self.clock.unix_ms_at(at)).unwrap_or(0);
        if self.ents.zone[i] != CASTLE || !self.wishing_room_door_open(cid, now_ms) {
            return self.dorm_error(i, DormErrorCode::Closed, None);
        }
        if self.ents.mover[i].mount.is_some() {
            return self.dorm_error(i, DormErrorCode::Mounted, None);
        }
        let pos = self.ents.pos[i];
        if !self
            .castle
            .interactable(&door)
            .is_some_and(|d| d.in_reach(pos))
        {
            return self.dorm_error(i, DormErrorCode::TooFar, None);
        }
        // One room per opener while it is occupied: entering joins it.
        let zone = match self.instances.of_wishing_room(cid) {
            Some(z) => z,
            None => {
                let Some(world) = self.instance_layouts.get(&layout).cloned() else {
                    tracing::error!(%layout, "no Wishing Room layout");
                    return self.dorm_error(i, DormErrorCode::Closed, None);
                };
                let name = self.ents.info[i].name.clone();
                let house = self.ents.house[i];
                self.instances
                    .create_wishing_room(cid, &name, house, room, &layout, world)
            }
        };
        if self.instances.members(zone).len() >= capacity {
            return self.dorm_error(i, DormErrorCode::Full, None);
        }
        self.close_wishing_room_door(cid);
        self.send_msg(i, &ServerMsg::WishingRoomDoor { open_until_ms: 0 });
        self.enter_wishing_room_zone(i, zone, at);
    }

    /// Takes character `i` into the live Wishing Room of `owner`
    /// (an invitation or a visit; the caller checked they are welcome).
    pub(super) fn enter_wishing_room_as_guest(
        &mut self,
        i: usize,
        owner: i64,
        at: Instant,
    ) -> Result<(), DormErrorCode> {
        let Some(zone) = self.instances.of_wishing_room(owner) else {
            return Err(DormErrorCode::Closed);
        };
        if self.ents.zone[i] == zone {
            return Ok(());
        }
        let capacity = self
            .explore_index
            .wishing_room()
            .map_or(usize::from(protocol::WISHING_ROOM_MAX), |r| {
                usize::from(r.capacity)
            });
        if self.instances.members(zone).len() >= capacity {
            return Err(DormErrorCode::Full);
        }
        self.enter_wishing_room_zone(i, zone, at);
        Ok(())
    }

    /// Moves character `i` into Wishing Room `zone`, at its entry,
    /// to come out in front of the door.
    fn enter_wishing_room_zone(&mut self, i: usize, zone: super::entities::ZoneId, at: Instant) {
        let Some(world) = self.instances.get(zone).map(|d| d.world.clone()) else {
            return;
        };
        let back = self
            .wishing_room_door_spot()
            .unwrap_or((self.ents.pos[i], self.ents.state[i].yaw));
        let (pos, _, yaw) = self.spawn_in(&world, None);
        self.move_to_zone(
            i,
            zone,
            pos,
            yaw,
            Some(ReturnPoint::Castle {
                pos: back.0,
                yaw: back.1,
            }),
            at,
        );
        self.metrics.wishing_room_entries.inc();
    }

    /// `DormSetOpen` inside one's own Wishing Room: opens it to
    /// friends or locks it (not saved: the room lives while occupied).
    pub(super) fn set_wishing_room_open(&mut self, i: usize, at: Instant, open: bool) {
        let Some(d) = self.instances.get(self.ents.zone[i]) else {
            return;
        };
        if d.open == open || !self.take_action(i, at, None) {
            return;
        }
        let zone = self.ents.zone[i];
        let d = self.instances.get_mut(zone).expect("checked above");
        d.open = open;
        let members = d.members.clone();
        let frame: bytes::Bytes =
            protocol::encode_server(&ServerMsg::DormOpenChanged { open }).into();
        for m in members {
            if let Some(j) = self.ents.index_of(m) {
                self.send(j, frame.clone());
            }
        }
    }
}
