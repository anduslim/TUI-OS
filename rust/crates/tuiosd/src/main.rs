//! `tuiosd`: the TUI OS server that runs on the Olares One.
//!
//! ```text
//! tuiosd [--config PATH]      run the server
//! tuiosd --print-config       print an example config
//! ```

use std::path::PathBuf;

use anyhow::{Context, Result};
use tuiosd::config::Config;
use tuiosd::{server, session};

const EXAMPLE: &str = r#"# ~/.config/tuiosd/config.toml
# On the Olares One, listen on all interfaces and allow TCP 7420 in
# Olares Settings > VPN (as for the Prometheus proxy on 9090).
listen = "0.0.0.0:7420"
# Shared secret the clients send (phase 2 replaces it with fingerprint keys).
# Generate one with: openssl rand -hex 24
token = "change-me-to-a-long-random-string"
# Program in the session; restarted when it exits.
command = ["tmux", "new", "-A", "-s", "main"]
# Gather output this long before sending a frame (fewer E-ink refreshes).
coalesce_ms = 50
"#;

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let mut args = std::env::args().skip(1);
    let mut path: Option<PathBuf> = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--config" | "-c" => path = Some(args.next().context("--config needs a path")?.into()),
            "--print-config" => {
                print!("{EXAMPLE}");
                return Ok(());
            }
            "--version" | "-V" => {
                println!("tuiosd {}", env!("CARGO_PKG_VERSION"));
                return Ok(());
            }
            "--help" | "-h" => {
                println!("usage: tuiosd [--config PATH] | --print-config | --version");
                return Ok(());
            }
            other => anyhow::bail!("unknown argument {other:?} (try --help)"),
        }
    }
    let path = path.unwrap_or_else(Config::default_path);
    let cfg = Config::load(&path)?;

    let listener = tokio::net::TcpListener::bind(&cfg.listen)
        .await
        .with_context(|| format!("binding {}", cfg.listen))?;
    log::info!(
        "tuiosd {} listening on {}",
        env!("CARGO_PKG_VERSION"),
        cfg.listen
    );
    let token = cfg.token.clone();
    let session = session::Session::start(cfg, 80, 24)?;

    tokio::select! {
        r = server::serve(listener, session, token) => r,
        _ = tokio::signal::ctrl_c() => { log::info!("shutting down"); Ok(()) }
    }
}
