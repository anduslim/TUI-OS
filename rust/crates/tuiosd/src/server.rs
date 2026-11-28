//! WebSocket gateway: authenticates a client, then streams frames to it and
//! feeds its input into the shared session.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use subtle::ConstantTimeEq;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::broadcast::error::RecvError;
use tokio_tungstenite::tungstenite::Message;
use tuios_proto::{decode, encode, ClientMsg, Screen, ServerMsg, PROTO_VERSION};

use crate::session::{Session, SessionEvent};

pub async fn serve(listener: TcpListener, session: Arc<Session>, token: String) -> Result<()> {
    let token = Arc::new(token);
    loop {
        let (stream, peer) = listener.accept().await?;
        let _ = stream.set_nodelay(true);
        let session = session.clone();
        let token = token.clone();
        tokio::spawn(async move {
            match handle(stream, session, &token).await {
                Ok(()) => log::info!("{peer}: disconnected"),
                Err(e) => log::warn!("{peer}: {e:#}"),
            }
        });
    }
}

async fn handle(stream: TcpStream, session: Arc<Session>, token: &str) -> Result<()> {
    let mut ws = tokio_tungstenite::accept_async(stream)
        .await
        .context("websocket handshake")?;

    // The first message must be a valid Hello within a few seconds.
    let hello = tokio::time::timeout(Duration::from_secs(10), ws.next())
        .await
        .context("no hello")?;
    let bytes = match hello {
        Some(Ok(Message::Binary(b))) => b,
        _ => bail!("expected a binary hello"),
    };
    let (device, cols, rows) = match decode::<ClientMsg>(&bytes).context("bad hello")? {
        ClientMsg::Hello {
            proto,
            token: t,
            device,
            cols,
            rows,
        } => {
            if proto != PROTO_VERSION {
                let msg = format!("protocol {proto} not supported (server speaks {PROTO_VERSION})");
                ws.send(Message::binary(encode(&ServerMsg::Error(msg.clone()))))
                    .await
                    .ok();
                bail!(msg);
            }
            if !bool::from(t.as_bytes().ct_eq(token.as_bytes())) {
                ws.send(Message::binary(encode(&ServerMsg::Error(
                    "bad token".into(),
                ))))
                .await
                .ok();
                bail!("bad token from device {device:?}");
            }
            (device, cols, rows)
        }
        _ => bail!("first message was not a hello"),
    };
    log::info!("device {device:?} connected at {cols}x{rows}");

    // The newest client decides the size, like tmux's "latest" policy.
    session.resize(cols, rows);
    let (mut screens, mut events) = session.subscribe();
    ws.send(Message::binary(encode(&ServerMsg::Welcome {
        server: format!("tuiosd {}", env!("CARGO_PKG_VERSION")),
        session: "main".into(),
    })))
    .await?;

    let mut last: Option<Screen> = None;
    let mut next_id: u64 = 1;
    // Send the current screen straight away.
    screens.mark_changed();

    loop {
        tokio::select! {
            changed = screens.changed() => {
                if changed.is_err() {
                    break;
                }
                let screen = screens.borrow_and_update().clone();
                let frame = screen.diff(last.as_ref(), next_id);
                next_id += 1;
                last = Some((*screen).clone());
                // Screens are only published when something changed, so even a
                // frame with no lines carries a cursor move.
                ws.send(Message::binary(encode(&ServerMsg::Frame(frame)))).await?;
            }
            ev = events.recv() => {
                let msg = match ev {
                    Ok(SessionEvent::Bell) => ServerMsg::Bell,
                    Ok(SessionEvent::Exited(status)) => ServerMsg::Exited { status },
                    Err(RecvError::Lagged(_)) => continue,
                    Err(RecvError::Closed) => break,
                };
                ws.send(Message::binary(encode(&msg))).await?;
            }
            incoming = ws.next() => {
                let bytes = match incoming {
                    Some(Ok(Message::Binary(b))) => b,
                    Some(Ok(Message::Ping(p))) => { ws.send(Message::Pong(p)).await?; continue; }
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Ok(_)) => continue,
                    Some(Err(e)) => return Err(e.into()),
                };
                match decode::<ClientMsg>(&bytes) {
                    Ok(msg) => dispatch(&session, msg),
                    Err(e) => log::warn!("device {device:?}: undecodable message: {e}"),
                }
            }
        }
    }
    Ok(())
}

fn dispatch(session: &Session, msg: ClientMsg) {
    match msg {
        ClientMsg::Key { key, mods } => session.key(key, mods),
        ClientMsg::Text(t) => session.text(&t),
        ClientMsg::Paste(t) => session.paste(&t),
        ClientMsg::Resize { cols, rows } => session.resize(cols, rows),
        ClientMsg::Scroll { lines } => session.scroll(lines),
        ClientMsg::Tap { col, row } => session.tap(col, row),
        ClientMsg::Ack { .. } | ClientMsg::Hello { .. } => {}
    }
}
