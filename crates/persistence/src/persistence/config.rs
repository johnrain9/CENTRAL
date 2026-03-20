use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct PersistenceConfig {
    pub database_path: String,
    pub sync_mode: SyncMode,
    pub read_pool_size: u32,
    pub busy_timeout_ms: u32,
    pub cache_size_kb: u32,
    pub size_warning_threshold: u64,
    pub wal_checkpoint_interval_secs: u64,
    pub retention: RetentionConfig,
    pub audit: AuditConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub enum SyncMode {
    Normal,
    Full,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RetentionConfig {
    pub auto_retire_after_days: u32,
}

#[derive(Debug, Clone, Deserialize)]
pub struct AuditConfig {
    pub retention_days: u32,
}
