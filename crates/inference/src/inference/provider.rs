use std::sync::Arc;

use async_trait::async_trait;

use super::config::InferenceConfig;
use super::cost::{Cost, CostEvent};
use super::model::ModelInfo;
use super::stream::StreamHandle;
use super::types::{InferenceError, InferenceRequest, TokenUsage};

pub struct InferenceClient {
    providers: Vec<Arc<dyn InferenceProvider>>,
}

impl InferenceClient {
    pub fn new(providers: Vec<Arc<dyn InferenceProvider>>) -> Self {
        Self { providers }
    }

    pub async fn stream(&self, request: InferenceRequest) -> Result<StreamHandle, InferenceError> {
        let provider = self
            .providers
            .first()
            .ok_or(InferenceError::Unknown)?
            .clone();
        provider.stream(request).await
    }

    pub fn providers(&self) -> &[Arc<dyn InferenceProvider>] {
        &self.providers
    }
}

#[async_trait]
pub trait InferenceProvider: Send + Sync {
    async fn stream(&self, request: InferenceRequest) -> Result<StreamHandle, InferenceError>;
    fn provider_name(&self) -> &str;
    fn supported_models(&self) -> Vec<ModelInfo>;
    fn estimate_cost(&self, _usage: TokenUsage) -> Cost {
        Cost::default()
    }
    fn configure(&self, _config: &InferenceConfig) {}
    fn report_cost_event(&self, _event: CostEvent) {}
}
