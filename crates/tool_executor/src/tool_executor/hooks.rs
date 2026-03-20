#[derive(Debug, Clone)]
pub struct HookConfig {
    pub name: String,
    pub trigger: HookTrigger,
    pub command: String,
    pub matcher: Option<String>,
    pub enabled: bool,
    pub timeout_ms: Option<u64>,
    pub max_output_bytes: Option<usize>,
    pub cache: Option<HookCacheConfig>,
}

#[derive(Debug, Clone)]
pub enum HookTrigger {
    PreToolUse,
    PostToolUse,
}

#[derive(Debug, Clone)]
pub struct HookCacheConfig {
    pub ttl_secs: u64,
}

#[derive(Debug, Default)]
pub struct HookManager {
    hooks: Vec<HookConfig>,
}

impl HookManager {
    pub fn register(&mut self, hook: HookConfig) {
        self.hooks.push(hook);
    }

    pub fn hooks(&self) -> &[HookConfig] {
        &self.hooks
    }
}
