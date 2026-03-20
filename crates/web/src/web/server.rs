use super::routes::Router;
use super::state::WebState;

pub struct WebServer {
    state: WebState,
}

impl WebServer {
    pub fn new() -> Self {
        Self {
            state: WebState::default(),
        }
    }

    pub async fn start(&self) {
        let message = Router::healthz(&self.state).await;
        tracing::info!(%message, "web server bootstrap");
    }
}
