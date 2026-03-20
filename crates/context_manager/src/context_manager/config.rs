use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct ContextManagerConfig {
    pub threshold_fraction: f32,
    pub recent_turns_to_keep: u32,
    pub min_recent_turns: u32,
    pub max_preserved_summaries: u32,
    pub summary_timeout_secs: u64,
    pub summary_temperature: f32,
    pub summary_max_tokens: u32,
    pub emergency_recent_turns: u32,
    pub large_text_threshold: usize,
}

impl Default for ContextManagerConfig {
    fn default() -> Self {
        Self {
            threshold_fraction: 0.80,
            recent_turns_to_keep: 10,
            min_recent_turns: 4,
            max_preserved_summaries: 30,
            summary_timeout_secs: 30,
            summary_temperature: 0.3,
            summary_max_tokens: 4096,
            emergency_recent_turns: 4,
            large_text_threshold: 5_000,
        }
    }
}
