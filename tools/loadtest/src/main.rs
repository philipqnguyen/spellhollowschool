//! Load-test bots: N simulated players that log in, walk the castle and chat.
//!
//! ```text
//! cargo run --release -p loadtest -- --url http://127.0.0.1:8080 --bots 1000 \
//!     --ramp-secs 60 --duration-secs 300 --scenario spread \
//!     --containers shs-loadtest-server-1,shs-loadtest-db-1
//! ```
//!
//! Each bot gets a token from `POST /api/dev/bot-session` (the server must run
//! with `SHS_DEV=1`) with a random body, dresses in a random outfit through
//! the wardrobe API, connects to `/ws` (it applies snapshot deltas and
//! resolves cached appearances as the browser's decoder does), and walks
//! between random destinations of its scenario (`scenarios.json`) using
//! `sim-core`'s own movement over its house's view of the castle, so a bot is
//! never legitimately corrected. With `--effects-pct P`, P percent of the
//! bots get a potion from the dev grant and drink it once in the world, so
//! their looks carry visible effects (`potion.rs`). Standing bots wave,
//! bow and sit now and then. While they run, the monitor scrapes
//! `/metrics` and (with `--containers`) `docker stats`. At the end the tool
//! prints a summary with the milestone budgets and writes it as JSON to
//! `loadtest-results/`. The exit status is 0 only if every budget passed.
//!
//! See tools/loadtest/README.md for the setup that emulates the launch VPS
//! (docker/compose.loadtest.yml) and how to read the results.
//!
//! `--fly-pct P` makes P percent of the walking bots flyers over
//! the whole grounds, and `--scenario pitch` puts 300 flyers over the
//! Flying Pitch and 100 spectators in its stands (`flyer.rs`); with
//! `--class-pct 6 --class-only flying` 60 students attend the Flying class
//! in two sections and fly its ring course (`rings.rs`).
//!
//! Bots walk the castle in the staircase state of the server's
//! clock, ride and re-plan at each switch (`stairs.rs`); `--scenario stairs`
//! sends 300 walkers up and down the Grand Staircase, and
//! `--stairs-period-s 20` matches a server run with `SHS_STAIRS_PERIOD_S=20`.
//!
//! Every bot fetches `GET /api/map` once after its session;
//! `--scenario secrets` (every role at once) adds explorers
//! touring the hidden passages and ritual bots pacing for the Wishing
//! Room (`secrets.rs`) to grounds and stair walkers; with
//! `--class-pct 35 --class-slot 10:30 --duel-pct 10 --fly-pct 75
//! --cup-poll-secs 30 --stairs-period-s 20 --long-window` it is the full
//! acceptance run.
//!
//! Every bot asks `POST /api/dev/bot-session` for a start in
//! the Entrance Hall (`nav::BOT_STARTS`; players start outside on the front
//! steps), and `--players-pct` of the bots search the online players list
//! and open a card at sign-in and every `--players-every-secs`
//! (`players.rs`).

mod bot;
mod cup;
mod desks;
mod dress;
mod exchange;
mod flyer;
mod grounds;
mod http;
mod nav;
mod players;
mod potion;
mod report;
mod rings;
mod school;
mod scrape;
mod secrets;
mod stairs;
mod stats;
mod walker;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::sync::watch;
use tokio::time::Instant;

use crate::bot::stopped;
use crate::http::Target;
use crate::nav::Scenario;
use crate::report::{Collected, DockerSample, RunConfig, Sample, Summary};
use crate::scrape::{HistogramDelta, Metrics};
use crate::stats::Live;

const USAGE: &str = "\
Usage: loadtest [options]

  --url URL              server base URL (default http://127.0.0.1:8080)
  --bots N               number of bots (default 100)
  --ramp-secs S          bots join evenly over this time (default 60)
  --duration-secs S      time at full load after the ramp (default 300)
  --settle-secs S        time after the ramp left out of the measured rates (default 30)
  --measure-secs S       measure S seconds after the ramp and the settle (sets --duration-secs
                         to the settle plus S, whichever order the flags come in)
  --long-window          measure at least 660 s (LONG_WINDOW_SECS), so a class run covers
                         whole sessions: the join, every round and the results burst
  --scenario NAME        spread | packed | exchange | grounds | pitch | stairs | secrets
                         (default spread; see scenarios.json)
  --chat-every-secs S    mean time between a bot's chat lines, 0 = never (default 60)
  --emote-every-secs S   mean time between a standing bot's emotes, 0 = never (default 30)
  --look-every-secs S    mean time between a bot's look-target changes (protocol v10: it
                         looks at someone it sees, or at nobody), 0 = never (default 20)
  --npc-talk-every-secs S
                         mean time between a bot's NpcInteract with a resident NPC in its
                         view (protocol v12; most are out of reach), 0 = never (default 0)
  --no-dress             skip dressing the bots through the wardrobe API
  --effects-pct P        share of bots (0-100) that drink a potion from the dev grant (default 0)
  --class-pct P          share of bots (0-100) that attend class, spread across the classes
                         of one timetable slot (sets the dev clock so the slot's sessions
                         open as the run starts; default 0)
  --class-slot HH:MM     the timetable slot the students attend (default: the first slot
                         with the most classes, 01:00); `night`: the first night slot with
                         the most classes (01:00: Astronomy traces, Herbology and Creature
                         Keeping picks)
  --class-only ID[,ID]   students attend only these classes of the slot (`flying`)
  --duel-pct P           share of bots (0-100) at the Dueling Club: pairs duel, one in five
                         watches (default 0)
  --fly-pct P            share of the walking bots (0-100) that get the Flying skill, mount on
                         the grounds and fly loops over the grounds and the lake at 8-10 m/s,
                         5-40 m up, hovering now and then; 10 % of them land and take off
                         again each minute (default 0)
  --stairs-period-s S    the moving staircases switch every S seconds, as the server does with
                         SHS_STAIRS_PERIOD_S=S (development; default: data/world/stairs.json's)
  --cup-poll-secs S      every bot asks GET /api/house-cup every S seconds, 0 = never (default 0)
  --players-pct P        share of bots (0-100) that search the online players list (a second
                         page when there is one) and open a player card, at sign-in and
                         every --players-every-secs (default 20)
  --players-every-secs S time between a browsing bot's looks, 0 = only at sign-in (default 120)
  --cup-close            one bot closes the running House Cup week (POST
                         /api/dev/house-cup/close) in the middle of the measured window
  --ping-every-secs S    time between a bot's pings (default 5)
  --view-limit N         send SetViewLimit N after joining (default: server default)
  --name-prefix P        bot names are P0000, P0001, ... (default lt + scenario initial)
  --layout PATH          world layout (default data/world/layout.json)
  --containers A,B       docker containers to sample with docker stats
  --sample-secs S        metrics/timeline sample interval (default 5)
  --out-dir DIR          where the JSON summary goes (default loadtest-results)
  --threads N            bot runtime worker threads (default: all cores)
";

/// The least `--long-window` measures (seconds after the ramp and
/// the settle). A class's results come 400-485 s into its session (History
/// last), its session ends at 540 s, and the run starts a second into it, so
/// with the default 60 s ramp and 30 s settle the window (90-750 s) holds
/// the join, every round, the results burst and the end of the session.
pub const LONG_WINDOW_SECS: f64 = 660.0;

struct Args {
    url: String,
    bots: usize,
    ramp: f64,
    duration: f64,
    settle: f64,
    scenario: Scenario,
    chat_every: f64,
    emote_every: f64,
    look_every: f64,
    npc_talk_every: f64,
    dress: bool,
    effects_pct: f64,
    class_pct: f64,
    /// The timetable slot of the classes (`"HH:MM"`, or Phase
    /// `"night"`).
    class_slot: Option<String>,
    /// The classes students attend (all of the slot's when empty).
    class_only: Vec<String>,
    duel_pct: f64,
    /// The share of walking bots that fly over the grounds.
    fly_pct: f64,
    /// The staircases' development period (`SHS_STAIRS_PERIOD_S`).
    stairs_period: Option<u32>,
    /// The House Cup polling interval (0: none) and the dev close.
    cup_poll: f64,
    cup_close: bool,
    /// The share of bots browsing the players list, and how
    /// often (0: only at sign-in).
    players_pct: f64,
    players_every: f64,
    ping_every: f64,
    view_limit: Option<u8>,
    name_prefix: String,
    layout: PathBuf,
    containers: Vec<String>,
    sample: f64,
    out_dir: PathBuf,
    threads: usize,
}

impl Args {
    fn parse(mut it: impl Iterator<Item = String>) -> Result<Args, String> {
        let mut a = Args {
            url: "http://127.0.0.1:8080".into(),
            bots: 100,
            ramp: 60.0,
            duration: 300.0,
            settle: 30.0,
            scenario: Scenario::named("spread")?,
            chat_every: 60.0,
            emote_every: 30.0,
            look_every: 20.0,
            npc_talk_every: 0.0,
            dress: true,
            effects_pct: 0.0,
            class_pct: 0.0,
            class_slot: None,
            class_only: Vec::new(),
            duel_pct: 0.0,
            fly_pct: 0.0,
            stairs_period: None,
            cup_poll: 0.0,
            cup_close: false,
            players_pct: players::DEFAULT_PCT,
            players_every: players::DEFAULT_EVERY_SECS,
            ping_every: 5.0,
            view_limit: None,
            name_prefix: String::new(),
            layout: "data/world/layout.json".into(),
            containers: Vec::new(),
            sample: 5.0,
            out_dir: "loadtest-results".into(),
            threads: std::thread::available_parallelism().map_or(4, |n| n.get()),
        };
        let mut measure: Option<f64> = None;
        let mut long_window = false;
        fn num<T: std::str::FromStr>(flag: &str, v: Option<String>) -> Result<T, String> {
            let v = v.ok_or_else(|| format!("{flag} needs a value"))?;
            v.parse()
                .map_err(|_| format!("{flag}: {v:?} is not a valid number"))
        }
        while let Some(flag) = it.next() {
            match flag.as_str() {
                "--url" => a.url = it.next().ok_or("--url needs a value")?,
                "--bots" => a.bots = num(&flag, it.next())?,
                "--ramp-secs" => a.ramp = num(&flag, it.next())?,
                "--duration-secs" => a.duration = num(&flag, it.next())?,
                "--settle-secs" => a.settle = num(&flag, it.next())?,
                "--measure-secs" => measure = Some(num(&flag, it.next())?),
                "--long-window" => long_window = true,
                "--scenario" => {
                    a.scenario = Scenario::named(&it.next().ok_or("--scenario needs a value")?)?
                }
                "--chat-every-secs" => a.chat_every = num(&flag, it.next())?,
                "--emote-every-secs" => a.emote_every = num(&flag, it.next())?,
                "--look-every-secs" => a.look_every = num(&flag, it.next())?,
                "--npc-talk-every-secs" => a.npc_talk_every = num(&flag, it.next())?,
                "--no-dress" => a.dress = false,
                "--effects-pct" => a.effects_pct = num(&flag, it.next())?,
                "--class-pct" => a.class_pct = num(&flag, it.next())?,
                "--class-slot" => {
                    a.class_slot = Some(it.next().ok_or("--class-slot needs a value")?)
                }
                "--class-only" => {
                    a.class_only = it
                        .next()
                        .ok_or("--class-only needs a value")?
                        .split(',')
                        .filter(|s| !s.is_empty())
                        .map(str::to_owned)
                        .collect()
                }
                "--duel-pct" => a.duel_pct = num(&flag, it.next())?,
                "--fly-pct" => a.fly_pct = num(&flag, it.next())?,
                "--stairs-period-s" => a.stairs_period = Some(num(&flag, it.next())?),
                "--cup-poll-secs" => a.cup_poll = num(&flag, it.next())?,
                "--cup-close" => a.cup_close = true,
                "--players-pct" => a.players_pct = num(&flag, it.next())?,
                "--players-every-secs" => a.players_every = num(&flag, it.next())?,
                "--ping-every-secs" => a.ping_every = num(&flag, it.next())?,
                "--view-limit" => a.view_limit = Some(num(&flag, it.next())?),
                "--name-prefix" => {
                    a.name_prefix = it.next().ok_or("--name-prefix needs a value")?
                }
                "--layout" => a.layout = it.next().ok_or("--layout needs a value")?.into(),
                "--containers" => {
                    a.containers = it
                        .next()
                        .ok_or("--containers needs a value")?
                        .split(',')
                        .filter(|s| !s.is_empty())
                        .map(str::to_owned)
                        .collect()
                }
                "--sample-secs" => a.sample = num(&flag, it.next())?,
                "--out-dir" => a.out_dir = it.next().ok_or("--out-dir needs a value")?.into(),
                "--threads" => a.threads = num(&flag, it.next())?,
                "-h" | "--help" => return Err(String::new()),
                other => return Err(format!("unknown option {other:?}")),
            }
        }
        if let Some(m) = measure {
            if !(m.is_finite() && m >= 0.0) {
                return Err("--measure-secs must be zero or more".into());
            }
            a.duration = a.settle + m;
        }
        if long_window {
            a.duration = a.duration.max(a.settle + LONG_WINDOW_SECS);
        }
        for (flag, v) in [
            ("--ramp-secs", a.ramp),
            ("--duration-secs", a.duration),
            ("--settle-secs", a.settle),
            ("--chat-every-secs", a.chat_every),
            ("--emote-every-secs", a.emote_every),
            ("--look-every-secs", a.look_every),
            ("--npc-talk-every-secs", a.npc_talk_every),
            ("--cup-poll-secs", a.cup_poll),
            ("--players-every-secs", a.players_every),
        ] {
            if !(v.is_finite() && v >= 0.0) {
                return Err(format!("{flag} must be zero or more"));
            }
        }
        if !(a.ping_every.is_finite() && a.ping_every >= 0.1) || a.sample < 0.5 {
            return Err("--ping-every-secs must be >= 0.1 and --sample-secs >= 0.5".into());
        }
        if !(0.0..=100.0).contains(&a.effects_pct) {
            return Err("--effects-pct must be 0-100".into());
        }
        if !(0.0..=100.0).contains(&a.players_pct) {
            return Err("--players-pct must be 0-100".into());
        }
        if !(0.0..=100.0).contains(&a.fly_pct) {
            return Err("--fly-pct must be 0-100".into());
        }
        if !(a.class_pct >= 0.0 && a.duel_pct >= 0.0 && a.class_pct + a.duel_pct <= 100.0) {
            return Err("--class-pct and --duel-pct must be 0-100 together".into());
        }
        if a.bots == 0 || a.bots > 99_999 || a.threads == 0 {
            return Err("--bots must be 1-99999 and --threads at least 1".into());
        }
        if a.name_prefix.is_empty() {
            a.name_prefix = format!("lt{}", &a.scenario.name[..1]);
        }
        if !a.name_prefix.starts_with(|c: char| c.is_ascii_alphabetic()) {
            return Err("--name-prefix must start with a letter".into());
        }
        Ok(a)
    }
}

fn main() -> ExitCode {
    let args = match Args::parse(std::env::args().skip(1)) {
        Ok(a) => a,
        Err(e) if e.is_empty() => {
            print!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        Err(e) => {
            eprintln!("loadtest: {e}\n\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    raise_open_file_limit(args.bots as u64 * 2 + 256);
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(args.threads)
        .enable_all()
        .build()
        .expect("tokio runtime starts");
    match runtime.block_on(run(args)) {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::FAILURE,
        Err(e) => {
            eprintln!("loadtest: {e}");
            ExitCode::from(2)
        }
    }
}

/// Each bot holds a socket, and macOS starts shells at 256 open files.
fn raise_open_file_limit(want: u64) {
    // SAFETY: getrlimit/setrlimit only read and write the struct passed in.
    unsafe {
        let mut lim = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        if libc::getrlimit(libc::RLIMIT_NOFILE, &mut lim) != 0 || lim.rlim_cur >= want as _ {
            return;
        }
        // macOS refuses values above OPEN_MAX even when the hard limit is "unlimited".
        for cap in [want as libc::rlim_t, 10_240] {
            let new = libc::rlimit {
                rlim_cur: cap.min(lim.rlim_max),
                rlim_max: lim.rlim_max,
            };
            if libc::setrlimit(libc::RLIMIT_NOFILE, &new) == 0 {
                return;
            }
        }
        eprintln!(
            "loadtest: could not raise the open-file limit above {}; run `ulimit -n {want}` first",
            lim.rlim_cur
        );
    }
}

async fn run(args: Args) -> Result<bool, String> {
    let layout = std::fs::read_to_string(&args.layout)
        .map_err(|e| format!("reading {}: {e}", args.layout.display()))?;
    // The castle with both positions of every moving staircase
    // flight; `world` has every flight at rest in position A (the class
    // doors, the exchange court, the grounds and the pitch are the same in
    // every staircase state).
    let all = sim_core::Layout::from_json(&layout).map_err(|e| e.to_string())?;
    let world = Arc::new(sim_core::World::new(all.for_stairs(0)).map_err(|e| e.to_string())?);
    let staircase = stairs::Staircase::new(&all, args.stairs_period)?.map(Arc::new);
    // Each bot walks its own house's view of the castle, as the server
    // checks, in the staircase state of its clock.
    let navs = stairs::CastleNavs::new(&all, &args.scenario)?;
    let nav = navs.nav(protocol::House::ALL[0], 0).clone();
    let surfaces = world.surfaces().len();
    let reachable = (0..surfaces as u32)
        .filter(|&s| nav.is_reachable(s))
        .count();
    println!(
        "loadtest: layout {}: {surfaces} surfaces ({reachable} reachable from the spawns), {} portals, {} destination surfaces, {} keep-outs",
        args.layout.display(),
        nav.portals().len(),
        nav.target_surfaces().len(),
        nav.keep_out().len()
    );
    let target = Target::parse(&args.url)?;

    // The server must be up (and in dev mode, which the first session checks).
    match target.get("/api/health").await {
        Ok(r) if r.is_success() => {}
        Ok(r) => {
            return Err(format!(
                "{}/api/health: {} {}",
                args.url,
                r.status,
                String::from_utf8_lossy(&r.body)
            ));
        }
        Err(e) => return Err(format!("{}/api/health: {e}", args.url)),
    }

    let config = RunConfig {
        url: args.url.clone(),
        bots: args.bots,
        ramp_secs: args.ramp,
        duration_secs: args.duration,
        settle_secs: args.settle,
        chat_every_secs: args.chat_every,
        emote_every_secs: args.emote_every,
        look_every_secs: args.look_every,
        npc_talk_every_secs: args.npc_talk_every,
        dress: args.dress,
        effects_pct: args.effects_pct,
        class_pct: args.class_pct,
        duel_pct: args.duel_pct,
        ping_every_secs: args.ping_every,
        view_limit: args.view_limit,
        name_prefix: args.name_prefix.clone(),
        containers: args.containers.clone(),
        bot_threads: args.threads,
        cup_poll_secs: args.cup_poll,
        cup_close: args.cup_close,
        players_pct: args.players_pct,
        players_every_secs: args.players_every,
        fly_pct: args.fly_pct,
        class_only: args.class_only.clone(),
        stairs_period_secs: staircase
            .as_ref()
            .map(|s| (s.stairs.period_ms() / 1000) as u32),
    };
    println!(
        "loadtest: {} bots, scenario {} ({}), ramp {:.0} s, then {:.0} s at full load against {}",
        args.bots,
        args.scenario.name,
        args.scenario.description,
        args.ramp,
        args.duration,
        args.url
    );

    // Roles: students need the class door open, so the dev
    // clock jumps to the start of the next session (just before the first
    // bot arrives); everyone else is unaffected by the game time.
    let school = if args.class_pct > 0.0 || args.duel_pct > 0.0 {
        let s = school::School::shipped_only(&world, args.class_slot.as_deref(), &args.class_only)?;
        if args.class_pct > 0.0 {
            set_class_clock(&target, &args.name_prefix, &s).await?;
        }
        Some(Arc::new(s))
    } else {
        None
    };
    let roles = school::roles(args.bots, &args.name_prefix, args.class_pct, args.duel_pct);
    // How the students split across the slot's classes.
    if let Some(s) = school.as_deref().filter(|_| args.class_pct > 0.0) {
        let mut split = vec![0usize; s.classes.len()];
        for (i, r) in roles.iter().enumerate() {
            if *r == school::Role::Student {
                split[s.class_of(i)] += 1;
            }
        }
        let names: Vec<String> = s
            .classes
            .iter()
            .zip(&split)
            .map(|(c, n)| {
                format!(
                    "{} {n} ({} {} in {})",
                    c.id,
                    c.room.desks.len(),
                    if c.room.seated {
                        "desk seats"
                    } else {
                        "standing spots"
                    },
                    c.room.layout
                )
            })
            .collect();
        println!(
            "loadtest: students at the {} slot: {}",
            s.slot_name(),
            names.join(", ")
        );
    }
    // Castle kit M2: the Owl Exchange court crowd.
    let exchange = match &args.scenario.exchange {
        Some(spec) => {
            if school.is_some() {
                return Err(
                    "the exchange scenario has its own roles: drop --class-pct and --duel-pct"
                        .into(),
                );
            }
            let ex = exchange::Exchange::new(&world, spec)?;
            println!(
                "loadtest: Owl Exchange court: {} standers outside the {:.1} m reach ring round {:?}, the other {} bots walk to the ring and back",
                ex.spots.len().min(args.bots),
                ex.radius,
                ex.centre,
                args.bots.saturating_sub(ex.spots.len())
            );
            Some(Arc::new(ex))
        }
        None => None,
    };

    // The grounds walkers, chosen among the bots without a school
    // role (the stair walkers; the passage explorers;
    // each tour among the bots no earlier one took).
    let mut free: Vec<bool> = roles.iter().map(|r| *r == school::Role::Walker).collect();
    let mut specs: Vec<(grounds::GroundsSpec, &'static str)> = Vec::new();
    if let Some(g) = &args.scenario.grounds {
        specs.push((g.clone(), grounds::ROLE));
    }
    if let Some(s) = &args.scenario.stairs {
        specs.push((s.clone(), stairs::ROLE));
    }
    if let Some(e) = &args.scenario.explore {
        specs.push((e.tour(&world, &all)?, secrets::EXPLORE_ROLE));
    }
    let mut tours = Vec::new();
    for (spec, role) in specs {
        let g =
            grounds::Grounds::with_spec(&world, &all, &args.scenario, spec.clone(), role, &free)?;
        for (i, f) in free.iter_mut().enumerate() {
            if g.is_member(i) {
                *f = false;
            }
        }
        println!(
            "loadtest: {}s: {} walkers tour {} stops ({} cells, stops within {:.1} m, {:.0} % of trips astray, {} portals); the other {} bots keep to {:?}",
            g.role,
            g.walkers(),
            g.stops.len(),
            spec.cells.join(", "),
            spec.jitter,
            spec.stray * 100.0,
            g.nav(protocol::House::Roarwing).portals().len(),
            args.bots - g.walkers(),
            args.scenario.cells.as_deref().unwrap_or_default()
        );
        tours.push(Arc::new(g));
    }
    // The ritual bots pace for the Wishing Room.
    let ritual = args.scenario.ritual.as_ref().map(|spec| {
        let r = secrets::Ritual::new(spec.clone(), &free);
        for (i, f) in free.iter_mut().enumerate() {
            if r.is_member(i) {
                *f = false;
            }
        }
        println!(
            "loadtest: ritual: {} bots pace the seventh-floor east corridor, enter the Wishing Room (rooms alternate), stay {:?} s and rest {:?} s",
            r.bots(),
            spec.stay_secs,
            spec.rest_secs
        );
        Arc::new(r)
    });

    // Flyers and pitch spectators, chosen among the bots that only
    // walk (not touring, not pacing).
    let flight = if args.scenario.pitch.is_some() || args.fly_pct > 0.0 {
        let walking = free.clone();
        let f = flyer::Flight::new(
            world.clone(),
            args.scenario.pitch.as_ref(),
            args.fly_pct / 100.0,
            &walking,
        )?;
        let (flyers, spectators) = f.counts();
        let (launch, spots) = f.spot_counts();
        println!(
            "loadtest: flying: {flyers} flyers ({launch} launch spots), {spectators} pitch spectators ({spots} spots on the stands and the field's ends)"
        );
        Some(Arc::new(f))
    } else {
        None
    };

    let epoch = Instant::now();
    let started_unix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    let live = Arc::new(Live::default());
    let navs_per_state = navs.per_state() || tours.iter().any(|g| g.role != grounds::ROLE);
    let shared = Arc::new(bot::Shared {
        target: target.clone(),
        navs,
        live: live.clone(),
        epoch,
        chat_every: (args.chat_every > 0.0).then(|| Duration::from_secs_f64(args.chat_every)),
        emote_every: (args.emote_every > 0.0).then(|| Duration::from_secs_f64(args.emote_every)),
        look_every: (args.look_every > 0.0).then(|| Duration::from_secs_f64(args.look_every)),
        npc_talk_every: (args.npc_talk_every > 0.0)
            .then(|| Duration::from_secs_f64(args.npc_talk_every)),
        wardrobe: args.dress.then(|| Arc::new(dress::Wardrobe::shipped())),
        potions: (args.effects_pct > 0.0).then(|| {
            (
                Arc::new(potion::Potions::shipped()),
                args.effects_pct / 100.0,
            )
        }),
        ping_every: Duration::from_secs_f64(args.ping_every),
        view_limit: args.view_limit,
        school,
        exchange,
        cup_every: (args.cup_poll > 0.0).then(|| Duration::from_secs_f64(args.cup_poll)),
        players: (args.players_pct > 0.0).then(|| {
            (
                args.players_pct,
                (args.players_every > 0.0).then(|| Duration::from_secs_f64(args.players_every)),
            )
        }),
        tours,
        ritual,
        flight,
        stairs: staircase.clone(),
    });
    if let Some(st) = &staircase {
        println!(
            "loadtest: moving staircases: {} flights switching every {} s (staggered; {}), navigation per staircase state: {}",
            st.stairs.flights().len(),
            st.stairs.period_ms() / 1000,
            if args.stairs_period.is_some() {
                "the development period: the server must run with the same SHS_STAIRS_PERIOD_S"
            } else {
                "the shipped period"
            },
            navs_per_state
        );
    }
    let (stop_tx, stop_rx) = watch::channel(false);

    let monitor = Monitor::new(target.clone(), live.clone(), epoch, args.bots);
    let monitor_task = tokio::spawn(
        monitor
            .clone()
            .run(Duration::from_secs_f64(args.sample), stop_rx.clone()),
    );
    let docker_task = (!args.containers.is_empty()).then(|| {
        tokio::spawn(monitor.clone().run_docker(
            args.containers.clone(),
            Duration::from_secs_f64(args.sample),
            stop_rx.clone(),
        ))
    });

    let mut bots = Vec::with_capacity(args.bots);
    for (i, role) in roles.iter().enumerate() {
        let offset = if args.bots > 1 {
            args.ramp * i as f64 / (args.bots - 1) as f64
        } else {
            0.0
        };
        let start = epoch + Duration::from_secs_f64(offset);
        let name = format!("{}{i:04}", args.name_prefix);
        bots.push(tokio::spawn(bot::run(
            shared.clone(),
            i,
            name,
            role.clone(),
            start,
            stop_rx.clone(),
        )));
    }

    // One dev close of the running House Cup week, mid-window.
    let cup_close = args.cup_close.then(|| {
        let measured = (args.duration - args.settle).max(0.0);
        let at = epoch + Duration::from_secs_f64(args.ramp + args.settle + measured / 2.0);
        tokio::spawn(cup::close_at(
            target.clone(),
            args.name_prefix.clone(),
            at,
            epoch,
            stop_rx.clone(),
        ))
    });

    let end = epoch + Duration::from_secs_f64(args.ramp + args.duration);
    let interrupted = tokio::select! {
        _ = tokio::time::sleep_until(end) => false,
        _ = tokio::signal::ctrl_c() => {
            eprintln!("loadtest: interrupted; summarising what ran so far");
            true
        }
    };
    // Final reading before the bots leave, so the window ends at full load.
    monitor.sample().await;
    let window_end = epoch.elapsed().as_secs_f64();
    stop_tx.send_replace(true);

    let mut reports = Vec::with_capacity(bots.len());
    for b in bots {
        match tokio::time::timeout(Duration::from_secs(30), b).await {
            Ok(Ok(r)) => reports.push(r),
            Ok(Err(e)) => eprintln!("loadtest: a bot panicked: {e}"),
            Err(_) => eprintln!("loadtest: a bot did not stop within 30 s"),
        }
    }
    let cup_close = match cup_close {
        Some(task) => task.await.ok().flatten(),
        None => None,
    };
    let _ = monitor_task.await;
    if let Some(t) = docker_task {
        let _ = t.await;
    }

    let window_start = (args.ramp + args.settle).min(window_end);
    let state = monitor.take();
    let summary = Summary::build(Collected {
        scenario: args.scenario.clone(),
        config,
        started_unix,
        window: (window_start, window_end),
        timeline: state.timeline,
        scrapes: state.scrapes,
        docker: state.docker,
        docker_errors: state.docker_errors,
        reports,
        cup_close,
    });
    print!("{}", summary.render());

    std::fs::create_dir_all(&args.out_dir)
        .map_err(|e| format!("creating {}: {e}", args.out_dir.display()))?;
    let file = args.out_dir.join(format!(
        "{}-{}bots-{started_unix}.json",
        args.scenario.name, args.bots
    ));
    let json = serde_json::to_string_pretty(&summary).expect("summary serializes");
    std::fs::write(&file, json).map_err(|e| format!("writing {}: {e}", file.display()))?;
    println!("wrote {}", file.display());
    Ok(summary.passed() && !interrupted)
}

/// Moves the server's dev clock (`POST /api/dev/clock`, through a session of
/// its own bot `<prefix>clock`) to a second after the next class session
/// starts, so its door admits students for the whole ramp.
async fn set_class_clock(
    target: &Target,
    prefix: &str,
    school: &school::School,
) -> Result<(), String> {
    let res = target
        .post_json(
            "/api/dev/bot-session",
            &serde_json::json!({ "name": format!("{prefix}clock") }),
        )
        .await?;
    let token = serde_json::from_slice::<serde_json::Value>(&res.body)
        .ok()
        .and_then(|v| v["token"].as_str().map(str::to_owned))
        .ok_or_else(|| format!("clock bot session: {}", res.status))?;
    // The server's clock now, as an earlier run may have left it: the next
    // session after it is always a fresh one (a session key the class
    // handler already ran does not run its lesson again).
    let now = target.get_as(&token, "/api/classes/schedule").await?;
    let now: serde_json::Value =
        serde_json::from_slice(&now.body).map_err(|e| format!("schedule: {e}"))?;
    let unix = now["now_unix_ms"]
        .as_i64()
        .ok_or("schedule: no now_unix_ms")?;
    let at = school.session_start(unix) + 1_000;
    let res = target
        .post_json_as(
            &token,
            "/api/dev/clock",
            &serde_json::json!({ "at_unix_ms": at }),
        )
        .await?;
    if !res.is_success() {
        return Err(format!(
            "dev clock {}: {}",
            res.status,
            String::from_utf8_lossy(&res.body)
        ));
    }
    println!(
        "loadtest: dev clock moved {} s ahead, to 1 s into the {} slot's sessions starting {}",
        (at - unix) / 1000,
        school.slot_name(),
        at - 1_000
    );
    Ok(())
}

/// What the monitor has collected so far.
#[derive(Default)]
struct MonitorState {
    timeline: Vec<Sample>,
    scrapes: Vec<(f64, Metrics)>,
    docker: Vec<DockerSample>,
    docker_errors: Vec<String>,
    scrape_errors: usize,
}

/// Samples the live counters and the server's `/metrics` on an interval and
/// prints a progress line every few samples.
#[derive(Clone)]
struct Monitor {
    target: Target,
    live: Arc<Live>,
    epoch: Instant,
    bots: usize,
    state: Arc<Mutex<MonitorState>>,
}

impl Monitor {
    fn new(target: Target, live: Arc<Live>, epoch: Instant, bots: usize) -> Monitor {
        Monitor {
            target,
            live,
            epoch,
            bots,
            state: Arc::default(),
        }
    }

    fn take(&self) -> MonitorState {
        std::mem::take(&mut *self.state.lock().expect("monitor lock"))
    }

    async fn run(self, every: Duration, mut stop: watch::Receiver<bool>) {
        let mut ticker = tokio::time::interval(every);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let print_every = (10.0 / every.as_secs_f64()).round().max(1.0) as usize;
        let mut n = 0usize;
        loop {
            tokio::select! {
                biased;
                _ = stopped(&mut stop) => return,
                _ = ticker.tick() => {}
            }
            self.sample().await;
            // The bots may have been told to leave while this sample ran.
            if n.is_multiple_of(print_every) && !*stop.borrow() {
                println!("{}", self.progress_line());
            }
            n += 1;
        }
    }

    /// One reading of the live counters and `/metrics`.
    async fn sample(&self) {
        let scraped = match self.target.get("/metrics").await {
            Ok(r) if r.is_success() => Some(Metrics::parse(&String::from_utf8_lossy(&r.body))),
            _ => None,
        };
        let t = self.epoch.elapsed().as_secs_f64();
        let live = &*self.live;
        let mut st = self.state.lock().expect("monitor lock");
        let interval_tick = scraped.as_ref().and_then(|m| {
            let (_, prev) = st.scrapes.last()?;
            let d = HistogramDelta::between(prev, m, "shs_world_tick_seconds");
            (d.count > 0.0).then(|| (d.quantile(0.5) * 1e3, d.quantile(0.99) * 1e3))
        });
        st.timeline.push(Sample {
            t,
            bots_in_world: live.in_world.load(std::sync::atomic::Ordering::Relaxed),
            bot_rx_bytes: Live::get(&live.rx_bytes),
            bot_rx_wire_bytes: Live::get(&live.rx_wire_bytes),
            bot_rx_messages: Live::get(&live.rx_messages),
            bot_tx_bytes: Live::get(&live.tx_bytes),
            bot_corrections: Live::get(&live.corrections),
            bot_disconnects: Live::get(&live.disconnects),
            players: scraped.as_ref().map(|m| m.get("shs_world_players")),
            tick_p50_ms: interval_tick.map(|t| t.0),
            tick_p99_ms: interval_tick.map(|t| t.1),
            server_sent_bytes: scraped.as_ref().map(|m| m.get("shs_ws_sent_bytes_total")),
        });
        match scraped {
            Some(m) => st.scrapes.push((t, m)),
            None => st.scrape_errors += 1,
        }
    }

    fn progress_line(&self) -> String {
        let st = self.state.lock().expect("monitor lock");
        let Some(last) = st.timeline.last() else {
            return String::new();
        };
        // Receive rate over the last ~10 s.
        let back = st
            .timeline
            .iter()
            .rev()
            .find(|s| last.t - s.t >= 9.5)
            .unwrap_or(&st.timeline[0]);
        let secs = (last.t - back.t).max(1e-9);
        let bots = last.bots_in_world.max(1) as f64;
        let rx = (last.bot_rx_wire_bytes - back.bot_rx_wire_bytes) as f64 / secs / bots;
        let mut line = format!(
            "[{:>5.0} s] bots {:>4}/{} in world | rx {:>5.2} KB/s/bot | corrections {} | disconnects {}",
            last.t,
            last.bots_in_world,
            self.bots,
            rx / 1024.0,
            last.bot_corrections,
            last.bot_disconnects
        );
        if let (Some(p), Some(p50), Some(p99)) = (last.players, last.tick_p50_ms, last.tick_p99_ms)
        {
            line += &format!(" | players {p:.0} | tick p50 {p50:.2} ms p99 {p99:.2} ms");
        }
        if st.scrape_errors > 0 {
            line += &format!(" | {} failed scrapes", st.scrape_errors);
        }
        if let Some(d) = st.docker.last() {
            for c in &d.containers {
                line += &format!(" | {} {:.0}% {:.0} MiB", c.name, c.cpu_pct, c.mem_mib);
            }
        }
        line
    }

    async fn run_docker(
        self,
        containers: Vec<String>,
        every: Duration,
        mut stop: watch::Receiver<bool>,
    ) {
        let mut ticker = tokio::time::interval(every);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                _ = ticker.tick() => {}
                _ = stopped(&mut stop) => return,
            }
            let read = scrape::docker_stats(&containers).await;
            let t = self.epoch.elapsed().as_secs_f64();
            let mut st = self.state.lock().expect("monitor lock");
            match read {
                Ok(containers) => st.docker.push(DockerSample { t, containers }),
                Err(e) => st.docker_errors.push(e),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(s: &str) -> Result<Args, String> {
        Args::parse(s.split_whitespace().map(str::to_owned))
    }

    #[test]
    fn parses_flags() {
        let a = parse(
            "--url http://h:1 --bots 1000 --ramp-secs 60 --duration-secs 300 --scenario packed \
             --containers a,b --view-limit 20",
        )
        .unwrap();
        assert_eq!((a.url.as_str(), a.bots), ("http://h:1", 1000));
        assert_eq!((a.ramp, a.duration), (60.0, 300.0));
        assert_eq!(a.scenario.name, "packed");
        assert_eq!(a.name_prefix, "ltp");
        assert_eq!(a.containers, vec!["a", "b"]);
        assert_eq!(a.view_limit, Some(20));
        assert_eq!(parse("").unwrap().scenario.name, "spread");
        // The grounds walkers.
        let g = parse("--scenario grounds").unwrap();
        assert_eq!(
            (g.scenario.name.as_str(), g.name_prefix.as_str()),
            ("grounds", "ltg")
        );
        assert_eq!(g.scenario.grounds.map(|s| s.walkers), Some(300));
        // The pitch, flyers and the Flying class only.
        let p = parse("--scenario pitch --fly-pct 10 --class-pct 6 --class-only flying").unwrap();
        assert_eq!(p.scenario.name, "pitch");
        assert_eq!((p.fly_pct, p.class_pct), (10.0, 6.0));
        assert_eq!(p.class_only, vec!["flying"]);
        assert!(parse("--fly-pct 101").is_err());
        // The stair walkers on the development period.
        let s = parse("--scenario stairs --stairs-period-s 20").unwrap();
        assert_eq!(s.scenario.name, "stairs");
        assert_eq!(s.stairs_period, Some(20));
        assert_eq!(s.scenario.stairs.map(|t| t.walkers), Some(300));
        assert_eq!(parse("").unwrap().stairs_period, None);
        assert!(parse("--stairs-period-s soon").is_err());
    }

    #[test]
    fn rejects_bad_flags() {
        assert!(parse("--bots").is_err());
        assert!(parse("--bots many").is_err());
        assert!(parse("--bots 0").is_err());
        assert!(parse("--scenario moon").is_err());
        assert!(parse("--ramp-secs -1").is_err());
        assert!(parse("--name-prefix 9x").is_err());
        assert!(parse("--frobnicate").is_err());
        assert!(parse("--measure-secs -5").is_err());
        assert_eq!(parse("--help").err().as_deref(), Some(""));
    }

    #[test]
    fn the_long_window_measures_whole_class_sessions() {
        // --measure-secs sets the duration after the settle, in any order.
        let a = parse("--measure-secs 600 --settle-secs 20").unwrap();
        assert_eq!((a.settle, a.duration), (20.0, 620.0));
        // --long-window: at least LONG_WINDOW_SECS measured...
        let a = parse("--long-window").unwrap();
        assert_eq!(a.duration - a.settle, LONG_WINDOW_SECS);
        const { assert!(LONG_WINDOW_SECS >= 600.0) };
        // ...covering the results 405 s into a session that starts as the
        // run does (the dev clock is set to 1 s into it).
        assert!(a.ramp + a.duration > 540.0 + 60.0);
        assert!(a.ramp + a.settle < 405.0);
        // ...and never shortening a longer run.
        let a = parse("--long-window --duration-secs 2000").unwrap();
        assert_eq!(a.duration, 2000.0);
        let a = parse("--measure-secs 900 --long-window").unwrap();
        assert_eq!(a.duration - a.settle, 900.0);
    }
}
