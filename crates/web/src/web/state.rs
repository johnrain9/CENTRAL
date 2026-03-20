use std::sync::Arc;

use tokio::sync::RwLock;

#[derive(Default)]
pub struct WebState {
    pub banner: Arc<RwLock<String>>,
}
