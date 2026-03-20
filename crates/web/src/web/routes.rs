use super::handlers;
use super::state::WebState;

pub struct Router;

impl Router {
    pub async fn healthz(state: &WebState) -> String {
        handlers::healthz(state).await
    }
}
