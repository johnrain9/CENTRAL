use std::sync::Arc;

use async_trait::async_trait;

use crate::inference::model::ModelInfo;
use crate::inference::provider::InferenceProvider;
use crate::inference::stream::{StreamEvent, StreamHandle};
use crate::inference::types::{InferenceError, InferenceRequest, TokenUsage};
use crate::inference::{cost::Cost, provider::InferenceClient};

pub struct AnthropicProvider;

#[async_trait]
impl InferenceProvider for AnthropicProvider {
    async fn stream(&self, request: InferenceRequest) -> Result<StreamHandle, InferenceError> {
        let (tx, handle) = StreamHandle::new(1, request.session_id.clone());
        let _ = tx.send(StreamEvent::MessageStart).await;
        Ok(handle)
    }

    fn provider_name(&self) -> &str {
        "anthropic"
    }

    fn supported_models(&self) -> Vec<ModelInfo> {
        vec![ModelInfo {
            model_id: "claude-3-opus".into(),
            max_context_tokens: 200_000,
            supports_thinking: true,
        }]
    }

    fn estimate_cost(&self, _usage: TokenUsage) -> Cost {
        Cost::default()
    }
}

impl From<AnthropicProvider> for InferenceClient {
    fn from(provider: AnthropicProvider) -> Self {
        Self::new(vec![Arc::new(provider)])
    }
}
