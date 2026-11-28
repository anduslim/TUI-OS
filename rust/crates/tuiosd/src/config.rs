//! `tuiosd` settings, read from a TOML file (default `~/.config/tuiosd/config.toml`).

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    /// Address to listen on. Use the Tailscale address (e.g. "100.64.0.1:7420")
    /// so the daemon is reachable only over the VPN.
    pub listen: String,
    /// Shared secret every client sends in its Hello. Phase 2 replaces it with
    /// fingerprint-unlocked device keys.
    pub token: String,
    /// Program run in the session's PTY. Restarted when it exits.
    pub command: Vec<String>,
    /// Working directory for `command` (default: $HOME).
    pub cwd: Option<PathBuf>,
    /// TERM given to the program.
    pub term: String,
    /// How long to gather terminal output before sending a frame. Higher
    /// means fewer E-ink refreshes during bursts of output.
    pub coalesce_ms: u64,
    /// Lines of scrollback kept for swipe-to-scroll.
    pub scrollback: usize,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            listen: format!("0.0.0.0:{}", tuios_proto::DEFAULT_PORT),
            token: String::new(),
            command: vec![
                "tmux".into(),
                "new".into(),
                "-A".into(),
                "-s".into(),
                "main".into(),
            ],
            cwd: None,
            term: "xterm-256color".into(),
            coalesce_ms: 50,
            scrollback: 5000,
        }
    }
}

impl Config {
    pub fn default_path() -> PathBuf {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        home.join(".config/tuiosd/config.toml")
    }

    pub fn load(path: &Path) -> Result<Config> {
        let text =
            std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
        Self::parse(&text).with_context(|| format!("parsing {}", path.display()))
    }

    pub fn parse(text: &str) -> Result<Config> {
        let cfg: Config = toml::from_str(text)?;
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn validate(&self) -> Result<()> {
        if self.token.len() < 16 {
            bail!("token must be at least 16 characters (try: openssl rand -hex 24)");
        }
        if self.command.is_empty() {
            bail!("command must name a program");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_and_fills_defaults() {
        let cfg =
            Config::parse("token = \"0123456789abcdef\"\ncommand = [\"bash\", \"-l\"]\n").unwrap();
        assert_eq!(cfg.command, vec!["bash", "-l"]);
        assert_eq!(cfg.coalesce_ms, 50);
    }

    #[test]
    fn rejects_short_token_and_unknown_keys() {
        assert!(Config::parse("token = \"short\"").is_err());
        assert!(Config::parse("token = \"0123456789abcdef\"\nbogus = 1").is_err());
    }
}
