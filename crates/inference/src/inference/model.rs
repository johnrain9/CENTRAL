#[derive(Debug, Clone, Default)]
pub struct ModelInfo {
    pub model_id: String,
    pub max_context_tokens: u32,
    pub supports_thinking: bool,
}

pub fn resolve_model(id_or_alias: &str) -> ModelInfo {
    ModelInfo {
        model_id: id_or_alias.to_string(),
        max_context_tokens: 200_000,
        supports_thinking: true,
    }
}
