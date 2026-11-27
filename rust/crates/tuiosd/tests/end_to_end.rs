//! Starts a real session (a shell in a PTY), connects the real client core
//! over a WebSocket, types into it, and checks the client's grid.

use std::sync::mpsc;
use std::time::{Duration, Instant};

use tuios_client::{Client, ClientConfig, Event, Model};
use tuios_proto::{mods, ClientMsg, Key};
use tuiosd::config::Config;

const TOKEN: &str = "test-token-0123456789";

async fn start_server(command: &[&str]) -> String {
    let cfg = Config {
        listen: "127.0.0.1:0".into(),
        token: TOKEN.into(),
        command: command.iter().map(|s| s.to_string()).collect(),
        coalesce_ms: 10,
        ..Config::default()
    };
    let listener = tokio::net::TcpListener::bind(&cfg.listen).await.unwrap();
    let addr = listener.local_addr().unwrap();
    let session = tuiosd::session::Session::start(cfg, 40, 10).unwrap();
    tokio::spawn(tuiosd::server::serve(listener, session, TOKEN.into()));
    format!("ws://{addr}")
}

fn connect(url: String, token: &str) -> (Client, mpsc::Receiver<Event>) {
    let (tx, rx) = mpsc::channel();
    let cfg = ClientConfig {
        url,
        token: token.into(),
        device: "test".into(),
    };
    let client = Client::spawn(cfg, 40, 10, move |ev| {
        let _ = tx.send(ev);
    });
    (client, rx)
}

/// Feeds events into a model until `done` holds or the deadline passes.
fn wait_for(
    rx: &mpsc::Receiver<Event>,
    model: &mut Model,
    what: &str,
    done: impl Fn(&Model) -> bool,
) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !done(model) {
        let left = deadline.saturating_duration_since(Instant::now());
        match rx.recv_timeout(left) {
            Ok(ev) => model.handle(ev),
            Err(_) => {
                let text = model.screen.as_ref().map(|s| {
                    (0..s.rows)
                        .map(|r| s.row_text(r))
                        .collect::<Vec<_>>()
                        .join("\n")
                });
                panic!(
                    "timed out waiting for {what}; screen:\n{}",
                    text.unwrap_or_default()
                );
            }
        }
    }
}

fn screen_has(m: &Model, needle: &str) -> bool {
    m.screen
        .as_ref()
        .map(|s| (0..s.rows).any(|r| s.row_text(r).contains(needle)))
        .unwrap_or(false)
}

#[tokio::test(flavor = "multi_thread")]
async fn typing_reaches_the_shell_and_output_comes_back() {
    let url = start_server(&["sh"]).await;
    let (client, rx) = connect(url, TOKEN);
    let mut model = Model::new();
    wait_for(&rx, &mut model, "connect", |m| {
        m.banner.is_none() && m.screen.is_some()
    });

    client.send(ClientMsg::Text("echo hel''lo-$((6*7))".into()));
    client.send(ClientMsg::Key {
        key: Key::Enter,
        mods: 0,
    });
    wait_for(&rx, &mut model, "echo output", |m| {
        screen_has(m, "hello-42")
    });

    // Resizing reaches the program: stty reports the new size.
    client.send(ClientMsg::Resize { cols: 50, rows: 12 });
    client.send(ClientMsg::Text("stty size".into()));
    client.send(ClientMsg::Key {
        key: Key::Enter,
        mods: 0,
    });
    wait_for(&rx, &mut model, "new size", |m| screen_has(m, "12 50"));
    assert_eq!(
        model.screen.as_ref().map(|s| (s.cols, s.rows)),
        Some((50, 12))
    );

    // Ctrl+C interrupts a running command.
    client.send(ClientMsg::Text("sleep 30; echo not-interrupted".into()));
    client.send(ClientMsg::Key {
        key: Key::Enter,
        mods: 0,
    });
    tokio::time::sleep(Duration::from_millis(300)).await;
    client.send(ClientMsg::Key {
        key: Key::Char('c'),
        mods: mods::CTRL,
    });
    client.send(ClientMsg::Text("echo after-ctrl-c".into()));
    client.send(ClientMsg::Key {
        key: Key::Enter,
        mods: 0,
    });
    wait_for(&rx, &mut model, "ctrl-c", |m| screen_has(m, "after-ctrl-c"));
    // The command line itself shows the words; the echo's own output line must not.
    let s = model.screen.as_ref().unwrap();
    assert!(!(0..s.rows).any(|r| s.row_text(r) == "not-interrupted"));
}

#[tokio::test(flavor = "multi_thread")]
async fn wrong_token_is_refused() {
    let url = start_server(&["sh"]).await;
    let (_client, rx) = connect(url, "wrong-token-xxxxxxxx");
    let ev = rx.recv_timeout(Duration::from_secs(10)).unwrap();
    assert_eq!(ev, Event::Fatal("bad token".into()));
}

#[tokio::test(flavor = "multi_thread")]
async fn two_clients_share_one_session() {
    let url = start_server(&["sh"]).await;
    let (a, rx_a) = connect(url.clone(), TOKEN);
    let (_b, rx_b) = connect(url, TOKEN);
    let (mut ma, mut mb) = (Model::new(), Model::new());
    wait_for(&rx_a, &mut ma, "a connects", |m| m.banner.is_none());
    wait_for(&rx_b, &mut mb, "b connects", |m| m.banner.is_none());
    a.send(ClientMsg::Text("echo shared-$((1+1))\r".into()));
    wait_for(&rx_b, &mut mb, "b sees a's command", |m| {
        screen_has(m, "shared-2")
    });
}
