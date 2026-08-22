/// Suijin desktop shell — a pure client of the gateway.
/// The app contains ZERO agent code: it connects (sidecar or remote)
/// to `suijin gateway` over HTTP/WS, authenticated by the session token.

use std::path::PathBuf;

/// Read ~/.suijin/gateway.json (the running gateway's one-click
/// advertisement: url + token). Returns null when no gateway is running.
/// This is the ONLY fs access the shell performs.
#[tauri::command]
fn read_discovery() -> Option<serde_json::Value> {
    let path = PathBuf::from(home_dir()).join(".suijin").join("gateway.json");
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}

/// Locate $HOME without pulling in a dirs crate.
fn home_dir() -> String {
    std::env::var("HOME").unwrap_or_else(|_| ".".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![read_discovery])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
