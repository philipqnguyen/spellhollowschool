//! Argon2id password hashing (the crate's defaults: m = 19 MiB, t = 2, p = 1,
//! the OWASP baseline). Hashing is CPU-bound, so the async wrappers run it on
//! Tokio's blocking pool rather than stalling the runtime (and the world tick).

use std::sync::{Arc, LazyLock};

use argon2::Argon2;
use argon2::password_hash::rand_core::OsRng;
use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use tokio::sync::Semaphore;

/// Stored instead of a hash for accounts that can never log in with a password
/// (dev bot accounts). It is not a valid PHC string, so verification always fails.
pub const NO_PASSWORD: &str = "!";

pub fn hash_blocking(password: &str) -> String {
    let salt = SaltString::generate(&mut OsRng);
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .expect("argon2 hashing with default params cannot fail")
        .to_string()
}

pub fn verify_blocking(password: &str, phc: &str) -> bool {
    match PasswordHash::new(phc) {
        Ok(parsed) => Argon2::default()
            .verify_password(password.as_bytes(), &parsed)
            .is_ok(),
        Err(_) => false,
    }
}

/// A real hash of an unguessable password, verified against when the email is
/// unknown so that response time does not reveal which emails have accounts.
static DUMMY_HASH: LazyLock<String> = LazyLock::new(|| {
    let (random, _) = super::token::generate();
    hash_blocking(&random)
});

/// Password hashes computed at once. Each Argon2 run holds 19 MiB and a core
/// for tens of milliseconds; without a limit, a burst of logins from many
/// addresses (each within its rate limit) would fill Tokio's blocking pool
/// (512 threads, so ~10 GB) and take every core from the world tick. Extra
/// requests wait their turn instead.
static HASHING: LazyLock<Arc<Semaphore>> =
    LazyLock::new(|| Arc::new(Semaphore::new(hashing_concurrency())));

/// Half the cores (at least one): the other half stays free for the world.
fn hashing_concurrency() -> usize {
    std::thread::available_parallelism().map_or(1, |n| (n.get() / 2).max(1))
}

/// Runs a password job on the blocking pool, at most [`hashing_concurrency`]
/// at a time.
async fn run_limited<T: Send + 'static>(
    limit: &Arc<Semaphore>,
    job: impl FnOnce() -> T + Send + 'static,
) -> Result<T, tokio::task::JoinError> {
    // The semaphore is never closed, so acquiring cannot fail. The permit moves
    // into the job: a request cancelled mid-hash still holds it until the hash ends.
    let permit = limit
        .clone()
        .acquire_owned()
        .await
        .expect("hashing semaphore is open");
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        job()
    })
    .await
}

pub async fn hash(password: String) -> Result<String, tokio::task::JoinError> {
    run_limited(&HASHING, move || hash_blocking(&password)).await
}

/// Verifies `password` against `phc`, or against a dummy hash when `phc` is `None`
/// (which always fails, taking the same time as a real check).
pub async fn verify(password: String, phc: Option<String>) -> Result<bool, tokio::task::JoinError> {
    run_limited(&HASHING, move || match phc {
        Some(phc) => verify_blocking(&password, &phc),
        None => {
            verify_blocking(&password, &DUMMY_HASH);
            false
        }
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hashes_with_argon2id_and_verifies() {
        let phc = hash_blocking("correct horse battery");
        assert!(phc.starts_with("$argon2id$"));
        assert!(verify_blocking("correct horse battery", &phc));
        assert!(!verify_blocking("wrong horse battery", &phc));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn hashing_runs_a_limited_number_at_once() {
        use std::sync::atomic::{AtomicUsize, Ordering};

        let limit = Arc::new(Semaphore::new(2));
        let (running, most) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let jobs: Vec<_> = (0..16)
            .map(|_| {
                let (limit, running, most) = (limit.clone(), running.clone(), most.clone());
                tokio::spawn(async move {
                    run_limited(&limit, move || {
                        let now = running.fetch_add(1, Ordering::SeqCst) + 1;
                        most.fetch_max(now, Ordering::SeqCst);
                        std::thread::sleep(std::time::Duration::from_millis(5));
                        running.fetch_sub(1, Ordering::SeqCst);
                    })
                    .await
                })
            })
            .collect();
        for j in jobs {
            j.await.unwrap().unwrap();
        }
        assert_eq!(most.load(Ordering::SeqCst), 2);
        assert!(hashing_concurrency() >= 1);
    }

    #[test]
    fn no_password_marker_never_verifies() {
        assert!(!verify_blocking("", NO_PASSWORD));
        assert!(!verify_blocking("!", NO_PASSWORD));
    }
}
