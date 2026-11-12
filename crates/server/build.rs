// Migrations are embedded with `sqlx::migrate!`; rebuild when any of them changes.
fn main() {
    println!("cargo:rerun-if-changed=../../db/migrations");
}
