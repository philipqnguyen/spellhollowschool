//! The player cap and the login queue (`SHS_PLAYER_CAP`).
//!
//! A player in the world holds a [`Permit`]. When the world is full, newcomers
//! get a [`Ticket`] and wait in first-come order; each time a slot frees up it
//! goes to the head of the queue and everyone behind moves up one place. A
//! ticket's [`QueueState`] is a `watch` value, so the connection task simply
//! awaits changes and forwards `Queued { position }` to its client.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use tokio::sync::watch;

use super::metrics::RealtimeMetrics;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueState {
    /// 1-based place in the queue.
    Waiting(u32),
    /// A slot is reserved for this ticket; claim it with [`Ticket::into_permit`].
    Admitted,
}

#[derive(Debug)]
pub struct Admission {
    inner: Mutex<Inner>,
    metrics: Arc<RealtimeMetrics>,
}

#[derive(Debug)]
struct Inner {
    cap: u32,
    active: u32,
    next_ticket: u64,
    queue: VecDeque<Waiter>,
}

#[derive(Debug)]
struct Waiter {
    ticket: u64,
    tx: watch::Sender<QueueState>,
}

/// One occupied world slot; dropping it frees the slot.
#[derive(Debug)]
pub struct Permit {
    admission: Arc<Admission>,
}

impl Drop for Permit {
    fn drop(&mut self) {
        self.admission.release();
    }
}

/// A place in the login queue. Dropping it leaves the queue (or frees the
/// reserved slot if it was admitted but never claimed).
#[derive(Debug)]
pub struct Ticket {
    admission: Arc<Admission>,
    id: u64,
    pub state: watch::Receiver<QueueState>,
    claimed: bool,
}

pub enum Entry {
    Admitted(Permit),
    Queued(Ticket),
}

impl Admission {
    pub fn new(cap: u32, metrics: Arc<RealtimeMetrics>) -> Arc<Self> {
        Arc::new(Self {
            inner: Mutex::new(Inner {
                cap,
                active: 0,
                next_ticket: 0,
                queue: VecDeque::new(),
            }),
            metrics,
        })
    }

    /// Takes a slot if one is free and nobody is waiting, else joins the queue.
    pub fn enter(self: &Arc<Self>) -> Entry {
        let mut inner = self.lock();
        if inner.active < inner.cap && inner.queue.is_empty() {
            inner.active += 1;
            return Entry::Admitted(Permit {
                admission: self.clone(),
            });
        }
        let id = inner.next_ticket;
        inner.next_ticket += 1;
        let (tx, rx) = watch::channel(QueueState::Waiting(inner.queue.len() as u32 + 1));
        inner.queue.push_back(Waiter { ticket: id, tx });
        self.metrics.queue_length.set(inner.queue.len() as f64);
        Entry::Queued(Ticket {
            admission: self.clone(),
            id,
            state: rx,
            claimed: false,
        })
    }

    /// Players in the world (permits held or reserved).
    pub fn active(&self) -> u32 {
        self.lock().active
    }

    pub fn queue_len(&self) -> usize {
        self.lock().queue.len()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().expect("admission lock poisoned")
    }

    fn release(&self) {
        let mut inner = self.lock();
        inner.active -= 1;
        self.admit_waiting(&mut inner);
    }

    /// Hands free slots to the head of the queue and renumbers the rest.
    fn admit_waiting(&self, inner: &mut Inner) {
        let mut moved = false;
        while inner.active < inner.cap {
            let Some(w) = inner.queue.pop_front() else {
                break;
            };
            inner.active += 1;
            // A waiter whose connection is gone frees its slot on Ticket drop.
            let _ = w.tx.send(QueueState::Admitted);
            moved = true;
        }
        if moved {
            renumber(&inner.queue, 0);
        }
        self.metrics.queue_length.set(inner.queue.len() as f64);
    }

    /// An unclaimed ticket going away: leaves the queue, or, if it was already
    /// admitted, passes its reserved slot on.
    ///
    /// The decision is made under the lock from the queue itself, not from the
    /// ticket's watch value: a slot can be handed to this ticket between the
    /// ticket reading `Waiting` and taking the lock, and deciding from that
    /// stale read would leak the slot for good.
    fn abandon(&self, ticket: u64) {
        let mut inner = self.lock();
        if let Some(at) = inner.queue.iter().position(|w| w.ticket == ticket) {
            inner.queue.remove(at);
            renumber(&inner.queue, at);
            self.metrics.queue_length.set(inner.queue.len() as f64);
        } else {
            // Only admission takes a waiter out of the queue.
            inner.active -= 1;
            self.admit_waiting(&mut inner);
        }
    }
}

fn renumber(queue: &VecDeque<Waiter>, from: usize) {
    for (i, w) in queue.iter().enumerate().skip(from) {
        w.tx.send_if_modified(|s| {
            let next = QueueState::Waiting(i as u32 + 1);
            let changed = *s != next;
            *s = next;
            changed
        });
    }
}

impl Ticket {
    pub fn position(&self) -> Option<u32> {
        match *self.state.borrow() {
            QueueState::Waiting(p) => Some(p),
            QueueState::Admitted => None,
        }
    }

    /// The reserved slot, once admitted.
    pub fn into_permit(mut self) -> Option<Permit> {
        (*self.state.borrow() == QueueState::Admitted).then(|| {
            self.claimed = true;
            Permit {
                admission: self.admission.clone(),
            }
        })
    }
}

impl Drop for Ticket {
    fn drop(&mut self) {
        if !self.claimed {
            self.admission.abandon(self.id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::metrics::Registry;

    fn admission(cap: u32) -> Arc<Admission> {
        Admission::new(
            cap,
            Arc::new(RealtimeMetrics::register(&Registry::default())),
        )
    }

    fn queued(e: Entry) -> Ticket {
        match e {
            Entry::Queued(t) => t,
            Entry::Admitted(_) => panic!("expected to queue"),
        }
    }

    fn admitted(e: Entry) -> Permit {
        match e {
            Entry::Admitted(p) => p,
            Entry::Queued(_) => panic!("expected a slot"),
        }
    }

    #[test]
    fn queues_beyond_the_cap_and_admits_in_order() {
        let a = admission(2);
        let p1 = admitted(a.enter());
        let _p2 = admitted(a.enter());
        let t3 = queued(a.enter());
        let t4 = queued(a.enter());
        let t5 = queued(a.enter());
        assert_eq!(
            (t3.position(), t4.position(), t5.position()),
            (Some(1), Some(2), Some(3))
        );
        assert_eq!(a.queue_len(), 3);

        // Someone in the middle gives up: the one behind moves up.
        drop(t4);
        assert_eq!(t5.position(), Some(2));

        // A slot frees: the head is admitted, the rest move up.
        drop(p1);
        assert_eq!(t3.position(), None);
        assert_eq!(t5.position(), Some(1));
        let p3 = t3.into_permit().expect("admitted");
        assert_eq!(a.active(), 2);

        drop(p3);
        let p5 = t5.into_permit().expect("admitted");
        assert_eq!(a.queue_len(), 0);
        drop(p5);
        assert_eq!(a.active(), 1);
        assert!(matches!(a.enter(), Entry::Admitted(_)));
    }

    #[test]
    fn an_admitted_ticket_that_is_dropped_passes_its_slot_on() {
        let a = admission(1);
        let p = admitted(a.enter());
        let t1 = queued(a.enter());
        let t2 = queued(a.enter());
        drop(p);
        assert_eq!(t1.position(), None);
        drop(t1); // The connection went away before claiming.
        assert_eq!(t2.position(), None);
        assert_eq!(a.active(), 1);
        drop(t2.into_permit());
        assert_eq!(a.active(), 0);
    }

    /// A waiter giving up at the moment a slot frees for it must not leak the
    /// slot, whichever side wins the race.
    #[test]
    fn giving_up_while_being_admitted_never_leaks_a_slot() {
        let a = admission(1);
        for _ in 0..2_000 {
            let p = admitted(a.enter());
            let t = queued(a.enter());
            let barrier = std::sync::Barrier::new(2);
            std::thread::scope(|s| {
                s.spawn(|| {
                    barrier.wait();
                    drop(p);
                });
                s.spawn(|| {
                    barrier.wait();
                    drop(t);
                });
            });
            assert_eq!((a.active(), a.queue_len()), (0, 0));
        }
    }

    #[test]
    fn newcomers_never_jump_the_queue() {
        let a = admission(1);
        let p = admitted(a.enter());
        let t = queued(a.enter());
        drop(p);
        // The slot went to the waiter, so a newcomer queues.
        let late = queued(a.enter());
        assert_eq!(late.position(), Some(1));
        drop(t);
        assert_eq!(late.position(), None);
    }
}
