/// Suijin desktop shell — a pure client of the gateway.
/// The app contains ZERO agent code: it connects (sidecar or remote)
/// to `suijin gateway` over HTTP/WS, authenticated by the session token.

#[tauri::command]
fn connect_info() -> serde_json::Value {
    // The UI reads connection defaults from here (Tauri injects the bundle).
    serde_json::json!({
        "default_gateway": "http://127.0.0.1:7331",
        "sidecar_hint": "run: suijin gateway",
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![connect_info])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
