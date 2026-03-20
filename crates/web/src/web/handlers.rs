use super::state::WebState;

pub async fn healthz(state: &WebState) -> String {
    let banner = state.banner.read().await.clone();
    format!("Ecosystem Web ready: {}", banner)
}
