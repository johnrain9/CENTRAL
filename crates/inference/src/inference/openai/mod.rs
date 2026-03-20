use std::sync::Arc;

use async_trait::async_trait;

use crate::inference::model::ModelInfo;
use crate::inference::provider::InferenceProvider;
use crate::inference::stream::{StreamEvent, StreamHandle};
use crate::inference::types::{InferenceError, InferenceRequest, TokenUsage};
use crate::inference::{cost::Cost, provider::InferenceClient};

pub struct OpenAiProvider;

#[async_trait]
impl InferenceProvider for OpenAiProvider {
    async fn stream(&self, request: InferenceRequest) -> Result<StreamHandle, InferenceError> {
        let (tx, handle) = StreamHandle::new(1, request.session_id.clone());
        let _ = tx.send(StreamEvent::Metadata(Default::default())).await;
        Ok(handle)
    }

    fn provider_name(&self) -> &str {
        "openai"
    }

    fn supported_models(&self) -> Vec<ModelInfo> {
        vec![ModelInfo {
            model_id: "o4-mini".into(),
            max_context_tokens: 200_000,
            supports_thinking: false,
        }]
    }

    fn estimate_cost(&self, _usage: TokenUsage) -> Cost {
        Cost::default()
    }
}

impl From<OpenAiProvider> for InferenceClient {
    fn from(provider: OpenAiProvider) -> Self {
        Self::new(vec![Arc::new(provider)])
    }
}
