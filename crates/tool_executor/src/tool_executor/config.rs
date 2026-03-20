use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct ToolExecutorConfig {
    pub max_shell_concurrency: usize,
    pub default_timeout_ms: u64,
    pub approval_required_tools: Vec<String>,
}

impl Default for ToolExecutorConfig {
    fn default() -> Self {
        Self {
            max_shell_concurrency: 4,
            default_timeout_ms: 60_000,
            approval_required_tools: Vec::new(),
        }
    }
}
